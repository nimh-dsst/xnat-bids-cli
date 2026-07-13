import argparse
import csv
import json
import re
import shutil
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

# QC/status manifest written at the mri BIDS project root, alongside
# mriconvert's mriconvert_qc.tsv/scans.tsv. Fully regenerated every run from the
# current mriconvert_qc.tsv content -- a visual reference for an expert reviewer,
# not a store consulted by a later run.
_QC_FILENAME = "physioconvert_qc.tsv"

# Static data-dictionary sidecar copied next to the TSV on each run.
_QC_DICT_FILENAME = "physioconvert_qc.json"

# BIDS suffix for continuous physiological recordings.
_SUFFIX = "physio"

# physioconvert_qc.tsv holds: the physio basename used, keyed first, and the
# regenerated status/metrics for its conversion.
_QC_COLUMNS = [
    "physio", "status", "n_channels", "sampling_frequencies",
    "sample_count", "duration_seconds",
]

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")

# A physio recording captures the whole run, not one echo of a multi-echo
# scan, so an ``echo-<N>`` entity inherited from the paired scan's bids_name
# must not carry over into the physio output's filename.
_ECHO_ENTITY = re.compile(r"_echo-\d+")

STATUS_CONVERTED = "CONVERTED"
STATUS_NOT_PHYSIO = "NOT_PHYSIO"
STATUS_READER_MISSING = "READER_MISSING"
STATUS_CONVERT_ERROR = "CONVERT_ERROR"
STATUS_SOURCE_MISSING = "SOURCE_MISSING"
STATUS_COLLISION = "COLLISION"

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
    """Append per-association rows to a CSV log, mirroring the other
    subcommands.

    A no-op when ``path`` is ``None`` (logging disabled). The header is
    ``DATESTAMP,STATUS,MRI_FILENAME,PHYSIO_SOURCE,DESTINATION_PATH``;
    physioconvert places files serially in the main process, so no lock is
    needed. An association that produced several outputs (a multi-frequency
    split) emits one row per destination; one with no output emits a single
    row with a blank ``DESTINATION_PATH``.
    """

    def __init__(self, path: Path | None):
        self._path = path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", newline="") as f:
                csv.writer(f).writerow(
                    ["DATESTAMP", "STATUS", "MRI_FILENAME", "PHYSIO_SOURCE", "DESTINATION_PATH"]
                )

    def write(
        self,
        datestamp: str,
        filename: str,
        status: str,
        physio_source: str,
        destinations: list[str] | None = None,
    ) -> None:
        """Append one row per destination path (or a single blank-dest row)."""
        if self._path is None:
            return
        dests = destinations or [""]
        with self._path.open("a", newline="") as f:
            writer = csv.writer(f)
            for dest in dests:
                writer.writerow([datestamp, status, filename, physio_source, dest])


def _fmt_freq(freq: float) -> str:
    """Format a sampling frequency without trailing zeros (e.g. ``1000``)."""
    return f"{freq:g}"


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


def _physio_basename(
    participant_id: str, session_id: str, entity_name: str, recording: str | None
) -> str:
    """Assemble the BIDS basename for a physio output paired with an mri scan.

    ``entity_name`` is the associated mriconvert_qc.tsv row's ``rename`` (if set)
    else ``bids_name`` — e.g. ``task-rest_bold``. Its trailing
    underscore-delimited suffix token is replaced with ``physio`` (e.g.
    ``task-rest_bold`` -> ``task-rest_physio``; a bare suffix like ``bold``
    with no other tokens yields just ``physio``), with an optional
    ``recording-<label>`` entity inserted just before it for a
    multi-frequency phys2bids split. Any ``echo-<N>`` entity is dropped
    first: a physio recording aligns with every echo of a multi-echo scan,
    not just the one row it happened to be associated with.
    """
    entity_name = _ECHO_ENTITY.sub("", entity_name)
    prefix = entity_name.rsplit("_", 1)[0] if "_" in entity_name else ""
    parts = [participant_id]
    if session_id:
        parts.append(session_id)
    if prefix:
        parts.append(prefix)
    if recording:
        parts.append(f"recording-{recording}")
    parts.append(_SUFFIX)
    return "_".join(parts)


def _destination_dir(bids_root: Path, participant_id: str, session_id: str, datatype: str) -> Path:
    """``bids_root/participant_id/[session_id/]datatype`` — the same
    directory the paired ``.nii.gz`` lives in."""
    out = bids_root / participant_id
    if session_id:
        out = out / session_id
    return out / datatype


def _find_collisions(rows: list[dict[str, str]]) -> dict[str, list[str]]:
    """``{physio_basename: [filenames]}`` for every ``physio`` value
    referenced by more than one row (rows must already be filtered to
    non-blank ``physio``)."""
    by_physio: dict[str, list[str]] = {}
    for row in rows:
        by_physio.setdefault(row["physio"].strip(), []).append(row["filename"])
    return {k: v for k, v in by_physio.items() if len(v) > 1}


def _read_physio_parent(mriconvert_qc_json: Path) -> Path | None:
    """Read ``PhysioParent.Value`` from mriconvert_qc.json.

    Returns ``None`` if the file is absent/unreadable, the value is blank, or
    the path is not a directory on disk.
    """
    if not mriconvert_qc_json.is_file():
        return None
    try:
        with mriconvert_qc_json.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    value = data.get("PhysioParent", {}).get("Value", "")
    if not value:
        return None
    path = Path(value)
    return path if path.is_dir() else None


def _run_phys2bids_to_staging(file_path: Path) -> tuple[str | None, str | None]:
    """Run the phys2bids workflow for one file into a fresh staging directory.

    This is the slow part of conversion — running phys2bids — isolated so it can
    be parallelized across processes. The placement of its output into the BIDS
    tree happens later, serially, in the main process. Returns
    ``(staging_dir, None)`` on success, or ``(None, error)``; the staging
    directory is left for the caller to consume and remove.
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


def _run_worker(task: tuple[str, str]) -> dict:
    """Validate and convert one physio association; the unit of work for
    parallel execution.

    Runs in a worker process (or the main process when serial): loads the raw
    file to confirm it is physiological data and gather channel info, then —
    if it is — runs phys2bids into a staging directory. Returns a picklable
    dict; placement into the BIDS tree happens later in the main process.
    """
    filename, raw_path_str = task
    path = Path(raw_path_str)
    result = {
        "filename": filename,
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

    result["staging"], result["convert_error"] = _run_phys2bids_to_staging(path)
    return result


def _place_at_destination(tsv_src: Path, dest_tsv: Path, bids_root: Path) -> str:
    """Move a phys2bids ``.tsv.gz``/``.json`` pair from staging to
    ``dest_tsv`` (which must not already exist). Returns the ``.tsv.gz``
    path relative to ``bids_root`` (POSIX)."""
    src_stem = tsv_src.name[: -len(".tsv.gz")]
    shutil.move(str(tsv_src), str(dest_tsv))
    json_src = tsv_src.with_name(src_stem + ".json")
    if json_src.is_file():
        shutil.move(
            str(json_src), str(dest_tsv.with_name(dest_tsv.name[: -len(".tsv.gz")] + ".json"))
        )
    else:
        print(f"WARNING: no JSON sidecar produced for {tsv_src.name}")
    return dest_tsv.relative_to(bids_root).as_posix()


def _place_converted(
    staging: Path,
    bids_root: Path,
    row: dict[str, str],
    base_stem: str,
    claimed: dict[str, str],
) -> tuple[str, list[str], str | None]:
    """Move a completed conversion's staged output into the mri BIDS tree.

    Places each produced ``.tsv.gz``/``.json`` pair at
    ``_destination_dir(...)/_physio_basename(...).{tsv.gz,json}``, adding a
    ``recording-<label>`` entity for a multi-frequency phys2bids split.

    Every run reconverts from scratch, so a destination left over from an
    earlier run of this same association is simply replaced. ``claimed``
    tracks destinations already written by another association *this run*
    (keyed by the owning row's ``filename``) so two distinct associations
    can never overwrite each other, even if their computed names collide.
    Returns ``(status, written_relpaths, detail)``. The staging directory is
    removed when done.
    """
    try:
        produced = sorted(staging.glob("*.tsv.gz"))
        if not produced:
            return STATUS_CONVERT_ERROR, [], "phys2bids produced no .tsv.gz output"

        multi = len(produced) > 1
        dest_dir = _destination_dir(
            bids_root, row["participant_id"], row["session_id"], row["datatype"]
        )
        dest_dir.mkdir(parents=True, exist_ok=True)
        entity_name = row["rename"].strip() or row["bids_name"].strip()
        assoc_key = row["filename"]

        written: list[str] = []
        for index, tsv_src in enumerate(produced):
            stem = tsv_src.name[: -len(".tsv.gz")]
            recording = _recording_label(stem, base_stem, index) if multi else None
            basename = _physio_basename(
                row["participant_id"], row["session_id"], entity_name, recording
            )
            dest_tsv = dest_dir / f"{basename}.tsv.gz"
            dest_rel = dest_tsv.relative_to(bids_root).as_posix()

            owner = claimed.get(dest_rel)
            if owner is not None and owner != assoc_key:
                return (
                    STATUS_CONVERT_ERROR,
                    [],
                    "destination already occupied by a different association: "
                    f"{dest_rel}",
                )
            if dest_tsv.exists():
                dest_tsv.unlink()
                dest_json = dest_tsv.with_name(dest_tsv.name[: -len(".tsv.gz")] + ".json")
                if dest_json.is_file():
                    dest_json.unlink()

            claimed[dest_rel] = assoc_key
            written.append(_place_at_destination(tsv_src, dest_tsv, bids_root))
        return STATUS_CONVERTED, written, None
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _carry_row(physio: str, status: str) -> dict[str, str]:
    """Build a QC row with blank metrics, for an association not (re)converted
    this run (COLLISION, SOURCE_MISSING)."""
    return {
        "physio": physio,
        "status": status,
        "n_channels": "",
        "sampling_frequencies": "",
        "sample_count": "",
        "duration_seconds": "",
    }


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


def _write_qc_dict(bids_root: Path, physio_parent: Path | None) -> None:
    """Write physioconvert_qc.json from the static asset, injecting
    ``PhysioParent`` with this run's resolved value (mirroring mriconvert's
    ``PhysioParent`` key in mriconvert_qc.json). Run once after all conversions.
    A missing or unreadable asset is warned about, not fatal.
    """
    src = _find_asset(_QC_DICT_FILENAME)
    if src is None:
        print(f"WARNING: {_QC_DICT_FILENAME} data dictionary not found; skipping its copy.")
        return
    try:
        with src.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        print(f"WARNING: could not read {src}: {exc}; skipping its copy.")
        return

    data.setdefault("PhysioParent", {})["Value"] = str(physio_parent) if physio_parent else ""

    dest = bids_root / _QC_DICT_FILENAME
    with dest.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
        f.write("\n")


def _write_tsv(path: Path, rows: list[dict[str, str]], columns: list[str]) -> None:
    """Write a TSV with the given columns, one row per association, sorted
    by ``physio``."""
    rows = sorted(rows, key=lambda r: r["physio"])
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def physioconvert_cmd(args: argparse.Namespace) -> int:
    if args.nphysio < 1:
        sys.exit("Error: -n/--nphysio must be >= 1.")

    bids_root = Path(args.output).resolve() / args.project
    if not bids_root.is_dir():
        sys.exit(
            f"Error: BIDS dataset not found at {bids_root}; run xnatcli "
            "mriconvert first."
        )

    mriconvert_qc_tsv = bids_root / "mriconvert_qc.tsv"
    if not mriconvert_qc_tsv.is_file():
        sys.exit(f"Error: {mriconvert_qc_tsv} not found; run xnatcli mriconvert first.")

    try:
        import phys2bids  # noqa: F401
        from phys2bids import io  # noqa: F401
        from phys2bids.phys2bids import phys2bids as _p2b  # noqa: F401
    except ImportError:
        sys.exit(
            "Error: phys2bids is required for physioconvert. "
            "Install it via 'uv sync' or 'pip install phys2bids'."
        )

    # physioconvert only reads mriconvert_qc.tsv -- it never writes it back.
    with mriconvert_qc_tsv.open(newline="", encoding="utf-8") as f:
        mri_rows = list(csv.DictReader(f, delimiter="\t"))

    in_scope = [r for r in mri_rows if (r.get("physio") or "").strip()]
    if not in_scope:
        print(
            "No physio associations found in mriconvert_qc.tsv's 'physio' column; "
            "nothing to do."
        )
        return 0

    qc_path = bids_root / _QC_FILENAME

    log_path: Path | None = None
    if args.log:
        while True:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = bids_root / "log" / f"physioconvert_{ts}_log.csv"
            if not log_path.exists():
                break
            time.sleep(1)
    log_writer = _LogWriter(log_path)

    counts = {
        STATUS_CONVERTED: 0,
        STATUS_NOT_PHYSIO: 0,
        STATUS_READER_MISSING: 0,
        STATUS_CONVERT_ERROR: 0,
        STATUS_SOURCE_MISSING: 0,
        STATUS_COLLISION: 0,
    }
    qc_rows: list[dict[str, str]] = []
    # Destinations already written by an association this run, keyed by
    # relpath -> the writing row's filename; guards against two distinct
    # associations computing the same output path.
    claimed: dict[str, str] = {}

    # --- Collisions: the same raw physio basename referenced by more than
    # one mriconvert_qc.tsv row. None of those rows are converted until resolved.
    collisions = _find_collisions(in_scope)
    for row in in_scope:
        physio = row["physio"].strip()
        if physio not in collisions:
            continue
        filename = row["filename"]
        counts[STATUS_COLLISION] += 1
        print(f"{filename}: {STATUS_COLLISION} — physio {physio!r} referenced by multiple rows")
        log_writer.write(_logging_now(), filename, STATUS_COLLISION, physio, [])
    for physio, filenames in collisions.items():
        print(
            f"WARNING: physio {physio!r} is referenced by {len(filenames)} "
            f"mriconvert_qc.tsv rows ({', '.join(filenames)}); none will be "
            "converted until only one row references it."
        )
        qc_rows.append(_carry_row(physio, STATUS_COLLISION))

    remaining = [r for r in in_scope if r["physio"].strip() not in collisions]
    physio_parent = _read_physio_parent(bids_root / "mriconvert_qc.json")

    # --- Classify each remaining association: needs conversion, or blocked. ---
    to_convert: list[dict[str, str]] = []
    for row in remaining:
        filename = row["filename"]
        physio = row["physio"].strip()

        if not row["participant_id"] or not row["datatype"]:
            counts[STATUS_SOURCE_MISSING] += 1
            detail = "mriconvert_qc.tsv row has blank participant_id/datatype; cannot place physio output"
            print(f"{filename}: {STATUS_SOURCE_MISSING} — {detail}")
            log_writer.write(_logging_now(), filename, STATUS_SOURCE_MISSING, physio, [])
            qc_rows.append(_carry_row(physio, STATUS_SOURCE_MISSING))
            continue

        if physio_parent is None:
            counts[STATUS_SOURCE_MISSING] += 1
            print(f"{filename}: {STATUS_SOURCE_MISSING} — PhysioParent not set/found in mriconvert_qc.json")
            log_writer.write(_logging_now(), filename, STATUS_SOURCE_MISSING, physio, [])
            qc_rows.append(_carry_row(physio, STATUS_SOURCE_MISSING))
            continue

        raw_path = physio_parent / physio
        if not raw_path.is_file():
            counts[STATUS_SOURCE_MISSING] += 1
            print(f"{filename}: {STATUS_SOURCE_MISSING} — {physio!r} not found under PhysioParent")
            log_writer.write(_logging_now(), filename, STATUS_SOURCE_MISSING, physio, [])
            qc_rows.append(_carry_row(physio, STATUS_SOURCE_MISSING))
            continue

        to_convert.append(row)

    # ``to_convert`` is placed in sorted filename order (not completion order)
    # so results (and log/QC ordering) are deterministic regardless of -n.
    to_convert.sort(key=lambda r: r["filename"])
    tasks_by_filename = {r["filename"]: r for r in to_convert}
    tasks = [
        (r["filename"], str(physio_parent / r["physio"].strip())) for r in to_convert
    ]

    def _finish(row: dict[str, str], result: dict) -> None:
        filename = row["filename"]
        physio = row["physio"].strip()
        start = result["start"]

        if not result["is_physio"]:
            status = STATUS_READER_MISSING if result["reader_missing"] else STATUS_NOT_PHYSIO
            counts[status] += 1
            print(f"{filename}: {status} — {result['err'] or 'no physio channels'}")
            log_writer.write(start, filename, status, physio, [])
            qc_rows.append(_carry_row(physio, status))
            return

        if result["convert_error"] is not None:
            status, written, detail = STATUS_CONVERT_ERROR, [], result["convert_error"]
        else:
            base_stem = Path(physio).stem
            status, written, detail = _place_converted(
                Path(result["staging"]), bids_root, row, base_stem, claimed,
            )

        counts[status] += 1
        line = f"{filename}: {status}"
        if detail:
            line += f" — {detail}"
        elif written:
            line += f" — wrote {len(written)} file(s)"
        print(line)
        log_writer.write(start, filename, status, physio, written)

        qc_rows.append({
            "physio": physio,
            "status": status,
            "n_channels": result["n_ch"],
            "sampling_frequencies": result["freqs"],
            "sample_count": result["sample_count"],
            "duration_seconds": result["duration_seconds"],
        })

    if args.nphysio <= 1:
        for task in tasks:
            _finish(tasks_by_filename[task[0]], _run_worker(task))
    else:
        # phys2bids runs in worker processes (real parallelism, since it is an
        # in-process Python library); placement stays serial in the main
        # process and is drained in sorted-filename order (out-of-order
        # completions are buffered until their turn), so results are fully
        # deterministic regardless of -n.
        with ProcessPoolExecutor(max_workers=args.nphysio) as ex:
            fut_to_index = {ex.submit(_run_worker, t): i for i, t in enumerate(tasks)}
            pending: dict[int, dict] = {}
            next_index = 0
            for fut in as_completed(fut_to_index):
                pending[fut_to_index[fut]] = fut.result()
                while next_index in pending:
                    _finish(tasks_by_filename[tasks[next_index][0]], pending.pop(next_index))
                    next_index += 1

    _write_tsv(qc_path, qc_rows, _QC_COLUMNS)
    _write_qc_dict(bids_root, physio_parent)

    total = sum(counts.values())
    print(f"\nProcessed {total} physio association(s):")
    for status in (
        STATUS_CONVERTED,
        STATUS_NOT_PHYSIO,
        STATUS_READER_MISSING,
        STATUS_CONVERT_ERROR,
        STATUS_SOURCE_MISSING,
        STATUS_COLLISION,
    ):
        print(f"  {status}: {counts[status]}")
    print(f"{_QC_FILENAME} written to {qc_path}")
    if log_path is not None:
        print(f"Log written to {log_path}")
    if counts[STATUS_COLLISION]:
        print(
            "Some physio associations were skipped because their raw file is "
            "referenced by more than one mriconvert_qc.tsv row (status COLLISION). "
            "Clear all but one row's physio column and re-run."
        )
    if counts[STATUS_SOURCE_MISSING]:
        print(
            "Some physio associations could not be resolved to a file (status "
            "SOURCE_MISSING). Check -y/--physio (mriconvert) and the "
            "physio column and re-run."
        )
    if counts[STATUS_READER_MISSING]:
        print(
            "Some files could not be read because the reader package they "
            "need is not installed (e.g. 'bioread' for .acq). Install it and "
            "re-run."
        )

    return 1 if counts[STATUS_CONVERT_ERROR] or counts[STATUS_READER_MISSING] else 0
