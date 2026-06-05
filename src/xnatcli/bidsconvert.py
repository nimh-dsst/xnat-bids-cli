import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from .archive import (
    OK_STATUSES as ARCHIVE_OK_STATUSES,
    archive_experiment,
    delete_experiment_dir,
)

STATUS_COMPLETE = "COMPLETE"
STATUS_FAILURE = "FAILURE"
STATUS_NONEXISTENT = "NONEXISTENT"
STATUS_EMPTY = "EMPTY"

_OK_STATUSES = {STATUS_COMPLETE, STATUS_EMPTY}
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")
_DICOM_EXTS = {".dcm", ".ima"}
_DATE_TAGS = (
    "StudyDate",
    "SeriesDate",
    "AcquisitionDate",
    "ContentDate",
    "InstanceCreationDate",
)
_NON_DIGIT = re.compile(r"\D")

# Root-level scans.tsv generation (run at the end of bidsconvert).
_SCANS_COLUMNS = [
    "filename",
    "acq_time",
    "series_number",
    "dimensions",
    "size_bytes",
    "bids_name",
    "rename",
    "recommend_for_use",
    "complete",
    "usable",
    "qc_rating",
    "rating_reason",
    "qc_notes",
    "participant_id",
    "session_id",
    "datatype",
    "task",
    "acquisition",
    "echo",
    "run",
    "suffix",
]
# Columns the generator always writes empty; user-entered text in any of them
# means the file has been reviewed and must not be regenerated over.
_SCANS_USER_COLUMNS = (
    "rename",
    "recommend_for_use",
    "complete",
    "usable",
    "qc_rating",
    "rating_reason",
    "qc_notes",
)
_SUB_RE = re.compile(r"(sub-[A-Za-z0-9]+)")
_SES_RE = re.compile(r"(ses-[A-Za-z0-9]+)")
_TASK_RE = re.compile(r"task-([A-Za-z0-9]+)")
_ACQ_RE = re.compile(r"acq-([A-Za-z0-9]+)")
_ECHO_RE = re.compile(r"echo-([A-Za-z0-9]+)")
_RUN_RE = re.compile(r"run-([A-Za-z0-9]+)")
_BIDS_NAME_RE = re.compile(r"^sub-[A-Za-z0-9]+_ses-[A-Za-z0-9]+_(.+)\.nii\.gz$")

_print_lock = threading.Lock()


def _safe_print(msg: str) -> None:
    with _print_lock:
        print(msg)


def _logging_now() -> str:
    now = datetime.now()
    return f"{now.strftime('%Y-%m-%d %H:%M:%S')},{now.microsecond // 1000:03d}"


class _LogWriter:
    def __init__(self, path: Path | None):
        self._path = path
        self._lock = threading.Lock()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", newline="") as f:
                csv.writer(f).writerow(
                    [
                        "DATESTAMP",
                        "PROJECT",
                        "SUBJECT",
                        "EXPERIMENT",
                        "STATUS",
                    ]
                )

    def write(
        self,
        datestamp: str,
        project: str,
        subject: str,
        experiment: str,
        status: str,
    ) -> None:
        if self._path is None:
            return
        with self._lock, self._path.open("a", newline="") as f:
            csv.writer(f).writerow(
                [datestamp, project, subject, experiment, status]
            )


def _extract_session_date(ds) -> str | None:
    """YYYYMMDD from the first non-empty DICOM date tag in priority order."""
    for tag in _DATE_TAGS:
        value = getattr(ds, tag, None)
        if value is None or value == "":
            continue
        digits = _NON_DIGIT.sub("", str(value))
        if len(digits) >= 8:
            return digits[:8]
    return None


def _scan_dicoms(scans_dir: Path) -> tuple[bool, str | None]:
    """Walk DICOMs under scans_dir, returning (any_readable, session_date).

    session_date is YYYYMMDD pulled from the first DICOM with a usable date
    tag, or None if no readable DICOM has one. After a DICOM parses but
    yields no date, sibling files in that directory are skipped so a series
    with empty date tags does not block dates available in other series.
    """
    import pydicom

    any_readable = False
    skip_dirs: set[Path] = set()
    for path in scans_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _DICOM_EXTS:
            continue
        if path.parent in skip_dirs:
            continue
        try:
            ds = pydicom.dcmread(str(path), stop_before_pixels=True)
        except Exception:
            continue
        any_readable = True
        date = _extract_session_date(ds)
        if date:
            return True, date
        skip_dirs.add(path.parent)
    return any_readable, None


def _convert_one(
    input_root: Path,
    project: str,
    subject: str,
    experiment: str,
    output_dir: Path,
    config_path: Path,
    dcm2bids_path: str,
) -> tuple[str, str | None]:
    exp_dir = input_root / project / subject / experiment
    if not exp_dir.is_dir():
        return STATUS_NONEXISTENT, f"session directory not found: {exp_dir}"

    scans_dir = exp_dir / "scans"
    if not scans_dir.is_dir():
        return STATUS_EMPTY, f"no 'scans/' subdirectory under {exp_dir}"

    any_readable, session_date = _scan_dicoms(scans_dir)
    if not any_readable:
        return STATUS_EMPTY, "no readable .dcm/.IMA DICOM files under scans/"

    participant = _NON_ALNUM.sub("", subject)
    session = session_date or _NON_ALNUM.sub("", experiment)
    if not participant or not session:
        return STATUS_FAILURE, (
            f"empty PARTICIPANT or SESSION after sanitizing "
            f"SUBJECT={subject!r} EXPERIMENT={experiment!r}"
        )

    bids_root = output_dir / project
    sub_dir = bids_root / f"sub-{participant}" / f"ses-{session}"
    if sub_dir.exists() and any(sub_dir.iterdir()):
        _safe_print(f"WARNING: overwriting existing {sub_dir}")

    bids_root.mkdir(parents=True, exist_ok=True)

    cmd = [
        dcm2bids_path,
        "-d", str(scans_dir),
        "-p", participant,
        "-s", session,
        "-c", str(config_path),
        "-o", str(bids_root),
        "--clobber",
        "--force_dcm2bids",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        stderr_tail = (result.stderr or "").strip().splitlines()[-1:] or [""]
        return STATUS_FAILURE, (
            f"dcm2bids exited with code {result.returncode}"
            + (f" — {stderr_tail[0]}" if stderr_tail[0] else "")
        )
    return STATUS_COMPLETE, None


def _discover_sessions(
    input_root: Path, args: argparse.Namespace
) -> list[tuple[str, str, str]]:
    if args.triplet is not None:
        p, s, e = args.triplet
        return [(p, s, e)]

    if args.subject is not None:
        project, subject = args.subject
        subject_dir = input_root / project / subject
        if not subject_dir.is_dir():
            sys.exit(f"Error: subject directory not found: {subject_dir}")
        sessions = [
            (project, subject, exp.name)
            for exp in sorted(subject_dir.iterdir())
            if exp.is_dir()
        ]
        if not sessions:
            sys.exit(f"Error: no sessions found under {subject_dir}")
        return sessions

    project = args.project
    project_dir = input_root / project
    if not project_dir.is_dir():
        sys.exit(f"Error: project directory not found: {project_dir}")
    sessions: list[tuple[str, str, str]] = []
    for subject_dir in sorted(project_dir.iterdir()):
        if not subject_dir.is_dir():
            continue
        for exp_dir in sorted(subject_dir.iterdir()):
            if exp_dir.is_dir():
                sessions.append(
                    (project, subject_dir.name, exp_dir.name)
                )
    if not sessions:
        sys.exit(f"Error: no sessions found under {project_dir}")
    return sessions


def _read_sidecar(nii_path: Path) -> dict:
    """Load the JSON sidecar that accompanies a .nii.gz, or {} if absent."""
    json_path = nii_path.with_name(nii_path.name[:-7] + ".json")
    if not json_path.is_file():
        return {}
    try:
        with json_path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _nii_dimensions(nii_path: Path, nib) -> str:
    """x-joined image shape from nibabel, padded to always include a 4th dim."""
    if nib is None:
        return ""
    try:
        shape = list(nib.load(str(nii_path)).shape)
    except Exception:
        return ""
    while len(shape) < 4:
        shape.append(1)
    return "x".join(str(int(s)) for s in shape)


def _scans_row(nii_path: Path, bids_root: Path, nib) -> list:
    basename = nii_path.name
    sidecar = _read_sidecar(nii_path)
    try:
        size_bytes: object = nii_path.stat().st_size
    except OSError:
        size_bytes = ""

    name_match = _BIDS_NAME_RE.match(basename)
    sub_match = _SUB_RE.search(basename)
    ses_match = _SES_RE.search(basename)
    task_match = _TASK_RE.search(basename)
    acq_match = _ACQ_RE.search(basename)
    echo_match = _ECHO_RE.search(basename)
    run_match = _RUN_RE.search(basename)
    stem = basename[:-7] if basename.endswith(".nii.gz") else basename

    return [
        nii_path.relative_to(bids_root).as_posix(),
        sidecar.get("AcquisitionTime", ""),
        sidecar.get("SeriesNumber", ""),
        _nii_dimensions(nii_path, nib),
        size_bytes,
        name_match.group(1) if name_match else "",
        "",  # rename — for the end-user
        "",  # recommend_for_use — for the end-user
        "",  # complete — for the end-user
        "",  # usable — for the end-user
        "",  # qc_rating — for the end-user
        "",  # rating_reason — for the end-user
        "",  # qc_notes — for the end-user
        sub_match.group(1) if sub_match else "",
        ses_match.group(1) if ses_match else "",
        nii_path.parent.name,
        f"task-{task_match.group(1)}" if task_match else "",
        f"acq-{acq_match.group(1)}" if acq_match else "",
        f"echo-{echo_match.group(1)}" if echo_match else "",
        f"run-{run_match.group(1)}" if run_match else "",
        stem.rsplit("_", 1)[-1],
    ]


def _find_scans_json() -> Path | None:
    """Locate the static scans.json data dictionary (dev tree or wheel)."""
    here = Path(__file__).resolve().parent
    for candidate in (
        here.parent / "assets" / "scans.json",  # dev: src/assets/
        here / "assets" / "scans.json",  # wheel: xnatcli/assets/
    ):
        if candidate.is_file():
            return candidate
    return None


def _read_existing_scans(
    tsv_path: Path,
) -> tuple[list[str], list[dict]] | None:
    """Parse an existing scans.tsv into (fieldnames, rows), or None if it
    cannot be read."""
    try:
        with tsv_path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            return list(reader.fieldnames or []), list(reader)
    except OSError:
        return None


def _scans_has_user_edits(fieldnames: list[str], rows: list[dict]) -> bool:
    """True if an existing scans.tsv has any text in a reviewer column.

    The reviewer columns (``_SCANS_USER_COLUMNS``) are always written empty by
    the generator, so any non-blank value there is end-user input we must not
    clobber. A header-less file is treated as having no edits.
    """
    columns = [c for c in _SCANS_USER_COLUMNS if c in fieldnames]
    if not columns:
        return False
    return any((row.get(c) or "").strip() for row in rows for c in columns)


def _scans_deviations(
    existing_rows: list[dict],
    new_rows: list[list],
    compare_columns: list[str],
) -> list[str]:
    """Human-readable deviations in non-user fields between the current
    scans.tsv and a freshly generated set of rows, keyed by ``filename``."""
    name_idx = _SCANS_COLUMNS.index("filename")
    col_idx = {c: _SCANS_COLUMNS.index(c) for c in compare_columns}
    existing_by_name = {r.get("filename", ""): r for r in existing_rows}
    new_by_name = {r[name_idx]: r for r in new_rows}

    deviations: list[str] = []
    for name in sorted(set(existing_by_name) | set(new_by_name)):
        if name not in existing_by_name:
            deviations.append(
                f"{name}: newly present on disk (absent from current scans.tsv)"
            )
            continue
        if name not in new_by_name:
            deviations.append(
                f"{name}: present in current scans.tsv but no longer found "
                "on disk"
            )
            continue
        old_row, new_row = existing_by_name[name], new_by_name[name]
        for col in compare_columns:
            new_val = new_row[col_idx[col]]
            new_val = "" if new_val is None else str(new_val)
            old_val = old_row.get(col) or ""
            if new_val != old_val:
                deviations.append(
                    f"{name}: {col} changed from {old_val!r} to {new_val!r}"
                )
    return deviations


def _report_scans_deviations(bids_root: Path, deviations: list[str]) -> None:
    """Print one WARNING per deviation to stdout and write them to a log
    file under <output>/log/."""
    project = bids_root.name
    lines = [
        f"WARNING: scans.tsv deviation [{project}] {d}" for d in deviations
    ]
    for line in lines:
        _safe_print(line)

    log_dir = bids_root.parent / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"scans_deviations_{project}_{ts}.log"
    with log_path.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    _safe_print(
        f"{len(deviations)} scans.tsv deviation(s) for {project} "
        f"logged to {log_path}"
    )


def _generate_scans_tsv(bids_root: Path) -> None:
    """Write <bids_root>/scans.tsv from every .nii.gz under the dataset.

    Walks bids_root with os.walk (skipping the dcm2bids ``tmp_dcm2bids``
    scratch directory) and emits one row per .nii.gz, then copies the static
    scans.json sidecar alongside it. When a scans.tsv is already present, every
    difference in a non-user (generator-owned) field is reported as a WARNING
    to stdout and a log file. An existing scans.tsv that already holds end-user
    reviewer entries is left untouched rather than regenerated.
    """
    if not bids_root.is_dir():
        return

    tsv_path = bids_root / "scans.tsv"

    try:
        import nibabel as nib
    except ImportError:
        nib = None
        _safe_print(
            "WARNING: nibabel not installed; the 'dimensions' column in "
            "scans.tsv will be empty. Install nibabel to populate it."
        )

    rows: list[list] = []
    for dirpath, dirnames, filenames in os.walk(bids_root):
        dirnames[:] = [d for d in dirnames if d != "tmp_dcm2bids"]
        for fname in filenames:
            if fname.endswith(".nii.gz"):
                rows.append(
                    _scans_row(Path(dirpath) / fname, bids_root, nib)
                )
    rows.sort(key=lambda r: r[0])

    existing = _read_existing_scans(tsv_path) if tsv_path.is_file() else None
    if existing is not None:
        fieldnames, existing_rows = existing
        # Non-user, non-key columns the generator owns. 'dimensions' is
        # excluded when nibabel is missing so its empty values are not
        # reported as spurious deviations.
        compare_columns = [
            c
            for c in _SCANS_COLUMNS
            if c not in _SCANS_USER_COLUMNS
            and c != "filename"
            and not (c == "dimensions" and nib is None)
        ]
        deviations = _scans_deviations(existing_rows, rows, compare_columns)
        if deviations:
            _report_scans_deviations(bids_root, deviations)

        if _scans_has_user_edits(fieldnames, existing_rows):
            _safe_print(
                f"WARNING: {tsv_path} already has reviewer entries; "
                "leaving it untouched (not regenerating)."
            )
            return

    with tsv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(_SCANS_COLUMNS)
        writer.writerows(rows)
    _safe_print(f"Wrote {tsv_path} ({len(rows)} scan(s))")

    src_json = _find_scans_json()
    if src_json is None:
        _safe_print(
            "WARNING: scans.json data dictionary not found; skipping the sidecar copy."
        )
    else:
        shutil.copyfile(src_json, bids_root / "scans.json")


def bidsconvert_cmd(args: argparse.Namespace) -> int:
    if args.nconvert < 1:
        sys.exit("Error: -n/--nconvert must be >= 1.")

    input_root = Path(args.input).resolve()
    if not input_root.is_dir():
        sys.exit(f"Error: input directory not found: {input_root}")

    output_dir = Path(args.output).resolve()

    # --maps: skip the dcm2bids conversion entirely and only (re)generate the
    # scans.tsv/scans.json tabular outputs for every project in scope from the
    # already-converted BIDS data under OUTPUT_DIR. The config, pydicom, and the
    # dcm2bids/dcm2niix tools are unused on this path, so none are required.
    if args.maps:
        if not output_dir.is_dir():
            sys.exit(
                f"Error: output directory not found: {output_dir}; run "
                "bidsconvert without -m/--maps first."
            )
        sessions = _discover_sessions(input_root, args)
        for project in sorted({p for p, _, _ in sessions}):
            _generate_scans_tsv(output_dir / project)
        return 0

    if args.config is None:
        sys.exit("Error: -c/--config is required unless -m/--maps is given.")
    config_path = Path(args.config).resolve()
    if not config_path.is_file():
        sys.exit(f"Error: config file not found: {config_path}")

    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        import pydicom  # noqa: F401
    except ImportError:
        sys.exit(
            "Error: pydicom is required for bidsconvert. "
            "Install it via 'uv sync' or 'pip install pydicom'."
        )

    dcm2bids = shutil.which("dcm2bids")
    dcm2niix = shutil.which("dcm2niix")
    missing = [
        name for name, path in (("dcm2bids", dcm2bids), ("dcm2niix", dcm2niix))
        if path is None
    ]
    if missing:
        sys.exit(
            f"Error: required tool(s) not found on PATH: {', '.join(missing)}."
        )

    sessions = _discover_sessions(input_root, args)

    log_path: Path | None = None
    if args.log:
        while True:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = output_dir / "log" / f"bidsconvert_{ts}_log.csv"
            if not log_path.exists():
                break
            time.sleep(1)
    log_writer = _LogWriter(log_path)

    counts = {
        STATUS_COMPLETE: 0,
        STATUS_FAILURE: 0,
        STATUS_NONEXISTENT: 0,
        STATUS_EMPTY: 0,
    }

    def _one(triplet: tuple[str, str, str]) -> str:
        p, s, e = triplet
        start = _logging_now()
        status, detail = _convert_one(
            input_root, p, s, e, output_dir, config_path, dcm2bids,
        )
        line = f"{p}/{s}/{e}: {status}"
        if detail:
            line += f" — {detail}"
        _safe_print(line)
        log_writer.write(start, p, s, e, status)

        archive_ok = False
        if args.archive:
            a_status, a_detail = archive_experiment(input_root, p, s, e)
            a_line = f"  archive {p}/{s}/{e}: {a_status}"
            if a_detail:
                a_line += f" — {a_detail}"
            _safe_print(a_line)
            archive_ok = a_status in ARCHIVE_OK_STATUSES

        if args.delete:
            if args.archive:
                if archive_ok:
                    delete_experiment_dir(input_root, p, s, e)
            elif status in _OK_STATUSES:
                delete_experiment_dir(input_root, p, s, e)
        return status

    if args.nconvert <= 1:
        for triplet in sessions:
            counts[_one(triplet)] += 1
    else:
        with ThreadPoolExecutor(max_workers=args.nconvert) as ex:
            futures = [ex.submit(_one, t) for t in sessions]
            for fut in as_completed(futures):
                counts[fut.result()] += 1

    total = sum(counts.values())
    print(f"\nProcessed {total} session(s):")
    for status in (
        STATUS_COMPLETE,
        STATUS_FAILURE,
        STATUS_NONEXISTENT,
        STATUS_EMPTY,
    ):
        print(f"  {status}: {counts[status]}")
    if log_path is not None:
        print(f"Log written to {log_path}")

    for project in sorted({p for p, _, _ in sessions}):
        _generate_scans_tsv(output_dir / project)

    bad = counts[STATUS_FAILURE] + counts[STATUS_NONEXISTENT]
    return 0 if bad == 0 else 1
