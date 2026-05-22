import argparse
import csv
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

STATUS_COMPLETE = "COMPLETE"
STATUS_FAILURE = "FAILURE"


def _logging_now() -> str:
    now = datetime.now()
    return f"{now.strftime('%Y-%m-%d %H:%M:%S')},{now.microsecond // 1000:03d}"


class _LogWriter:
    def __init__(self, path: Path | None):
        self._path = path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", newline="") as f:
                csv.writer(f).writerow(
                    ["DATESTAMP", "PROJECT", "STEP", "STATUS"]
                )

    def write(
        self, datestamp: str, project: str, step: str, status: str
    ) -> None:
        if self._path is None:
            return
        with self._path.open("a", newline="") as f:
            csv.writer(f).writerow([datestamp, project, step, status])


def _run_step(
    label: str,
    cmd: list[str],
    project: str,
    log_writer: _LogWriter,
) -> bool:
    start = _logging_now()
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    status = STATUS_COMPLETE if result.returncode == 0 else STATUS_FAILURE
    log_writer.write(start, project, label, status)
    if result.returncode != 0:
        print(
            f"Error: cubids {label} exited with code {result.returncode}.",
            file=sys.stderr,
        )
        return False
    return True


def cubids_cmd(args: argparse.Namespace) -> int:
    input_root = Path(args.input).resolve()
    if not input_root.is_dir():
        sys.exit(f"Error: input directory not found: {input_root}")

    project = args.project
    bids_dir = input_root / project
    if not bids_dir.is_dir():
        sys.exit(
            f"Error: BIDS dataset for project {project!r} not found at "
            f"{bids_dir}"
        )

    cubids = shutil.which("cubids")
    if cubids is None:
        sys.exit(
            "Error: required tool 'cubids' was not found on PATH. "
            "Install it (e.g., 'uv sync' or 'pip install cubids') and try again."
        )

    output_dir = input_root / f"PROJECT-{project}_cubids"
    output_dir.mkdir(parents=True, exist_ok=True)

    log_path: Path | None = None
    if args.log:
        while True:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = output_dir / "log" / f"cubids_{ts}_log.csv"
            if not log_path.exists():
                break
            time.sleep(1)
    log_writer = _LogWriter(log_path)

    ok = _run_step(
        "add-nifti-info",
        [cubids, "add-nifti-info", str(bids_dir)],
        project,
        log_writer,
    )
    if not ok:
        if log_path is not None:
            print(f"Log written to {log_path}")
        return 1

    v0_prefix = output_dir / "v0"
    ok = _run_step(
        "group",
        [cubids, "group", str(bids_dir), str(v0_prefix)],
        project,
        log_writer,
    )

    if log_path is not None:
        print(f"Log written to {log_path}")
    return 0 if ok else 1
