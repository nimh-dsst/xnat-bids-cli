import argparse
import csv
import os
import sys
import threading
import time
from collections.abc import Callable
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


def _enable_ansi() -> bool:
    if not sys.stdout.isatty():
        return False
    if os.name != "nt":
        return True
    # On Windows, try to enable VT processing. If the call fails (e.g., on
    # mintty/Cygwin where there is no Win32 console handle), assume the
    # terminal handles ANSI itself; isatty was already true.
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        STD_OUTPUT_HANDLE = -11
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        mode = ctypes.c_ulong()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(
                handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
            )
    except Exception:
        pass
    return True


class _ProgressDisplay:
    """One in-place progress bar per concurrent triplet.

    Reserves n_slots terminal lines below the cursor at construction. Each
    slot displays a 20-character bar (5% per cell), file count, percent, and
    the PROJECT/SUBJECT/EXPERIMENT label of the triplet currently using it.
    Slots are reused as triplets finish; the final-state bar of a finished
    triplet remains visible until a new one takes the slot.
    """

    BAR_WIDTH = 20

    def __init__(self, n_slots: int):
        self._n_slots = max(0, n_slots)
        self._lock = threading.Lock()
        self._slots: list[bool] = [False] * self._n_slots
        self._states: list[tuple[str, int, int, str | None]] = [
            ("", 0, 0, None) for _ in range(self._n_slots)
        ]
        self._enabled = self._n_slots > 0 and _enable_ansi()
        if self._enabled:
            sys.stdout.write("\n" * self._n_slots)
            sys.stdout.flush()

    @classmethod
    def _format(
        cls, label: str, done: int, total: int, status: str | None
    ) -> str:
        if total <= 0:
            pct = 100.0 if status else 0.0
            filled = cls.BAR_WIDTH if status else 0
        else:
            pct = (done / total) * 100.0
            filled = min(cls.BAR_WIDTH, int(done * cls.BAR_WIDTH / total))
        bar = "#" * filled + "-" * (cls.BAR_WIDTH - filled)
        suffix = f" {status}" if status else ""
        return f"[{bar}] {done}/{total} ({pct:5.1f}%) {label}{suffix}"

    def _redraw_locked(self) -> None:
        # Cursor sits below the bar area. Move up to the top, rewrite each
        # line, ending again below the bar area.
        sys.stdout.write(f"\x1b[{self._n_slots}A")
        for i in range(self._n_slots):
            label, done, total, status = self._states[i]
            sys.stdout.write("\r\x1b[2K")
            if label:
                sys.stdout.write(self._format(label, done, total, status))
            sys.stdout.write("\n")
        sys.stdout.flush()

    def acquire(self, label: str, total: int) -> int:
        with self._lock:
            for i, occupied in enumerate(self._slots):
                if not occupied:
                    self._slots[i] = True
                    self._states[i] = (label, 0, total, None)
                    if self._enabled:
                        self._redraw_locked()
                    return i
            return -1

    def update(self, slot: int, done: int) -> None:
        if slot < 0:
            return
        with self._lock:
            label, _, total, status = self._states[slot]
            self._states[slot] = (label, done, total, status)
            if self._enabled:
                self._redraw_locked()

    def release(self, slot: int, status: str) -> None:
        if slot < 0:
            return
        with self._lock:
            label, done, total, _ = self._states[slot]
            self._states[slot] = (label, done, total, status)
            self._slots[slot] = False
            if self._enabled:
                self._redraw_locked()
            else:
                print(self._format(label, done, total, status))

    def print_above(self, msg: str) -> None:
        with self._lock:
            if not self._enabled:
                print(msg)
                return
            sys.stdout.write(f"\x1b[{self._n_slots}A")
            sys.stdout.write("\x1b[J")
            for line in msg.split("\n"):
                sys.stdout.write(line + "\n")
            sys.stdout.write("\n" * self._n_slots)
            sys.stdout.flush()
            self._redraw_locked()

    def close(self) -> None:
        with self._lock:
            sys.stdout.flush()


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
    on_file_complete: Callable[[bool], None] | None = None,
    on_error: Callable[[str], None] | None = None,
) -> tuple[int, int]:
    success = 0
    failure = 0

    def _one(rel_dest: Path, f) -> tuple[bool, str | None]:
        dest = experiment_root / rel_dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            f.get(str(dest))
            return True, None
        except Exception as e:
            return False, f"ERROR downloading {dest}: {e}"

    def _handle(ok: bool, err: str | None) -> None:
        nonlocal success, failure
        if ok:
            success += 1
        else:
            failure += 1
            if err and on_error is not None:
                on_error(err)
        if on_file_complete is not None:
            on_file_complete(ok)

    if n_parallel <= 1:
        for rel_dest, f in files:
            ok, err = _one(rel_dest, f)
            _handle(ok, err)
    else:
        with ThreadPoolExecutor(max_workers=n_parallel) as ex:
            futures = [ex.submit(_one, rel, f) for rel, f in files]
            for fut in as_completed(futures):
                ok, err = fut.result()
                _handle(ok, err)

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
    project: str,
    subject: str,
    experiment: str,
    output_dir: Path,
    n_parallel_files: int,
    progress: _ProgressDisplay | None = None,
) -> str:
    label = f"{project}/{subject}/{experiment}"
    try:
        exp_obj = (
            interface.select.project(project)
            .subject(subject)
            .experiment(experiment)
        )
        if not exp_obj.exists():
            return STATUS_NONEXISTENT
        files = _enumerate_files(exp_obj)
    except Exception as e:
        msg = f"Error walking {label}: {e}"
        if progress is not None:
            progress.print_above(msg)
        else:
            _safe_print(msg)
        return STATUS_FAILURE

    if not files:
        return STATUS_EMPTY

    experiment_root = output_dir / project / subject / experiment

    slot = progress.acquire(label, len(files)) if progress is not None else -1
    done = [0]
    done_lock = threading.Lock()

    def _on_complete(_ok: bool) -> None:
        with done_lock:
            done[0] += 1
            local_done = done[0]
        if progress is not None:
            progress.update(slot, local_done)

    def _on_error(msg: str) -> None:
        if progress is not None:
            progress.print_above(f"  {msg}")
        else:
            _safe_print(f"  {msg}")

    success, failure = _download_files(
        files, experiment_root, n_parallel_files, _on_complete, _on_error
    )
    status = _classify_status(len(files), success, failure)
    if progress is not None:
        progress.release(slot, status)
    return status


def _read_csv_rows(path: Path) -> list[tuple[str, str, str]]:
    if not path.exists():
        sys.exit(f"Error: input CSV not found: {path}")
    rows: list[tuple[str, str, str]] = []
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        required = {"PROJECT", "SUBJECT_LABEL", "EXPERIMENT_LABEL"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            sys.exit(
                f"Error: input CSV {path} must have columns "
                "PROJECT, SUBJECT_LABEL, EXPERIMENT_LABEL."
            )
        for i, row in enumerate(reader, start=2):
            p = (row.get("PROJECT") or "").strip()
            s = (row.get("SUBJECT_LABEL") or "").strip()
            e = (row.get("EXPERIMENT_LABEL") or "").strip()
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
    project: str,
    subject: str,
    experiment: str,
    output_dir: Path,
    n_parallel_files: int,
    log_writer: _LogWriter,
) -> str:
    progress = _ProgressDisplay(1)
    iface = Interface(server=server, user=user, password=password)
    try:
        start = _logging_now()
        status = _process_experiment(
            iface,
            project,
            subject,
            experiment,
            output_dir,
            n_parallel_files,
            progress,
        )
    finally:
        try:
            iface.disconnect()
        except Exception:
            pass
        progress.close()
    log_writer.write(start, project, subject, experiment, status)
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

    n_slots = max(1, n_parallel_experiments)
    progress = _ProgressDisplay(n_slots)

    def _worker(triplet: tuple[str, str, str]) -> str:
        p, s, e = triplet
        iface = _get_thread_interface(server, user, password)
        start = _logging_now()
        status = _process_experiment(iface, p, s, e, output_dir, 1, progress)
        log_writer.write(start, p, s, e, status)
        return status

    try:
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
    finally:
        progress.close()

    return counts


def download_cmd(args: argparse.Namespace) -> int:
    if args.ndownload < 1:
        sys.exit("Error: -n/--ndownload must be >= 1.")

    server, user, password = load_credentials()
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    log_path: Path | None = None
    if args.log:
        while True:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = output_dir / "log" / f"download_{ts}_log.csv"
            if not log_path.exists():
                break
            time.sleep(1)
    log_writer = _LogWriter(log_path)

    if args.triplet is not None:
        project, subject, experiment = args.triplet
        status = _run_single(
            server,
            user,
            password,
            project,
            subject,
            experiment,
            output_dir,
            args.ndownload,
            log_writer,
        )
        print(
            f"Status for {project}/{subject}/{experiment}: {status}"
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
