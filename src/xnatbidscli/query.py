import argparse
import csv
import json
import os
import re
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

from pyxnat import Interface

from .download import _enable_windows_ansi
from .login import load_credentials


COLUMNS = [
    "PROJECT",
    "SUBJECT_LABEL",
    "SUBJECT_ID",
    "SUBJECT_BIDS_RENAME",
    "EXPERIMENT_LABEL",
    "EXPERIMENT_ID",
    "EXPERIMENT_DATE",
    "EXPERIMENT_BIDS_RENAME",
    "ESTIMATED_SIZE_BYTES",
    "EXPERIMENT_TIME",
    "STUDY_DESCRIPTION",
    "STUDY_UID",
    "SCANNER_NAME",
    "SCANNER_MANUFACTURER",
    "SCANNER_MODEL",
    "SCANNER_SERIAL",
    "SOFTWARE_VERSION",
    "FIELD_STRENGTH",
    "SITE",
    "OPERATOR",
    "XNAT_GENDER",
    "DICOM_SEX",
    "HANDEDNESS",
    "AGE",
]

# Non-numeric ESTIMATED_SIZE_BYTES values, used instead of a misleading "0"
# (or a blank cell) when no usable size total was found.
SIZE_UNKNOWN = "UNKNOWN"
SIZE_FILES_WITH_UNLABELED_SIZE = "FILES_WITH_UNLABELED_SIZE"
SIZE_UNPARSEABLE_SIZE_VALUES = "UNPARSEABLE_SIZE_VALUES"

# DICOM tags read via XNAT's dicomdump service: keyword -> 8-digit hex tag.
DICOM_TAGS = {
    "StudyTime": "00080030",
    "Manufacturer": "00080070",
    "InstitutionName": "00080080",
    "StationName": "00081010",
    "StudyDescription": "00081030",
    "OperatorsName": "00081070",
    "ManufacturerModelName": "00081090",
    "PatientSex": "00100040",
    "PatientAge": "00101010",
    "MagneticFieldStrength": "00180087",
    "DeviceSerialNumber": "00181000",
    "SoftwareVersions": "00181020",
    "StudyInstanceUID": "0020000D",
}

# CSV column -> DICOM keyword used first; XNAT's value fills in if it's blank.
DICOM_PREFERRED = {
    "EXPERIMENT_TIME": "StudyTime",
    "STUDY_UID": "StudyInstanceUID",
    "SCANNER_NAME": "StationName",
    "SCANNER_MANUFACTURER": "Manufacturer",
    "SCANNER_MODEL": "ManufacturerModelName",
    "SITE": "InstitutionName",
    "OPERATOR": "OperatorsName",
}

# Columns an --accession value can match (any one keeps the experiment).
ACCESSION_COLUMNS = (
    "SUBJECT_ID", "SUBJECT_LABEL", "EXPERIMENT_ID", "EXPERIMENT_LABEL", "STUDY_UID",
)

# XNAT gender words (and --sex words) -> DICOM PatientSex codes.
XNAT_SEX_CODES = {"male": "M", "female": "F", "other": "O"}

HANDEDNESS_CHOICES = ("L", "R", "A", "U")

# Seconds between redraws of the live status line.
STATUS_INTERVAL_SECONDS = 5


class _QueryStatus:
    """Single in-place "still working" line shown while ``query`` runs.

    A background thread redraws it every ``STATUS_INTERVAL_SECONDS`` with the
    elapsed time and experiment counts. Disabled when stdout isn't a
    terminal, so redirected output only gets the final summary line.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._start = time.monotonic()
        self._project = ""
        self._drawn = False
        self.checked = 0
        self.matched = 0

    def start(self, project: str) -> None:
        """Reset the clock and counts, and begin redrawing if on a terminal."""
        self._project = project
        self._start = time.monotonic()
        self.checked = 0
        self.matched = 0
        if not sys.stdout.isatty():
            return
        _enable_windows_ansi()
        self._stop.clear()
        with self._lock:
            self._draw()
        self._thread = threading.Thread(target=self._tick, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop redrawing and erase the line."""
        if self._thread is not None:
            self._stop.set()
            self._thread.join()
            self._thread = None
        with self._lock:
            self._clear()

    def elapsed(self) -> int:
        """Whole seconds since ``start``."""
        return int(time.monotonic() - self._start)

    def warn(self, msg: str) -> None:
        """Print ``msg`` to stderr without mangling the status line."""
        with self._lock:
            was_drawn = self._drawn
            self._clear()
            print(msg, file=sys.stderr, flush=True)
            if was_drawn:
                self._draw()

    def _tick(self) -> None:
        while not self._stop.wait(STATUS_INTERVAL_SECONDS):
            with self._lock:
                self._draw()

    def _draw(self) -> None:
        """Repaint the line in place. Caller holds ``self._lock``."""
        text = (
            f"Querying {self._project}: {self.checked} experiment(s) checked, "
            f"{self.matched} matched, elapsed {self.elapsed()}s"
        )
        # Truncate so the line never wraps; "\r" only rewinds one screen row.
        width = shutil.get_terminal_size().columns - 1
        sys.stdout.write(f"\r\x1b[K{text[:width]}")
        sys.stdout.flush()
        self._drawn = True

    def _clear(self) -> None:
        """Erase the line if drawn. Caller holds ``self._lock``."""
        if self._drawn:
            sys.stdout.write("\r\x1b[K")
            sys.stdout.flush()
            self._drawn = False


_status = _QueryStatus()


# --- argparse value types -------------------------------------------------

def date_filter(value: str) -> str:
    """argparse type for ``--date``: ``MM``, ``YYYY``, ``YYYYMM`` or ``YYYYMMDD``."""
    if not value.isdigit() or len(value) not in (2, 4, 6, 8):
        raise argparse.ArgumentTypeError(
            f"invalid date '{value}': use MM, YYYY, YYYYMM or YYYYMMDD"
        )
    if len(value) == 2 and not 1 <= int(value) <= 12:
        raise argparse.ArgumentTypeError(f"invalid month '{value}'")
    return value


def time_filter(value: str) -> str:
    """argparse type for ``--time``: ``HH``, ``HHMM`` or ``HHMMSS``."""
    if not value.isdigit() or len(value) not in (2, 4, 6):
        raise argparse.ArgumentTypeError(
            f"invalid time '{value}': use HH, HHMM or HHMMSS"
        )
    return value


def handedness_filter(value: str) -> str:
    """argparse type for ``--handedness``: one of L, R, A, U (case-insensitive)."""
    upper = value.upper()
    if upper not in HANDEDNESS_CHOICES:
        raise argparse.ArgumentTypeError(
            f"invalid handedness '{value}': use L, R, A or U"
        )
    return upper


def sex_filter(value: str) -> str:
    """argparse type for ``--sex``: M/F/O or male/female/other, as a code."""
    return _sex_code(value)


# --- filters --------------------------------------------------------------

@dataclass
class Filters:
    """Query filters. Different flags AND together; values within one flag OR.

    Every list is empty when its flag was not given. An experiment whose
    field is blank never matches an active filter on that field.
    """

    accession: list[str]
    date: list[str]
    time: list[str]
    scanner: list[str]
    study: list[str]
    site: list[str]
    operator: list[str]
    sex: list[str]
    handedness: list[str]
    age: list[int]

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "Filters":
        return cls(**{
            name: list(getattr(args, name, None) or [])
            for name in cls.__dataclass_fields__
        })


def _contains_any(values: list[str], *fields: str) -> bool:
    """Case-insensitive substring match of any value against any non-blank field."""
    haystacks = [f.lower() for f in fields if f]
    return any(v.lower() in h for v in values for h in haystacks)


def _equals_any(values: list[str], *fields: str) -> bool:
    """Case-insensitive exact match of any value against any non-blank field."""
    wanted = {v.lower() for v in values}
    return any(f and f.lower() in wanted for f in fields)


def _date_matches(values: list[str], yyyymmdd: str) -> bool:
    if not yyyymmdd:
        return False
    for v in values:
        # A bare MM is the only form that isn't a prefix of YYYYMMDD.
        if (len(v) == 2 and yyyymmdd[4:6] == v) or (
            len(v) != 2 and yyyymmdd.startswith(v)
        ):
            return True
    return False


def _starts_any(values: list[str], field: str) -> bool:
    """Prefix match of any value against a non-blank field."""
    return bool(field) and any(field.startswith(v) for v in values)


def _looks_like_uid(value: str) -> bool:
    """True for a DICOM UID shape (digits and dots), unlike XNAT IDs."""
    return re.fullmatch(r"[0-9.]+", value) is not None


def _handedness_passes(f: Filters, handedness: str) -> bool:
    # XNAT stores handedness as words (left/right/ambidextrous/unknown).
    return not f.handedness or handedness[:1].upper() in f.handedness


def _experiment_passes(f: Filters, row: dict, pending_dicom: bool = False) -> bool:
    """Check the experiment-level filters against ``row``.

    With ``pending_dicom`` (before the DICOM header is read), only the
    XNAT-only fields are checked, since DICOM may still set the others.
    The exception is ``--accession``, which is checked early against XNAT's
    study UID when it has one; XNAT copies it from the same DICOM tag at
    import, so the two only differ if the record was edited.
    """
    if (
        f.accession
        and not (pending_dicom and not row.get("STUDY_UID"))
        and not _equals_any(f.accession, *(row.get(c, "") for c in ACCESSION_COLUMNS))
    ):
        return False
    checks = [
        (f.date, ["EXPERIMENT_DATE"], _date_matches),
    ]
    if not pending_dicom:
        checks += [
            (f.time, ["EXPERIMENT_TIME"], _starts_any),
            (f.scanner, ["SCANNER_NAME", "SCANNER_MANUFACTURER", "SCANNER_MODEL"],
             _contains_any),
            (f.site, ["SITE"], _contains_any),
            (f.operator, ["OPERATOR"], _contains_any),
            (f.study, ["STUDY_DESCRIPTION"], _contains_any),
            # Permissive: either source matching keeps the experiment.
            (f.sex, ["XNAT_GENDER", "DICOM_SEX"], _equals_any),
        ]
    for values, cols, matches in checks:
        if values and not matches(values, *(row.get(c, "") for c in cols)):
            return False
    return True


# --- XNAT record access ---------------------------------------------------

def _get_item(interface: Interface, uri: str) -> dict:
    """Fetch one XNAT subject/experiment record as XNAT's item JSON.

    pyxnat's ``_get_json`` forces CSV (listing endpoints only), so the raw
    ``_exec`` primitive is used and the ``{"items": [...]}`` body parsed here.
    """
    content = interface._exec(f"{uri}?format=json", "GET")
    items = json.loads(content).get("items") or []
    return items[0] if items else {}


def _field(item: dict, key: str, child: str | None = None) -> str:
    """Return a data field from an item JSON record, or ``""``.

    With ``child``, looks inside the first nested item under that child
    field (e.g. ``demographics``). A ``parent/attr`` key missing from the
    flat ``data_fields`` also falls back to the nested ``parent`` child.
    """
    if child is not None:
        for c in item.get("children") or []:
            if c.get("field") == child:
                for sub in c.get("items") or []:
                    return _field(sub, key)
        return ""

    value = (item.get("data_fields") or {}).get(key)
    if value is not None:
        return str(value).strip()
    parent, _, attr = key.partition("/")
    return _field(item, attr, child=parent) if attr else ""


def _parse_xnat_date(raw: str) -> date | None:
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _normalize_time(raw: str) -> str:
    """``HH:MM:SS[.fff]`` -> ``HHMMSS``; ``""`` if unparseable."""
    digits = re.sub(r"\D", "", raw.split(".")[0])
    return digits[:6] if len(digits) >= 4 else ""


def _dicom_header_fields(
    interface: Interface, project: str, exp_id: str, label: str
) -> dict[str, str] | None:
    """Read the ``DICOM_TAGS`` fields from one of an experiment's DICOMs.

    Uses XNAT's ``dicomdump`` service, which reads a single DICOM file from
    the session and returns its header as a ResultSet. One REST call. Only
    study-level tags are read, since one file can't speak for every series.

    Returns
    -------
    dict[str, str] | None
        Values keyed by DICOM keyword (tags absent from the header are
        omitted), or ``None`` if the service call failed (e.g. the
        experiment has no DICOM files).
    """
    src = quote(f"/archive/projects/{project}/experiments/{exp_id}", safe="/")
    try:
        content = interface._exec(
            f"/data/services/dicomdump?src={src}&format=json", "GET"
        )
        results = json.loads(content)["ResultSet"]["Result"]
    except Exception as e:
        _status.warn(f"Warning: could not read DICOM header for {label}: {e}")
        return None

    keyword_by_tag = {tag: kw for kw, tag in DICOM_TAGS.items()}
    found: dict[str, str] = {}
    for r in results:
        tag = re.sub(r"[^0-9A-Fa-f]", "", str(r.get("tag1", ""))).upper()
        kw = keyword_by_tag.get(tag)
        if kw and kw not in found:
            found[kw] = str(r.get("value") or "").strip()
    return found


def _sex_code(value: str) -> str:
    """Map ``male``/``female``/``other`` to ``M``/``F``/``O``.

    Anything else (a code already, or e.g. ``unknown``) is returned as-is.
    """
    value = value.strip()
    return XNAT_SEX_CODES.get(value.lower(), value)


def _years_between(born: date, on: date) -> int:
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


def _dicom_age_years(raw: str) -> int | None:
    """DICOM AS value (``nnnD|W|M|Y``) -> whole years."""
    m = re.fullmatch(r"(\d{1,3})([DWMY])", raw.strip().upper())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    return {"Y": n, "M": n // 12, "W": n // 52, "D": n // 365}[unit]


def _age_years(
    dicom_age: str,
    session_age: str,
    exp_date: date | None,
    dob: str,
    yob: str,
    subject_age: str,
) -> int | None:
    """Age in whole years at the experiment, from the best available source.

    Priority: DICOM PatientAge -> XNAT session ``age`` -> subject ``dob`` +
    experiment date -> subject ``yob`` + experiment year (can be one year
    high) -> subject demographics ``age``.
    """
    from_dicom = _dicom_age_years(dicom_age) if dicom_age else None
    if from_dicom is not None:
        return from_dicom
    try:
        return int(float(session_age))
    except ValueError:
        pass
    born = _parse_xnat_date(dob) if dob else None
    if born and exp_date:
        return _years_between(born, exp_date)
    if yob.isdigit() and exp_date:
        return exp_date.year - int(yob)
    try:
        return int(float(subject_age))
    except ValueError:
        return None


def _experiment_size_bytes(interface: Interface, exp_obj, label: str) -> int | str:
    """Sum file sizes (bytes) for one experiment.

    Uses XNAT's session-wide ``/files`` listing (one row per file, across
    every scan and session-level resource) rather than pyxnat's modeled
    ``experiment -> scans -> resources`` tree, so this costs exactly one
    REST call per experiment regardless of how many scans it has. pyxnat
    does not wrap this endpoint with a convenience method, so it is reached
    via the same ``interface._get_json`` primitive pyxnat's own ``Resource``
    class uses internally.

    Parameters
    ----------
    interface : Interface
        Connected pyxnat interface.
    exp_obj
        The experiment element object to sum file sizes for.
    label : str
        Human-readable identifier used in the warning printed on failure.

    Returns
    -------
    int | str
        Total size in bytes when the files' sizes sum to more than zero.
        Otherwise a categorical string:
        ``SIZE_UNPARSEABLE_SIZE_VALUES`` if any file's ``Size`` value was
        non-numeric (takes priority, since it signals a data anomaly
        rather than a merely-missing value), else
        ``SIZE_FILES_WITH_UNLABELED_SIZE`` if every file's ``Size`` field
        was missing/empty, else ``SIZE_UNKNOWN`` (the ``/files`` request
        failed, listed no files, or its sizes summed to zero).
    """
    try:
        rows = interface._get_json(f"{exp_obj._uri}/files?format=json")
    except Exception as e:
        _status.warn(f"Warning: could not determine size for {label}: {e}")
        return SIZE_UNKNOWN

    total = 0
    has_unlabeled = False
    has_unparseable = False
    for row in rows:
        raw = row.get("Size")
        if not raw:
            has_unlabeled = True
            continue
        try:
            total += int(float(raw))
        except (TypeError, ValueError):
            has_unparseable = True
            continue

    if total > 0:
        return total
    if has_unparseable:
        return SIZE_UNPARSEABLE_SIZE_VALUES
    if has_unlabeled:
        return SIZE_FILES_WITH_UNLABELED_SIZE
    return SIZE_UNKNOWN


def _collect_rows(
    interface: Interface,
    project: str,
    subject: str | None,
    filters: Filters,
) -> list[dict]:
    """Build one CSV row per experiment that passes ``filters``.

    Filters are applied cheapest-first so a dropped subject/experiment
    skips the later REST calls: subject record (1 call per subject) ->
    experiment record (1 call) -> DICOM header (1 call) -> file sizes
    (1 call). DICOM is the primary source for age and the session fields
    in ``DICOM_PREFERRED``, with XNAT as the fallback. Sex is kept per source
    (``XNAT_GENDER``, ``DICOM_SEX``).
    """
    proj_obj = interface.select.project(project)
    if not proj_obj.exists():
        sys.exit(
            f"Error: project '{project}' not found on the configured server."
        )
    canonical_project = proj_obj.id()

    rows: list[dict] = []

    if subject is None:
        subj_iter = proj_obj.subjects()
    else:
        only = proj_obj.subject(subject)
        if not only.exists():
            sys.exit(
                f"Error: subject '{subject}' not found in project "
                f"'{project}' on the configured server."
            )
        subj_iter = [only]

    for subj_obj in subj_iter:
        subj_label = subj_obj.label()
        subj_id = subj_obj.id()
        subj_item = _get_item(interface, subj_obj._uri)
        xnat_gender = _sex_code(_field(subj_item, "gender", child="demographics"))
        handedness = _field(subj_item, "handedness", child="demographics")
        if not _handedness_passes(filters, handedness):
            continue

        for exp_obj in subj_obj.experiments():
            _status.checked += 1
            exp_id = exp_obj.id()
            exp_label = exp_obj.label()
            # Skip fetching the record when no --accession value could be a
            # study UID (digits and dots only) and no ID or label matches.
            if (
                filters.accession
                and not _equals_any(
                    filters.accession, subj_id, subj_label, exp_id, exp_label
                )
                and not any(_looks_like_uid(v) for v in filters.accession)
            ):
                continue
            label = f"{subj_label}/{exp_label}"
            exp_item = _get_item(interface, exp_obj._uri)
            exp_date = _parse_xnat_date(_field(exp_item, "date"))

            row = {
                "PROJECT": canonical_project,
                "SUBJECT_LABEL": subj_label,
                "SUBJECT_ID": subj_id,
                "SUBJECT_BIDS_RENAME": "",  # left blank for manual entry
                "EXPERIMENT_LABEL": exp_label,
                "EXPERIMENT_ID": exp_id,
                "EXPERIMENT_DATE": exp_date.strftime("%Y%m%d") if exp_date else "",
                "EXPERIMENT_BIDS_RENAME": "",  # left blank for manual entry
                "EXPERIMENT_TIME": _normalize_time(_field(exp_item, "time")),
                "STUDY_UID": _field(exp_item, "UID"),
                "SCANNER_NAME": _field(exp_item, "scanner"),
                "SCANNER_MANUFACTURER": _field(exp_item, "scanner/manufacturer"),
                "SCANNER_MODEL": _field(exp_item, "scanner/model"),
                "SITE": _field(exp_item, "acquisition_site"),
                "OPERATOR": _field(exp_item, "operator"),
                "XNAT_GENDER": xnat_gender,
                "HANDEDNESS": handedness,
            }
            if not _experiment_passes(filters, row, pending_dicom=True):
                continue

            dicom = _dicom_header_fields(interface, canonical_project, exp_id, label) or {}
            for col, keyword in DICOM_PREFERRED.items():
                row[col] = dicom.get(keyword) or row[col]
            row["EXPERIMENT_TIME"] = _normalize_time(row["EXPERIMENT_TIME"])
            row["STUDY_DESCRIPTION"] = dicom.get("StudyDescription", "")
            row["SCANNER_SERIAL"] = dicom.get("DeviceSerialNumber", "")
            row["SOFTWARE_VERSION"] = dicom.get("SoftwareVersions", "")
            row["FIELD_STRENGTH"] = dicom.get("MagneticFieldStrength", "")
            row["DICOM_SEX"] = dicom.get("PatientSex", "")
            if not _experiment_passes(filters, row):
                continue

            age = _age_years(
                dicom.get("PatientAge", ""),
                _field(exp_item, "age"),
                exp_date,
                _field(subj_item, "dob", child="demographics"),
                _field(subj_item, "yob", child="demographics"),
                _field(subj_item, "age", child="demographics"),
            )
            row["AGE"] = "" if age is None else age
            if filters.age and age not in filters.age:
                continue

            row["ESTIMATED_SIZE_BYTES"] = _experiment_size_bytes(interface, exp_obj, label)
            rows.append(row)
            _status.matched += 1

    rows.sort(key=lambda row: (row["SUBJECT_LABEL"], row["EXPERIMENT_LABEL"]))

    return rows


def query_cmd(args: argparse.Namespace) -> int:
    server, username, password = load_credentials()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    interface = None
    _status.start(args.project)
    try:
        interface = Interface(server=server, user=username, password=password)
        rows = _collect_rows(
            interface, args.project, args.subject, Filters.from_args(args)
        )
    except SystemExit:
        raise
    except Exception as e:
        sys.exit(f"Error: query failed: {e}")
    finally:
        _status.stop()
        if interface is not None:
            try:
                interface.disconnect()
            except Exception:
                pass

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.subject is None:
        filename = f"PROJECT-{args.project}_{ts}.csv"
    else:
        filename = f"PROJECT-{args.project}_SUBJECT-{args.subject}_{ts}.csv"
    output_path = output_dir / filename

    # The CSV can hold PII (MRN-like labels, dates, ages), so restrict it to
    # owner only. os.open's mode is masked by umask; force 0o600 after.
    fd = os.open(str(output_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    os.chmod(output_path, 0o600)

    print(
        f"Took {_status.elapsed()} second(s) to write {len(rows)} row(s) "
        f"to {output_path}"
    )
    print(
        "INFO: The query CSV is readable only by you. To share it, update its "
        "group and permissions (e.g. chgrp, chmod g+r), but take care: it may "
        "contain PII."
    )
    return 0
