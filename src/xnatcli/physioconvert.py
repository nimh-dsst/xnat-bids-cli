import argparse
import csv
import re
import shutil
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

# Combined map+QC TSV written at the output (BIDS project) root, mirroring
# mriconvert's mriscans.tsv/scans.tsv for physio recordings.
_SCANS_FILENAME = "physioscans.tsv"

# Static data-dictionary sidecar copied next to the TSV on each run.
_SCANS_DICT_FILENAME = "physioscans.json"

# BIDS suffix for continuous physiological recordings.
_SUFFIX = "physio"

# File extensions phys2bids can read (acq/txt/mat/gep/smr); discovery is
# limited to these and every match is validated by actually loading it.
_SUPPORTED_EXTS = {".acq", ".txt", ".mat", ".gep", ".smr"}

# BIDS entity columns the user edits pre-conversion to drive file placement.
_EDITABLE_COLUMNS = [
    "participant_id",
    "session_id",
    "datatype",
    "task",
    "acquisition",
    "run",
]
# Post-conversion review columns the user edits by hand, mirroring mriconvert's
# scans.tsv QC block. Never regenerated once set — preserved across runs
# exactly like _EDITABLE_COLUMNS. ``rename`` only applies to a source that
# produced exactly one output file (see ``_load_rename_map`` in bidsmap.py).
_QC_EDIT_COLUMNS = [
    "rename",
    "recommend_for_use",
    "complete",
    "usable",
    "qc_rating",
    "rating_reason",
    "qc_notes",
]
# physioscans.tsv holds: the editable entities, the regenerated status/metrics
# (channel/frequency info, converted output path(s)), a regenerated bids_name
# (derived from the current output(s), comma-aligned with output_files), and
# the user-owned QC/rename block.
_SCANS_COLUMNS = (
    ["source_path", "status"]
    + _EDITABLE_COLUMNS
    + [
        "n_channels",
        "sampling_frequencies",
        "sample_count",
        "duration_seconds",
        "output_files",
        "bids_name",
    ]
    + _QC_EDIT_COLUMNS
)

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")

STATUS_CONVERTED = "CONVERTED"
STATUS_UNKNOWN_NAME = "UNKNOWN_NAME"
STATUS_DATES_DISAGREE = "DATES_DISAGREE"
STATUS_NOT_PHYSIO = "NOT_PHYSIO"
STATUS_READER_MISSING = "READER_MISSING"
STATUS_CONVERT_ERROR = "CONVERT_ERROR"
STATUS_MISSING = "MISSING"

# Three-letter month abbreviations -> month number, for Format 1 (DDMMMYY).
# Keys are capitalized (first letter upper, rest lower); the parser normalizes
# the filename's month text with str.capitalize() before looking it up.
_MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}
_YEAR_MIN, _YEAR_MAX = 1900, 2100

# A DDMMMYY date with a real 3-letter month, used to locate the date inside a
# fully concatenated (underscore-less) ``ID + date + run`` name like
# ``RPD12309Oct190003``. The alphabetic month is an unambiguous anchor.
_DDMMMYY_RE = re.compile(rf"\d{{2}}(?:{'|'.join(_MONTHS)})\d{{2}}", re.IGNORECASE)

# Subdirectory of the output BIDS project where recordings whose BIDS entities
# are unresolved are converted, named by the input basename instead.
_TMP_DIRNAME = "tmp_phys2bids"

# Optional reader packages phys2bids imports lazily per format. A missing one
# is an environment problem, not a sign the file is not physiological data.
_READER_PACKAGES = {
    ".acq": "bioread",
    ".mat": "scipy",
    ".smr": "sonpy",
}


def _logging_now() -> str:
    """Local timestamp matching the other subcommands' log ``DATESTAMP``."""
    now = datetime.now()
    return f"{now.strftime('%Y-%m-%d %H:%M:%S')},{now.microsecond // 1000:03d}"


class _LogWriter:
    """Append per-file rows to a CSV log, mirroring the other subcommands.

    A no-op when ``path`` is ``None`` (logging disabled). The header is
    ``DATESTAMP,STATUS,SOURCE_PATH,DESTINATION_PATH``; physioconvert runs serially, so
    no lock is needed. A converted file that produced several outputs emits one
    row per destination (so the one-row-per-source invariant is relaxed for
    converted files); files with no output emit a single row with a blank
    ``DESTINATION_PATH``.
    """

    def __init__(self, path: Path | None):
        self._path = path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", newline="") as f:
                csv.writer(f).writerow(
                    ["DATESTAMP", "STATUS", "SOURCE_PATH", "DESTINATION_PATH"]
                )

    def write(
        self,
        datestamp: str,
        source_path: str,
        status: str,
        destinations: list[str] | None = None,
    ) -> None:
        """Append one row per destination path (or a single blank-dest row)."""
        if self._path is None:
            return
        # One row per output; an empty/absent list still emits a single row so
        # every processed source appears in the log.
        dests = destinations or [""]
        with self._path.open("a", newline="") as f:
            writer = csv.writer(f)
            for dest in dests:
                writer.writerow([datestamp, status, source_path, dest])


def _fmt_freq(freq: float) -> str:
    """Format a sampling frequency without trailing zeros (e.g. ``1000``)."""
    return f"{freq:g}"


def _parse_filename_date(token: str) -> tuple[str | None, bool]:
    """Interpret one underscore token as an acquisition date.

    Returns ``(yyyymmdd, ambiguous)``:

    - ``(date, False)`` — parsed unambiguously. Formats handled: Format 1
      ``DDMMMYY`` (e.g. ``23May18`` -> ``20180523``, year prefixed with ``20``);
      Format 2 ``YYYYMMDD`` (used as-is); Format 3 ``MMDDYYYY`` / Format 4
      ``DDMMYYYY`` (year last), disambiguated when exactly one of the two
      leading pairs exceeds 12.
    - ``(None, True)`` — it is a year-last 8-digit date but both leading pairs
      are <= 12, so month/day order is ambiguous (caller falls back to the
      file's date but still treats the token as the date).
    - ``(None, False)`` — the token is not a date at all.
    """
    # Format 1: DDMMMYY (alphabetic month).
    m = re.fullmatch(r"(\d{2})([A-Za-z]{3})(\d{2})", token)
    if m:
        day, month, year = (
            int(m.group(1)),
            _MONTHS.get(m.group(2).capitalize()),
            m.group(3),
        )
        if month and 1 <= day <= 31:
            return f"20{year}{month:02d}{day:02d}", False
        return None, False

    if not re.fullmatch(r"\d{8}", token):
        return None, False

    # Format 2: YYYYMMDD (year first).
    year, month, day = int(token[:4]), int(token[4:6]), int(token[6:8])
    if _YEAR_MIN <= year <= _YEAR_MAX and 1 <= month <= 12 and 1 <= day <= 31:
        return token, False

    # Format 3 (MMDDYYYY) / Format 4 (DDMMYYYY): year last.
    pair1, pair2, year = int(token[:2]), int(token[2:4]), int(token[4:8])
    if _YEAR_MIN <= year <= _YEAR_MAX:
        if pair1 > 12 and 1 <= pair2 <= 12 and pair1 <= 31:  # DDMMYYYY
            return f"{year}{pair2:02d}{pair1:02d}", False
        if pair2 > 12 and 1 <= pair1 <= 12 and pair2 <= 31:  # MMDDYYYY
            return f"{year}{pair1:02d}{pair2:02d}", False
        if 1 <= pair1 <= 12 and 1 <= pair2 <= 12:  # both valid -> ambiguous
            return None, True
    return None, False


def _match_iso(text: str) -> tuple[str, int] | None:
    """Match a leading ``YYYY-MM-DDTHH_MM_SS`` datetime in ``text``.

    ``text`` is the underscore-joined remainder after the participant token.
    Returns ``(yyyymmdd, n_tokens)`` where ``n_tokens`` is how many
    underscore-separated tokens the datetime spans (3, since ``HH_MM_SS`` adds
    two underscores), or ``None`` if no such datetime starts the string.
    """
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})T\d{2}_\d{2}_\d{2}", text)
    if not m:
        return None
    year, month, day = m.group(1), int(m.group(2)), int(m.group(3))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return f"{year}{month:02d}{day:02d}", m.group(0).count("_") + 1


def _reference_date(path: Path) -> str:
    """The file's last-modified date as ``YYYYMMDD`` (local time)."""
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y%m%d")


def _split_glued(token: str) -> list[str]:
    """Split a date glued to a trailing 4-digit run into ``[date, run]`` tokens.

    Some filenames drop the underscore between the acquisition date and the run
    number, e.g. ``050620260000`` (date ``05062026`` + run ``0000``) or
    ``23May180000`` (``23May18`` + ``0000``). Returns the two split tokens when
    the token is such a glued date+run, otherwise the original token unchanged.
    """
    if re.fullmatch(r"\d{12}", token):
        date, ambiguous = _parse_filename_date(token[:8])
        if date is not None or ambiguous:
            return [token[:8], token[8:]]
    m = re.fullmatch(r"(\d{2}[A-Za-z]{3}\d{2})(\d{4})", token)
    if m and _parse_filename_date(m.group(1))[0] is not None:
        return [m.group(1), m.group(2)]
    return [token]


def _derive_entities(file_path: Path) -> tuple[dict[str, str], dict]:
    """Derive BIDS entities from a physio file's underscore-delimited name.

    The basename (without extension) is split on underscores: the first token
    is the participant label (written as ``sub-<token>``). A fully concatenated
    name with no underscore between the ID, date, and run (e.g.
    ``RPD12309Oct190003``) is split on its embedded ``DDMMMYY`` date first — the
    3-letter month is an unambiguous anchor — into ID + date + run. Any token
    that glues a date to a trailing run (e.g. ``050620260000``) is then split
    (see ``_split_glued``). The acquisition date for ``session_id`` is located
    among the remaining tokens — it need not sit right after the participant —
    via ``_parse_filename_date`` (or the ISO fallback). A trailing 4-digit token
    becomes a zero-padded 2-digit ``run-`` entity, and anything left over goes
    into ``acquisition``. ``datatype`` defaults to ``func`` (it is not encoded
    in the name) and stays overridable in the map.

    ``session_id`` always falls back to the file's last-modified date when the
    name has no usable date, or when a parsed date is ambiguous. Returns
    ``(entities, date_info)`` where ``date_info`` carries the parsed filename
    date, the reference (last-modified) date, and whether they disagree.
    """
    tokens = file_path.stem.split("_")
    # Split a fully concatenated first token (no underscores) on an embedded
    # DDMMMYY date: text before it is the ID, text after it is the run.
    embedded = _DDMMMYY_RE.search(tokens[0]) if tokens else None
    if embedded is not None and _parse_filename_date(embedded.group(0))[0] is not None:
        before, after = tokens[0][: embedded.start()], tokens[0][embedded.end() :]
        tokens = [before, embedded.group(0)] + ([after] if after else []) + tokens[1:]
    participant = _NON_ALNUM.sub("", tokens[0]) if tokens else ""
    entities = {
        "participant_id": f"sub-{participant}" if participant else "",
        "session_id": "",
        "datatype": "func",
        "task": "",
        "acquisition": "",
        "run": "",
    }

    ref_date = _reference_date(file_path)
    # Split glued date+run tokens, then find the date anywhere in what remains
    # (it may follow an acquisition label), so neither the date nor the run is
    # left to pollute acquisition.
    rest = [t for tok in tokens[1:] for t in _split_glued(tok)]
    filename_date: str | None = None

    date_index: int | None = None
    for i, tok in enumerate(rest):
        date, ambiguous = _parse_filename_date(tok)
        if date is not None:
            filename_date, date_index = date, i
            break
        if ambiguous:
            # The token is the date but its order is unreadable: consume it and
            # fall back to the file's date below.
            date_index = i
            break
    if date_index is not None:
        rest = rest[:date_index] + rest[date_index + 1 :]
    else:
        iso = _match_iso("_".join(rest))  # least-likely format, checked last
        if iso is not None:
            filename_date, n_tokens = iso
            rest = rest[n_tokens:]

    disagrees = False
    if filename_date is not None and filename_date == ref_date:
        session = filename_date
    else:
        # No date, ambiguous order, or a parsed date that conflicts with the
        # file's last-modified date — in every uncertain case the file date wins.
        session = ref_date
        disagrees = filename_date is not None
    entities["session_id"] = f"ses-{session}"

    # Trailing 4-digit token is the run number; the rest is the acquisition.
    if rest and re.fullmatch(r"\d{4}", rest[-1]):
        entities["run"] = f"run-{int(rest[-1]):02d}"
        rest = rest[:-1]
    acq_label = _NON_ALNUM.sub("", "".join(rest))
    if acq_label:
        entities["acquisition"] = f"acq-{acq_label}"

    date_info = {
        "filename_date": filename_date,
        "reference_date": ref_date,
        "disagrees": disagrees,
    }
    return entities, date_info


def _blocked_reason(entities: dict[str, str]) -> str | None:
    """Why an entity set cannot yet be written to BIDS, or ``None`` if it can.

    Only ``participant_id`` is mandatory: a file is staged under tmp_phys2bids
    (status ``UNKNOWN_NAME``) only when it is blank. ``session_id`` defaults to
    the file's last-modified date and ``datatype`` defaults to ``func``, so
    those — along with the optional ``task``/``acquisition``/``run`` —
    never block conversion.
    """
    if not entities["participant_id"]:
        return "participant_id is blank"
    return None


def _bids_basename(entities: dict[str, str], recording: str | None) -> str:
    """Assemble a BIDS filename stem (without extension) for one recording.

    Entities are ordered per the BIDS spec: sub, ses, task, acq, run,
    recording. Blank optional entities are omitted.
    """
    parts = [entities["participant_id"]]
    if entities["session_id"]:
        parts.append(entities["session_id"])
    if entities["task"]:
        parts.append(entities["task"])
    if entities["acquisition"]:
        parts.append(entities["acquisition"])
    if entities["run"]:
        parts.append(entities["run"])
    if recording:
        parts.append(f"recording-{recording}")
    return "_".join(parts) + f"_{_SUFFIX}"


def _output_dir(output_dir: Path, entities: dict[str, str]) -> Path:
    """Directory the recording is written to: ``sub-X/[ses-Y/]<datatype>/``.

    Only called for resolved files (``participant_id`` non-blank). ``datatype``
    falls back to ``func`` if the user blanked it in the map.
    """
    out = output_dir / entities["participant_id"]
    if entities["session_id"]:
        out = out / entities["session_id"]
    return out / (entities["datatype"] or "func")


def _load_blueprint(path: Path):
    """Load a physio file with the phys2bids loader for its extension.

    Returns ``(blueprint, error, reader_missing)``: a phys2bids
    ``BlueprintInput`` with ``(None, False)`` on success, or ``None`` with a
    message. ``reader_missing`` is True when the failure is an ``ImportError``
    for the optional reader phys2bids needs for this format (e.g. ``bioread``
    for ``.acq``) — an environment problem, not a sign the file is not physio.
    """
    from phys2bids import io as p2b_io

    loaders = {
        ".acq": p2b_io.load_acq,
        ".txt": p2b_io.load_txt,
        ".mat": p2b_io.load_mat,
        ".gep": p2b_io.load_gep,
        ".smr": p2b_io.load_smr,
    }
    ext = path.suffix.lower()
    loader = loaders.get(ext)
    if loader is None:
        return None, f"unsupported extension {path.suffix}", False
    try:
        blueprint = loader(str(path))
    except ImportError as exc:
        package = _READER_PACKAGES.get(ext, "the required reader")
        return (
            None,
            f"reader package not installed ({exc}); install {package} to read "
            f"{ext} files",
            True,
        )
    except Exception as exc:  # noqa: BLE001 - any other load failure = not physio
        return None, str(exc), False
    return blueprint, None, False


def _blueprint_info(blueprint) -> tuple[str, str, str, str, bool]:
    """Channel count, per-frequency metrics, and an is-physio flag.

    phys2bids stores one 1-D timeseries per channel (the time channel first),
    each tagged with its own sampling frequency, so channels may differ in both
    frequency and length. The sampling frequencies, sample counts, and durations
    are returned as comma-separated lists aligned by unique frequency (ascending):
    each entry's ``sample_count`` is the longest channel recorded at that
    frequency and its ``duration_seconds`` is ``sample_count / frequency`` in
    seconds at 0.001 s precision. A successfully loaded file is treated as
    physiological when it has at least one channel and a positive sampling
    frequency.

    Returns ``(n_channels, sampling_frequencies, sample_count, duration_seconds,
    is_physio)`` with the three metric strings sharing the same order/length.
    """
    # Pair each channel's frequency with the longest sample count seen at it.
    # Channels sharing a frequency should share a length; keep the max if not.
    counts_by_freq: dict[float, int] = {}
    timeseries = getattr(blueprint, "timeseries", None) or []
    freq_list = getattr(blueprint, "freq", None) or []
    for series, freq in zip(timeseries, freq_list):
        try:
            f, n = float(freq), int(len(series))
        except Exception:  # noqa: BLE001
            continue
        counts_by_freq[f] = max(counts_by_freq.get(f, 0), n)

    if counts_by_freq:
        freqs = sorted(counts_by_freq)
        sample_count = ",".join(str(counts_by_freq[f]) for f in freqs)
        duration_seconds = ",".join(
            f"{counts_by_freq[f] / f:.3f}" if f > 0 else "" for f in freqs
        )
    else:
        # No timeseries lengths available; still report the raw frequencies so a
        # loaded-but-empty blueprint is recognized as physio where appropriate.
        try:
            freqs = sorted({float(f) for f in freq_list})
        except Exception:  # noqa: BLE001
            freqs = []
        sample_count = ""
        duration_seconds = ""

    try:
        n_channels = int(blueprint.ch_amount)
    except Exception:  # noqa: BLE001
        n_channels = len(getattr(blueprint, "ch_name", []) or [])
    sampling_frequencies = ",".join(_fmt_freq(f) for f in freqs)
    is_physio = n_channels >= 1 and any(f > 0 for f in freqs)
    return (
        str(n_channels),
        sampling_frequencies,
        sample_count,
        duration_seconds,
        is_physio,
    )


def _recording_label(stem: str, base: str, index: int) -> str:
    """Recording label for one of several per-frequency phys2bids outputs.

    phys2bids names multi-frequency outputs ``<base>_<freq>Hz``; the trailing
    ``<freq>Hz`` (sanitized to alphanumerics) becomes the ``recording-`` label,
    falling back to ``rec<N>`` if the suffix cannot be recovered.
    """
    if stem.startswith(base + "_"):
        label = _NON_ALNUM.sub("", stem[len(base) + 1 :])
        if label:
            return label
    return f"rec{index + 1}"


def _unique_stem(dest_dir: Path, stem: str) -> str:
    """A ``<stem>.tsv.gz`` name free in ``dest_dir``, suffixing ``_N`` if taken.

    Used for the flat ``tmp_phys2bids`` layout so two inputs sharing a basename
    (e.g. several sessions each holding ``data.acq``) never overwrite.
    """
    candidate = stem
    suffix = 1
    while (dest_dir / f"{candidate}.tsv.gz").exists():
        candidate = f"{stem}_{suffix}"
        suffix += 1
    return candidate


def _place(tsv_src: Path, dest_dir: Path, basename: str, output_dir: Path) -> str:
    """Move a phys2bids ``.tsv.gz``/``.json`` pair to ``dest_dir/<basename>``.

    ``basename`` is expected to be collision-free already (callers reserve it
    via ``_unique_bids_basename`` or ``_unique_stem``), so nothing is
    overwritten. Returns the destination ``.tsv.gz`` path relative to
    ``output_dir`` (POSIX), which records the converted basename in the map.
    """
    src_stem = tsv_src.name[: -len(".tsv.gz")]
    tsv_dst = dest_dir / f"{basename}.tsv.gz"
    shutil.move(str(tsv_src), str(tsv_dst))

    json_src = tsv_src.with_name(src_stem + ".json")
    if json_src.is_file():
        shutil.move(str(json_src), str(dest_dir / f"{basename}.json"))
    else:
        print(f"WARNING: no JSON sidecar produced for {tsv_src.name}")
    return tsv_dst.relative_to(output_dir).as_posix()


def _unique_bids_basename(
    dest_dir: Path, entities: dict[str, str], recording: str | None
) -> str:
    """A BIDS basename free in ``dest_dir``, adding/incrementing ``run-`` on need.

    A run number parsed from the filename is trusted first. Only when the
    resulting name already exists is a zero-padded 2-digit ``run-`` entity added
    (or the parsed one bumped) until a free name is found, so an existing file
    is never overwritten.
    """
    basename = _bids_basename(entities, recording)
    if not (dest_dir / f"{basename}.tsv.gz").exists():
        return basename

    run_n = 0
    if entities["run"]:
        m = re.search(r"(\d+)$", entities["run"])
        if m:
            run_n = int(m.group(1)) + 1
    while True:
        candidate = _bids_basename(
            {**entities, "run": f"run-{run_n:02d}"}, recording
        )
        if not (dest_dir / f"{candidate}.tsv.gz").exists():
            return candidate
        run_n += 1


def _delete_prior_outputs(prior_outputs: str, output_dir: Path) -> None:
    """Delete every output file a source produced on a previous run.

    Called once a re-conversion is known to have produced fresh output, so
    re-runs are idempotent: the source's own prior outputs (tmp_phys2bids copy
    or proper-path files) are removed rather than lingering or being mistaken
    for another file's collision.
    """
    for rel_out in (s.strip() for s in prior_outputs.split(",")):
        if not rel_out:
            continue
        tsv = output_dir / rel_out
        for victim in (tsv, tsv.with_name(tsv.name[: -len(".tsv.gz")] + ".json")):
            try:
                victim.unlink()
            except (FileNotFoundError, OSError):
                pass


def _run_phys2bids_to_staging(file_path: Path) -> tuple[str | None, str | None]:
    """Run the phys2bids workflow for one file into a fresh staging directory.

    This is the slow part of conversion — running phys2bids — isolated so it can
    be parallelized across processes. The placement of its output into the BIDS
    tree happens later, serially, in the main process (see ``_place_staged``).
    Returns ``(staging_dir, None)`` on success, or ``(None, error)``; the
    staging directory is left for the caller to consume and remove.
    """
    from phys2bids.phys2bids import phys2bids as run_phys2bids

    staging = Path(tempfile.mkdtemp(prefix="xnatcli_phys2bids_"))
    try:
        run_phys2bids(
            filename=file_path.name,
            indir=str(file_path.parent),
            outdir=str(staging),
            quiet=True,
        )
    except Exception as exc:  # noqa: BLE001 - surface phys2bids failures
        shutil.rmtree(staging, ignore_errors=True)
        return None, f"phys2bids failed: {exc}"
    if not list(staging.glob("*.tsv.gz")):
        shutil.rmtree(staging, ignore_errors=True)
        return None, "phys2bids produced no .tsv.gz output"
    return str(staging), None


def _run_worker(task: tuple[str, str, bool]) -> dict:
    """Validate and convert one file; the unit of work for parallel execution.

    Runs in a worker process (or the main process when serial): loads the file
    to confirm it is physiological data and gather channel info, then — if it
    is — runs phys2bids into a staging directory. Returns a picklable dict; all
    BIDS naming and file placement is done later in the main process so the
    no-overwrite/run-number logic stays serial and race-free.

    When ``maps`` (the third task element) is True, the phys2bids conversion is
    skipped: the file is still loaded to recompute its metrics, but no staging
    directory is produced (placement and prior-output paths are handled in the
    main process from the existing physioscans.tsv).
    """
    input_root_str, path_str, maps = task
    path = Path(path_str)
    rel = path.relative_to(Path(input_root_str)).as_posix()
    result = {
        "rel": rel,
        "start": _logging_now(),
        "is_physio": False,
        "reader_missing": False,
        "err": None,
        "n_ch": "",
        "freqs": "",
        "sample_count": "",
        "duration_seconds": "",
        "staging": None,
        "convert_error": None,
    }

    blueprint, err, reader_missing = _load_blueprint(path)
    if blueprint is not None:
        (
            result["n_ch"],
            result["freqs"],
            result["sample_count"],
            result["duration_seconds"],
            result["is_physio"],
        ) = _blueprint_info(blueprint)
    if not result["is_physio"]:
        result["reader_missing"] = reader_missing
        result["err"] = err
        return result

    # On the maps path the slow phys2bids conversion is skipped; metrics above
    # are enough to (re)build the tabular outputs in the main process.
    if not maps:
        result["staging"], result["convert_error"] = _run_phys2bids_to_staging(path)
    return result


def _place_staged(
    staging: Path,
    output_dir: Path,
    entities: dict[str, str],
    name_known: bool,
    prior_outputs: str,
    base_stem: str,
) -> tuple[str, list[str], str | None]:
    """Move a completed conversion's staged output into the BIDS tree.

    Runs serially in the main process. When ``name_known`` is True each
    ``.tsv.gz``/``.json`` pair is moved into ``sub-X/[ses-Y/]<datatype>/``
    renamed to the BIDS basename from ``entities`` — adding a ``run-`` entity to
    avoid overwriting an existing file (multi-frequency outputs also gain a
    ``recording-`` entity). When False the name is unresolved, so each pair is
    moved into ``<output>/tmp_phys2bids/`` keeping the input basename, suffixed
    ``_N`` on collision (status ``UNKNOWN_NAME``). The source's ``prior_outputs``
    are deleted first so re-runs are idempotent. The staging directory is
    removed when done. Returns ``(status, written_relpaths, detail)``.
    """
    try:
        produced = sorted(staging.glob("*.tsv.gz"))
        if not produced:
            return STATUS_CONVERT_ERROR, [], "phys2bids produced no .tsv.gz output"

        # Clear this source's previous outputs so re-runs are idempotent and its
        # own names don't look like collisions below.
        _delete_prior_outputs(prior_outputs, output_dir)

        multi = len(produced) > 1
        written: list[str] = []

        if name_known:
            dest_dir = _output_dir(output_dir, entities)
            dest_dir.mkdir(parents=True, exist_ok=True)
            for index, tsv_src in enumerate(produced):
                stem = tsv_src.name[: -len(".tsv.gz")]
                recording = _recording_label(stem, base_stem, index) if multi else None
                basename = _unique_bids_basename(dest_dir, entities, recording)
                written.append(_place(tsv_src, dest_dir, basename, output_dir))
            return STATUS_CONVERTED, written, None

        # BIDS name unresolved: stage under tmp_phys2bids by input basename.
        dest_dir = output_dir / _TMP_DIRNAME
        dest_dir.mkdir(parents=True, exist_ok=True)
        for tsv_src in produced:
            stem = tsv_src.name[: -len(".tsv.gz")]
            basename = _unique_stem(dest_dir, stem)
            written.append(_place(tsv_src, dest_dir, basename, output_dir))
        return STATUS_UNKNOWN_NAME, written, None
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _recording_of(rel_out: str, base_stem: str, index: int) -> str:
    """Recover the ``recording-`` label of a prior multi-frequency output.

    Used when relocating already-converted files in maps mode so each
    per-frequency output keeps the same ``recording-<freq>Hz`` label it was first
    given. Proper-BIDS outputs embed it as ``recording-<label>``; tmp_phys2bids
    outputs keep phys2bids' ``<base>_<freq>Hz`` stem. Falls back to ``rec<N>`` if
    neither form is recognizable.
    """
    stem = Path(rel_out).name
    if stem.endswith(".tsv.gz"):
        stem = stem[: -len(".tsv.gz")]
    m = re.search(rf"_recording-([A-Za-z0-9]+)_{_SUFFIX}$", stem)
    if m:
        return m.group(1)
    if stem.endswith(f"_{_SUFFIX}"):
        stem = stem[: -(len(_SUFFIX) + 1)]
    return _recording_label(stem, base_stem, index)


def _prune_empty_dirs(start: Path, stop: Path) -> None:
    """Remove ``start`` and any empty parents up to (not including) ``stop``.

    Called after a relocation so a now-emptied ``sub-X/ses-Y/<datatype>/`` (or
    ``tmp_phys2bids/``) directory does not linger once its files move elsewhere.
    """
    current = start
    while current != stop and stop in current.parents:
        try:
            current.rmdir()  # succeeds only if the directory is empty
        except OSError:
            break
        current = current.parent


def _relocate_existing_outputs(
    prior_outputs: list[str],
    output_dir: Path,
    entities: dict[str, str],
    name_known: bool,
    base_stem: str,
) -> tuple[str, list[str], str | None]:
    """Move already-converted outputs to match edited physioscans.tsv entities.

    Maps-mode counterpart to ``_place_staged``: instead of re-running phys2bids,
    it relocates the ``.tsv.gz``/``.json`` pair(s) a source produced on an
    earlier run to the BIDS path implied by the (possibly user-edited) entities,
    preserving each multi-frequency output's ``recording-`` label. A file already
    correctly placed is left untouched; a target occupied by a *different* file is
    skipped with a WARNING — nothing is ever overwritten. Emptied source
    directories (including ``tmp_phys2bids/``) are pruned afterward. Returns
    ``(status, new_relpaths, detail)``; entries whose source file is gone keep
    their recorded path.

    When ``name_known`` is False (``participant_id`` blank) the outputs belong in
    ``tmp_phys2bids/`` under their input basename — where they already are — so no
    move happens and the status is ``UNKNOWN_NAME``.
    """
    multi = len(prior_outputs) > 1
    new_rels: list[str] = []
    old_parents: set[Path] = set()
    moved = 0

    for index, rel_out in enumerate(prior_outputs):
        src_tsv = output_dir / rel_out
        if not src_tsv.is_file():
            new_rels.append(rel_out)  # source no longer on disk; keep its path
            continue

        if name_known:
            recording = _recording_of(rel_out, base_stem, index) if multi else None
            basename = _bids_basename(entities, recording)
            dest_tsv = _output_dir(output_dir, entities) / f"{basename}.tsv.gz"
        else:
            # Unresolved name: tmp_phys2bids under the input basename (already so).
            stem = src_tsv.name[: -len(".tsv.gz")]
            dest_tsv = output_dir / _TMP_DIRNAME / f"{stem}.tsv.gz"

        if dest_tsv.resolve() == src_tsv.resolve():
            new_rels.append(rel_out)  # already satisfies the map
            continue
        if dest_tsv.exists():
            print(
                f"WARNING: cannot move {rel_out} -> "
                f"{dest_tsv.relative_to(output_dir).as_posix()}: target already "
                "exists; skipping (resolve the conflict and re-run)."
            )
            new_rels.append(rel_out)
            continue

        dest_tsv.parent.mkdir(parents=True, exist_ok=True)
        src_json = src_tsv.with_name(src_tsv.name[: -len(".tsv.gz")] + ".json")
        dest_json = dest_tsv.with_name(dest_tsv.name[: -len(".tsv.gz")] + ".json")
        shutil.move(str(src_tsv), str(dest_tsv))
        if src_json.is_file():
            shutil.move(str(src_json), str(dest_json))
        else:
            print(f"WARNING: no JSON sidecar found for {rel_out}")
        old_parents.add(src_tsv.parent)
        new_rels.append(dest_tsv.relative_to(output_dir).as_posix())
        moved += 1

    for parent in old_parents:
        _prune_empty_dirs(parent, output_dir)

    status = STATUS_CONVERTED if name_known else STATUS_UNKNOWN_NAME
    detail = f"relocated {moved} file(s) to match map" if moved else None
    return status, new_rels, detail


def _read_existing_scans(scans_path: Path) -> dict[str, dict[str, str]]:
    """Load an existing physioscans.tsv as full rows keyed by ``source_path``.

    The whole row is kept so editable entity columns and the user-owned
    QC/rename columns (whose edits, including intentional blanks, must be
    preserved) are available when merging, and so an already-converted file's
    regenerated metrics can be preserved verbatim when its conversion is
    skipped. Empty when the file is absent.
    """
    if not scans_path.is_file():
        return {}
    existing: dict[str, dict[str, str]] = {}
    with scans_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            src = row.get("source_path")
            if src:
                existing[src] = {
                    k: (v if v is not None else "") for k, v in row.items()
                }
    return existing


def _has_existing_outputs(
    rel: str,
    existing: dict[str, dict[str, str]],
    existing_outputs: dict[str, str],
    output_dir: Path,
) -> bool:
    """Whether ``rel`` was already converted and its outputs are still on disk.

    True when the prior status indicates it was converted (``CONVERTED``,
    ``DATES_DISAGREE``, or ``UNKNOWN_NAME`` — the tmp_phys2bids staging) *and*
    every output path recorded for it in physioscans.tsv still exists on disk. Such
    a file is never re-converted: its outputs are instead relocated to match the
    (possibly edited) map. A file with no recorded outputs, a missing output, or
    any other status (``CONVERT_ERROR``, ``NOT_PHYSIO``, …) is (re-)converted.
    """
    prior = existing.get(rel)
    if prior is None or prior.get("status") not in (
        STATUS_CONVERTED,
        STATUS_DATES_DISAGREE,
        STATUS_UNKNOWN_NAME,
    ):
        return False
    rel_outs = [s.strip() for s in existing_outputs.get(rel, "").split(",") if s.strip()]
    if not rel_outs:
        return False
    return all((output_dir / out).is_file() for out in rel_outs)


def _write_tsv(
    path: Path,
    rows: list[dict[str, str]],
    columns: list[str],
) -> None:
    """Write a TSV with the given columns, one row per file, sorted by ``source_path``."""
    rows = sorted(rows, key=lambda r: r["source_path"])
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _resolve_entities(
    rel_path: str, file_path: Path, existing: dict[str, dict[str, str]]
) -> tuple[dict[str, str], dict]:
    """Entity values for a file: user-edited if present, else derived.

    Editable values from an existing physioscans.tsv take precedence (including
    intentional blanks); any column missing from an older file falls back to a
    fresh derivation. Returns ``(entities, date_info)``; ``date_info`` gains an
    ``overridden`` flag that is True when the user has changed ``session_id``
    away from the derived value (so a date disagreement is considered resolved).
    """
    derived, date_info = _derive_entities(file_path)
    prior = existing.get(rel_path)
    if prior is None:
        entities = dict(derived)
    else:
        entities = {
            col: prior[col] if col in prior else derived[col]
            for col in _EDITABLE_COLUMNS
        }
    date_info["overridden"] = entities["session_id"] != derived["session_id"]
    return entities, date_info


def _resolve_qc_edits(rel_path: str, existing: dict[str, dict[str, str]]) -> dict[str, str]:
    """Carry forward the user-owned QC/rename columns from a prior row.

    Unlike the entity columns, these are never derived — purely preserved
    verbatim (blank if there is no prior row), exactly like mriconvert's
    scans.tsv QC block.
    """
    prior = existing.get(rel_path, {})
    return {col: prior.get(col, "") for col in _QC_EDIT_COLUMNS}


def _preserve_scans_row(
    rel_path: str, prior: dict[str, str], status: str
) -> dict[str, str]:
    """Rebuild a row from preserved editable/QC values plus a status, with
    blank metrics.

    Used for files that were in a prior physioscans.tsv but are now missing or
    no longer readable as physio, so the user's manual edits are not lost.
    """
    row: dict[str, str] = {"source_path": rel_path, "status": status}
    for col in _EDITABLE_COLUMNS + _QC_EDIT_COLUMNS:
        row[col] = prior.get(col, "")
    for col in (
        "n_channels", "sampling_frequencies", "sample_count",
        "duration_seconds", "output_files", "bids_name",
    ):
        row[col] = ""
    return row


def _converted_result(rel: str, prior_row: dict[str, str]) -> dict:
    """Build a ``_run_worker``-shaped result for an already-converted file.

    Lets an already-converted file be fed through ``_finish`` (in relocate mode)
    without re-running phys2bids or re-reading the file: the channel/frequency
    metrics are carried over verbatim from its prior physioscans.tsv row, and only
    the output paths are refreshed by the relocation. Marked ``is_physio`` with no
    staging so ``_finish`` takes the relocate-existing-outputs branch.
    """
    return {
        "rel": rel,
        "start": _logging_now(),
        "is_physio": True,
        "reader_missing": False,
        "err": None,
        "n_ch": prior_row.get("n_channels", ""),
        "freqs": prior_row.get("sampling_frequencies", ""),
        "sample_count": prior_row.get("sample_count", ""),
        "duration_seconds": prior_row.get("duration_seconds", ""),
        "staging": None,
        "convert_error": None,
    }


def _bids_name_of(output_file: str, entities: dict[str, str]) -> str:
    """The portion of an output file's basename after its sub[_ses]_ prefix
    and before ``.tsv.gz``, mirroring mriconvert's scans.tsv ``bids_name``."""
    name = Path(output_file).name
    if name.endswith(".tsv.gz"):
        name = name[: -len(".tsv.gz")]
    prefix = entities["participant_id"]
    if entities["session_id"]:
        prefix += f"_{entities['session_id']}"
    prefix += "_"
    return name[len(prefix):] if name.startswith(prefix) else name


def _bids_names_of(output_files: list[str], entities: dict[str, str]) -> str:
    """Comma-separated ``bids_name`` values aligned with ``output_files``."""
    return ",".join(_bids_name_of(f, entities) for f in output_files)


def _find_asset(name: str) -> Path | None:
    """Locate a static asset (data dictionary) in the dev tree or the wheel."""
    here = Path(__file__).resolve().parent
    for candidate in (
        here.parent / "assets" / name,  # dev: src/assets/
        here / "assets" / name,  # wheel: xnatcli/assets/
    ):
        if candidate.is_file():
            return candidate
    return None


def _copy_data_dictionary(output_dir: Path) -> None:
    """Copy the physioscans.json data dictionary next to physioscans.tsv.

    Run once after all conversions. A missing asset is warned about, not fatal.
    """
    src = _find_asset(_SCANS_DICT_FILENAME)
    if src is None:
        print(f"WARNING: {_SCANS_DICT_FILENAME} data dictionary not found; skipping its copy.")
    else:
        shutil.copyfile(src, output_dir / _SCANS_DICT_FILENAME)


def physioconvert_cmd(args: argparse.Namespace) -> int:
    if args.nphysio < 1:
        sys.exit("Error: -n/--nphysio must be >= 1.")

    input_root = Path(args.input).resolve()
    if not input_root.is_dir():
        sys.exit(f"Error: input directory not found: {input_root}")

    # Physio outputs nest under OUTPUT_DIR/PROJECT/, mirroring mriconvert's
    # per-project BIDS layout.
    output_dir = Path(args.output).resolve() / args.project
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        import phys2bids  # noqa: F401
        from phys2bids import io  # noqa: F401
        from phys2bids.phys2bids import phys2bids as _p2b  # noqa: F401
    except ImportError:
        sys.exit(
            "Error: phys2bids is required for physioconvert. "
            "Install it via 'uv sync' or 'pip install phys2bids'."
        )

    # Discover supported files, skipping anything under the output directory
    # (so a re-run does not re-ingest its own BIDS outputs).
    candidates = sorted(
        (
            p
            for p in input_root.rglob("*")
            if p.is_file()
            and p.suffix.lower() in _SUPPORTED_EXTS
            and output_dir not in p.parents
        ),
        key=lambda p: p.as_posix(),
    )
    if not candidates:
        print(f"No phys2bids-supported files found under {input_root}; nothing to do.")
        return 0

    scans_path = output_dir / _SCANS_FILENAME
    existing = _read_existing_scans(scans_path)
    # Prior output_files (kept whole in `existing`) drive the idempotent cleanup
    # of a source's earlier outputs on re-conversion, and let an already-converted
    # file's metrics/bids_name be preserved verbatim when skipped.
    existing_outputs = {
        src: row.get("output_files", "") for src, row in existing.items()
    }

    log_path: Path | None = None
    if args.log:
        while True:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = output_dir / "log" / f"physioconvert_{ts}_log.csv"
            if not log_path.exists():
                break
            time.sleep(1)
    log_writer = _LogWriter(log_path)

    counts = {
        STATUS_CONVERTED: 0,
        STATUS_UNKNOWN_NAME: 0,
        STATUS_DATES_DISAGREE: 0,
        STATUS_NOT_PHYSIO: 0,
        STATUS_READER_MISSING: 0,
        STATUS_CONVERT_ERROR: 0,
        STATUS_MISSING: 0,
    }
    scans_rows: list[dict[str, str]] = []
    handled: set[str] = set()
    # Files already converted on a prior run are relocated (not re-converted);
    # this tallies how many for the summary.
    skipped = 0

    def _finish(result: dict, maps: bool) -> None:
        """Place a completed conversion and record its row — main process only.

        Runs serially regardless of -n, so BIDS naming/collision handling and
        the shared counts/rows/log are never touched concurrently.

        When ``maps`` is True the file's already-converted outputs are relocated
        to match the (possibly edited) entities rather than converted afresh —
        used both by ``-m/--maps`` and, on a plain run, for any file that already
        has outputs on disk (so edits always rename without re-running phys2bids).
        """
        rel = result["rel"]
        handled.add(rel)
        start = result["start"]

        if not result["is_physio"]:
            status = (
                STATUS_READER_MISSING if result["reader_missing"] else STATUS_NOT_PHYSIO
            )
            counts[status] += 1
            print(f"{rel}: {status} — {result['err'] or 'no physio channels'}")
            log_writer.write(start, rel, status)
            # Preserve a prior row's edits if this file used to be physio, with
            # blank metrics.
            if rel in existing:
                scans_rows.append(_preserve_scans_row(rel, existing[rel], status))
            return

        path = input_root / rel
        entities, date_info = _resolve_entities(rel, path, existing)
        if maps:
            # No conversion happens. Relocate the file's already-converted
            # outputs to match the (possibly edited) entities, reusing the same
            # placement logic as a fresh conversion.
            prior = [
                s.strip()
                for s in existing_outputs.get(rel, "").split(",")
                if s.strip()
            ]
            blocked = _blocked_reason(entities)
            if prior:
                status, written, detail = _relocate_existing_outputs(
                    prior, output_dir, entities, blocked is None, path.stem
                )
                if status == STATUS_UNKNOWN_NAME and detail is None:
                    detail = f"{blocked}; moved under {_TMP_DIRNAME}/"
            else:
                # No prior outputs to move (e.g. an earlier CONVERT_ERROR or a
                # file new since the last run); maps mode cannot convert, so only
                # the entity-derived status is recorded.
                written = []
                if blocked is not None:
                    status = STATUS_UNKNOWN_NAME
                    detail = f"{blocked}; not converted (maps mode)"
                else:
                    status, detail = STATUS_CONVERTED, None
            # A placed file whose filename date conflicts with its last-modified
            # date (and was not overridden) is flagged for review, as in a
            # full run.
            if (
                status == STATUS_CONVERTED
                and date_info["disagrees"]
                and not date_info["overridden"]
            ):
                status = STATUS_DATES_DISAGREE
                detail = (
                    f"filename date {date_info['filename_date']} != file date "
                    f"{date_info['reference_date']}; used file date for session_id"
                )
        elif result["convert_error"] is not None:
            status, written, detail = STATUS_CONVERT_ERROR, [], result["convert_error"]
        else:
            blocked = _blocked_reason(entities)
            prior_outputs = existing_outputs.get(rel, "")
            status, written, detail = _place_staged(
                Path(result["staging"]),
                output_dir,
                entities,
                blocked is None,
                prior_outputs,
                path.stem,
            )
            if status == STATUS_UNKNOWN_NAME and detail is None:
                detail = f"{blocked}; staged under {_TMP_DIRNAME}/"
            # A converted file whose filename date conflicts with its
            # last-modified date (and was not overridden) is flagged for review.
            if (
                status == STATUS_CONVERTED
                and date_info["disagrees"]
                and not date_info["overridden"]
            ):
                status = STATUS_DATES_DISAGREE
                detail = (
                    f"filename date {date_info['filename_date']} != file date "
                    f"{date_info['reference_date']}; used file date for session_id"
                )

        counts[status] += 1
        line = f"{rel}: {status}"
        if detail:
            line += f" — {detail}"
        elif written:
            line += f" — wrote {len(written)} file(s)"
        print(line)
        log_writer.write(start, rel, status, written)

        scans_rows.append({
            "source_path": rel,
            "status": status,
            **entities,
            "n_channels": result["n_ch"],
            "sampling_frequencies": result["freqs"],
            "sample_count": result["sample_count"],
            "duration_seconds": result["duration_seconds"],
            "output_files": ",".join(written),
            "bids_name": _bids_names_of(written, entities) if written else "",
            **_resolve_qc_edits(rel, existing),
        })

    # On a plain run, any file already converted (with its outputs still on disk)
    # is relocated to match the map instead of being re-converted: it is fed
    # through _finish in relocate mode using its preserved metrics, so edits to
    # its physioscans.tsv row always rename/move its outputs without re-running
    # phys2bids. Files lacking outputs (new, or a prior CONVERT_ERROR/NOT_PHYSIO)
    # still go to the converter. Under -m/--maps every file already takes the
    # relocate path, so this split is bypassed.
    to_convert: list[Path] = []
    for path in candidates:
        rel = path.relative_to(input_root).as_posix()
        if not args.maps and _has_existing_outputs(
            rel, existing, existing_outputs, output_dir
        ):
            skipped += 1
            _finish(_converted_result(rel, existing[rel]), maps=True)
        else:
            to_convert.append(path)

    # ``to_convert`` keeps ``candidates`` source-path order; placing in that order
    # (not in completion order) keeps BIDS-name/run-number reservation deterministic.
    tasks = [(str(input_root), str(p), args.maps) for p in to_convert]
    if args.nphysio <= 1:
        for task in tasks:
            _finish(_run_worker(task), args.maps)
    else:
        # phys2bids runs in worker processes (real parallelism for the in-process
        # library); placement stays serial in the main process and is drained in
        # sorted task order, buffering out-of-order completions until their turn.
        with ProcessPoolExecutor(max_workers=args.nphysio) as ex:
            fut_to_index = {ex.submit(_run_worker, t): i for i, t in enumerate(tasks)}
            pending: dict[int, dict] = {}
            next_index = 0
            for fut in as_completed(fut_to_index):
                pending[fut_to_index[fut]] = fut.result()
                while next_index in pending:
                    _finish(pending.pop(next_index), args.maps)
                    next_index += 1

    # Preserve rows for files in a prior physioscans.tsv that are no longer on
    # disk so the user's edits are not lost; flag them as MISSING.
    for rel, prior in existing.items():
        if rel in handled:
            continue
        counts[STATUS_MISSING] += 1
        print(f"{rel}: {STATUS_MISSING} — source file not found under input")
        log_writer.write(_logging_now(), rel, STATUS_MISSING)
        scans_rows.append(_preserve_scans_row(rel, prior, STATUS_MISSING))

    # physioscans.tsv is indexed by, and sorted by, source_path.
    _write_tsv(scans_path, scans_rows, _SCANS_COLUMNS)
    _copy_data_dictionary(output_dir)

    total = sum(counts.values())
    print(f"\nProcessed {total} file(s):")
    for status in (
        STATUS_CONVERTED,
        STATUS_UNKNOWN_NAME,
        STATUS_DATES_DISAGREE,
        STATUS_NOT_PHYSIO,
        STATUS_READER_MISSING,
        STATUS_CONVERT_ERROR,
        STATUS_MISSING,
    ):
        print(f"  {status}: {counts[status]}")
    if skipped:
        print(
            f"  ({skipped} already converted on a prior run; not re-converted, "
            "outputs relocated to match the map as needed)"
        )
    print(f"physioscans.tsv written to {scans_path}")
    if log_path is not None:
        print(f"Log written to {log_path}")
    if counts[STATUS_DATES_DISAGREE]:
        print(
            "Some files have a filename date that disagrees with their "
            "last-modified date (status DATES_DISAGREE); they were converted "
            "using the last-modified date. Review and set session_id in "
            "physioscans.tsv if the filename date is correct."
        )
    if counts[STATUS_UNKNOWN_NAME]:
        print(
            f"Some files were converted into {_TMP_DIRNAME}/ with UNKNOWN_NAME "
            "because participant_id is blank (the filename did not start with a "
            "participant label). Fill in participant_id in physioscans.tsv and "
            f"re-run to place them properly (the {_TMP_DIRNAME}/ copy is removed "
            "then)."
        )
    if counts[STATUS_READER_MISSING]:
        print(
            "Some files could not be read because the reader package they "
            "need is not installed (e.g. 'bioread' for .acq). Install it and "
            "re-run."
        )

    return 1 if counts[STATUS_CONVERT_ERROR] or counts[STATUS_READER_MISSING] else 0
