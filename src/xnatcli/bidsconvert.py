import argparse
import csv
import re
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

STATUS_COMPLETE = "COMPLETE"
STATUS_FAILURE = "FAILURE"
STATUS_NONEXISTENT = "NONEXISTENT"
STATUS_EMPTY = "EMPTY"

_OK_STATUSES = {STATUS_COMPLETE, STATUS_EMPTY}
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")
_DICOM_EXTS = {".dcm", ".ima"}

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
                        "PROJECT_ID",
                        "SUBJECT_ID",
                        "EXPERIMENT_ID",
                        "STATUS",
                    ]
                )

    def write(
        self,
        datestamp: str,
        project_id: str,
        subject_id: str,
        experiment_id: str,
        status: str,
    ) -> None:
        if self._path is None:
            return
        with self._lock, self._path.open("a", newline="") as f:
            csv.writer(f).writerow(
                [datestamp, project_id, subject_id, experiment_id, status]
            )


def _find_first_valid_dicom(scans_dir: Path) -> Path | None:
    """First .dcm/.IMA file under scans_dir that pydicom can parse, or None."""
    import pydicom

    for path in scans_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _DICOM_EXTS:
            continue
        try:
            pydicom.dcmread(str(path), stop_before_pixels=True)
            return path
        except Exception:
            continue
    return None


def _convert_one(
    input_root: Path,
    project_id: str,
    subject_id: str,
    experiment_id: str,
    output_dir: Path,
    config_path: Path,
    dcm2bids_path: str,
) -> tuple[str, str | None]:
    exp_dir = input_root / project_id / subject_id / experiment_id
    if not exp_dir.is_dir():
        return STATUS_NONEXISTENT, f"session directory not found: {exp_dir}"

    scans_dir = exp_dir / "scans"
    if not scans_dir.is_dir():
        return STATUS_EMPTY, f"no 'scans/' subdirectory under {exp_dir}"

    if _find_first_valid_dicom(scans_dir) is None:
        return STATUS_EMPTY, "no readable .dcm/.IMA DICOM files under scans/"

    participant = _NON_ALNUM.sub("", subject_id)
    session = _NON_ALNUM.sub("", experiment_id)
    if not participant or not session:
        return STATUS_FAILURE, (
            f"empty PARTICIPANT or SESSION after sanitizing "
            f"SUBJECT_ID={subject_id!r} EXPERIMENT_ID={experiment_id!r}"
        )

    bids_root = output_dir / project_id
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
        project_id, subject_id = args.subject
        subject_dir = input_root / project_id / subject_id
        if not subject_dir.is_dir():
            sys.exit(f"Error: subject directory not found: {subject_dir}")
        sessions = [
            (project_id, subject_id, exp.name)
            for exp in sorted(subject_dir.iterdir())
            if exp.is_dir()
        ]
        if not sessions:
            sys.exit(f"Error: no sessions found under {subject_dir}")
        return sessions

    project_id = args.project
    project_dir = input_root / project_id
    if not project_dir.is_dir():
        sys.exit(f"Error: project directory not found: {project_dir}")
    sessions: list[tuple[str, str, str]] = []
    for subject_dir in sorted(project_dir.iterdir()):
        if not subject_dir.is_dir():
            continue
        for exp_dir in sorted(subject_dir.iterdir()):
            if exp_dir.is_dir():
                sessions.append(
                    (project_id, subject_dir.name, exp_dir.name)
                )
    if not sessions:
        sys.exit(f"Error: no sessions found under {project_dir}")
    return sessions


def bidsconvert_cmd(args: argparse.Namespace) -> int:
    if args.nconvert < 1:
        sys.exit("Error: -n/--nconvert must be >= 1.")

    input_root = Path(args.input).resolve()
    if not input_root.is_dir():
        sys.exit(f"Error: input directory not found: {input_root}")

    config_path = Path(args.config).resolve()
    if not config_path.is_file():
        sys.exit(f"Error: config file not found: {config_path}")

    output_dir = Path(args.output).resolve()
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
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        log_path = output_dir / "log" / f"bidsconvert_{ts}_log.csv"
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

    bad = counts[STATUS_FAILURE] + counts[STATUS_NONEXISTENT]
    return 0 if bad == 0 else 1
