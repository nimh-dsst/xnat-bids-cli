import argparse
import csv
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from pyxnat import Interface

from .login import load_credentials

STATUS_COMPLETE = "COMPLETE"
STATUS_PARTIAL = "PARTIAL"
STATUS_FAILURE = "FAILURE"
STATUS_NONEXISTENT = "NONEXISTENT"
STATUS_EMPTY = "EMPTY"

_OK_STATUSES = {STATUS_COMPLETE, STATUS_EMPTY}

_print_lock = threading.Lock()
_thread_iface = threading.local()


def _safe_print(msg: str) -> None:
    with _print_lock:
        print(msg)


def _logging_now() -> str:
    # Matches logging module's default %(asctime)s: "YYYY-MM-DD HH:MM:SS,mmm"
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


def _get_thread_interface(server: str, user: str, password: str) -> Interface:
    iface = getattr(_thread_iface, "iface", None)
    if iface is None:
        iface = Interface(server=server, user=user, password=password)
        _thread_iface.iface = iface
    return iface


def _close_thread_interface() -> None:
    iface = getattr(_thread_iface, "iface", None)
    if iface is not None:
        try:
            iface.disconnect()
        except Exception:
            pass
        _thread_iface.iface = None


def _enumerate_files(experiment) -> list[tuple[Path, object]]:
    files: list[tuple[Path, object]] = []
    for scan in experiment.scans():
        scan_id = scan.id()
        for resource in scan.resources():
            label = resource.label()
            for f in resource.files():
                files.append(
                    (Path("scans") / scan_id / label / f.label(), f)
                )
    for resource in experiment.resources():
        label = resource.label()
        for f in resource.files():
            files.append((Path("resources") / label / f.label(), f))
    return files


def _download_files(
    files: list[tuple[Path, object]],
    experiment_root: Path,
    n_parallel: int,
) -> tuple[int, int]:
    success = 0
    failure = 0

    def _one(rel_dest: Path, f) -> bool:
        dest = experiment_root / rel_dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            f.get(str(dest))
            _safe_print(f"  {dest}")
            return True
        except Exception as e:
            _safe_print(f"  ERROR downloading {dest}: {e}")
            return False

    if n_parallel <= 1:
        for rel_dest, f in files:
            if _one(rel_dest, f):
                success += 1
            else:
                failure += 1
    else:
        with ThreadPoolExecutor(max_workers=n_parallel) as ex:
            futures = [ex.submit(_one, rel, f) for rel, f in files]
            for fut in as_completed(futures):
                if fut.result():
                    success += 1
                else:
                    failure += 1

    return success, failure


def _classify_status(file_count: int, success: int, failure: int) -> str:
    if file_count == 0:
        return STATUS_EMPTY
    if failure == 0:
        return STATUS_COMPLETE
    if success == 0:
        return STATUS_FAILURE
    return STATUS_PARTIAL


def _process_experiment(
    interface: Interface,
    project_id: str,
    subject_id: str,
    experiment_id: str,
    output_dir: Path,
    n_parallel_files: int,
) -> str:
    try:
        experiment = (
            interface.select.project(project_id)
            .subject(subject_id)
            .experiment(experiment_id)
        )
        if not experiment.exists():
            return STATUS_NONEXISTENT
        files = _enumerate_files(experiment)
    except Exception as e:
        _safe_print(
            f"Error walking {project_id}/{subject_id}/{experiment_id}: {e}"
        )
        return STATUS_FAILURE

    if not files:
        return STATUS_EMPTY

    experiment_root = output_dir / project_id / subject_id / experiment_id
    success, failure = _download_files(files, experiment_root, n_parallel_files)
    return _classify_status(len(files), success, failure)


def _read_csv_rows(path: Path) -> list[tuple[str, str, str]]:
    if not path.exists():
        sys.exit(f"Error: input CSV not found: {path}")
    rows: list[tuple[str, str, str]] = []
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        required = {"PROJECT_ID", "SUBJECT_ID", "EXPERIMENT_ID"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            sys.exit(
                f"Error: input CSV {path} must have columns "
                "PROJECT_ID, SUBJECT_ID, EXPERIMENT_ID."
            )
        for i, row in enumerate(reader, start=2):
            p = (row.get("PROJECT_ID") or "").strip()
            s = (row.get("SUBJECT_ID") or "").strip()
            e = (row.get("EXPERIMENT_ID") or "").strip()
            if not (p and s and e):
                sys.exit(
                    f"Error: row {i} of {path} is missing a required value."
                )
            rows.append((p, s, e))
    return rows


def _run_single(
    server: str,
    user: str,
    password: str,
    project_id: str,
    subject_id: str,
    experiment_id: str,
    output_dir: Path,
    n_parallel_files: int,
    log_writer: _LogWriter,
) -> str:
    iface = Interface(server=server, user=user, password=password)
    try:
        start = _logging_now()
        status = _process_experiment(
            iface,
            project_id,
            subject_id,
            experiment_id,
            output_dir,
            n_parallel_files,
        )
    finally:
        try:
            iface.disconnect()
        except Exception:
            pass
    log_writer.write(start, project_id, subject_id, experiment_id, status)
    return status


def _run_csv(
    server: str,
    user: str,
    password: str,
    rows: list[tuple[str, str, str]],
    output_dir: Path,
    n_parallel_experiments: int,
    log_writer: _LogWriter,
) -> dict[str, int]:
    counts = {
        STATUS_COMPLETE: 0,
        STATUS_PARTIAL: 0,
        STATUS_FAILURE: 0,
        STATUS_NONEXISTENT: 0,
        STATUS_EMPTY: 0,
    }

    def _worker(triplet: tuple[str, str, str]) -> str:
        p, s, e = triplet
        iface = _get_thread_interface(server, user, password)
        start = _logging_now()
        status = _process_experiment(iface, p, s, e, output_dir, 1)
        log_writer.write(start, p, s, e, status)
        return status

    if n_parallel_experiments <= 1:
        try:
            for triplet in rows:
                counts[_worker(triplet)] += 1
        finally:
            _close_thread_interface()
    else:
        with ThreadPoolExecutor(max_workers=n_parallel_experiments) as ex:
            futures = [ex.submit(_worker, t) for t in rows]
            for fut in as_completed(futures):
                counts[fut.result()] += 1
        # Worker threads' Interface objects are GC'd when the pool shuts down.

    return counts


def download_cmd(args: argparse.Namespace) -> int:
    if args.ndownload < 1:
        sys.exit("Error: -n/--ndownload must be >= 1.")

    server, user, password = load_credentials()
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    log_path: Path | None = None
    if args.log:
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        log_path = output_dir / "log" / f"download_{ts}_log.csv"
    log_writer = _LogWriter(log_path)

    if args.triplet is not None:
        project_id, subject_id, experiment_id = args.triplet
        status = _run_single(
            server,
            user,
            password,
            project_id,
            subject_id,
            experiment_id,
            output_dir,
            args.ndownload,
            log_writer,
        )
        print(
            f"Status for {project_id}/{subject_id}/{experiment_id}: {status}"
        )
        if log_path is not None:
            print(f"Log written to {log_path}")
        return 0 if status in _OK_STATUSES else 1

    rows = _read_csv_rows(Path(args.input))
    counts = _run_csv(
        server,
        user,
        password,
        rows,
        output_dir,
        args.ndownload,
        log_writer,
    )
    total = sum(counts.values())
    print(f"\nProcessed {total} experiment(s):")
    for status in (
        STATUS_COMPLETE,
        STATUS_PARTIAL,
        STATUS_FAILURE,
        STATUS_NONEXISTENT,
        STATUS_EMPTY,
    ):
        print(f"  {status}: {counts[status]}")
    if log_path is not None:
        print(f"Log written to {log_path}")
    bad = (
        counts[STATUS_PARTIAL]
        + counts[STATUS_FAILURE]
        + counts[STATUS_NONEXISTENT]
    )
    return 0 if bad == 0 else 1
