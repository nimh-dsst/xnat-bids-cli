import argparse
import csv
import shutil
import sys
import threading
import time
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests
from pyxnat import Interface
from pyxnat.core import downloadutils

from .archive import (
    OK_STATUSES as ARCHIVE_OK_STATUSES,
    archive_experiment,
    delete_experiment_dir,
)
from .login import load_credentials

STATUS_COMPLETE = "COMPLETE"
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


def _describe_download_error(e: Exception) -> str:
    """Return a clear message for an exception raised during a zip download.

    pyxnat's own zip-download code (``downloadutils.download`` and
    ``resources.CObject.download``) wraps ``response.iter_content()`` in a
    bare ``except Exception as e: sys.stderr.write(e)``. Since ``write()``
    requires a ``str``, that line itself raises a ``TypeError`` that masks
    whatever actually broke the download (almost always a
    ``requests.exceptions.ChunkedEncodingError`` from the server or a proxy
    dropping the connection mid-transfer). Unwrap that TypeError's context
    to surface the real cause instead of the confusing "write() argument
    must be str" message.
    """
    context = e.__context__
    if isinstance(e, TypeError) and isinstance(context, requests.exceptions.RequestException):
        return (
            f"connection dropped during zip download ({context}); "
            "this is usually a transient network/server timeout, try again"
        )
    if isinstance(e, requests.exceptions.RequestException):
        return (
            f"connection dropped during zip download ({e}); "
            "this is usually a transient network/server timeout, try again"
        )
    return str(e)


def _human_bytes(n: float) -> str:
    """Format a byte count for display, e.g. ``1536`` -> ``"1.5 KB"``."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0:
            return f"{n:3.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} PB"


class _ExperimentProgress:
    """Tracks bytes downloaded so far for one experiment's zip transfers.

    XNAT's zip-export endpoint is fetched as two sequential whole-archive
    downloads (scans, then session-level resources); each one is written
    directly to its final path with incremental flushing, so at most one
    in-progress zip exists under the experiment directory at a time. This
    tracks the completed phase's byte count plus whatever is currently
    on disk, so reported progress climbs across both phases instead of
    resetting to zero when the scans zip is extracted and deleted.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._completed_bytes = 0

    def add_completed(self, n: int) -> None:
        with self._lock:
            self._completed_bytes += n

    def current_bytes(self, experiment_root: Path) -> int:
        in_flight = 0
        try:
            for p in experiment_root.glob("*.zip"):
                try:
                    in_flight += p.stat().st_size
                except OSError:
                    continue
        except OSError:
            pass
        with self._lock:
            return self._completed_bytes + in_flight


def _report_progress(
    label: str,
    experiment_root: Path,
    estimated_total: int | None,
    progress: _ExperimentProgress,
    stop_event: threading.Event,
    interval: float = 5.0,
) -> None:
    """Print a periodic download-progress line for one experiment until stopped.

    Runs in its own thread, polling every `interval` seconds; one such
    thread runs per experiment currently downloading, so under `-n` each
    active worker prints its own interleaved status lines.
    """
    while not stop_event.wait(interval):
        downloaded = progress.current_bytes(experiment_root)
        if estimated_total:
            pct = min(100.0, downloaded / estimated_total * 100)
            _safe_print(
                f"  [{label}] {pct:5.1f}% "
                f"({_human_bytes(downloaded)} / {_human_bytes(estimated_total)} est.)"
            )
        else:
            _safe_print(f"  [{label}] {_human_bytes(downloaded)} downloaded")


def _zip_wrapper_prefix(names: list[str]) -> str:
    """Return the shared top-level path segment across all zip members, if any.

    XNAT's zip export nests every entry under a single wrapper directory
    (typically named after the experiment), which would otherwise reproduce
    an identically-named EXPERIMENT/EXPERIMENT folder on disk. Detecting it
    by shared prefix rather than a hardcoded name keeps this robust to
    whatever XNAT actually calls it.
    """
    segments = {n.split("/", 1)[0] for n in names if "/" in n and n.split("/", 1)[0]}
    if len(segments) == 1:
        return next(iter(segments)) + "/"
    return ""


def _extract_zip_flattened(zip_path: Path, dest_dir: Path) -> None:
    """Extract zip_path into dest_dir, stripping any shared wrapper directory."""
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        prefix = _zip_wrapper_prefix(names)
        for name in names:
            if name.endswith("/"):
                continue  # directory entry
            rel = name[len(prefix):] if prefix and name.startswith(prefix) else name
            if not rel:
                continue
            target = dest_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    zip_path.unlink(missing_ok=True)


def _process_experiment(
    interface: Interface,
    project: str,
    subject: str,
    experiment: str,
    output_dir: Path,
    report: Callable[[str], None],
    progress: _ExperimentProgress | None = None,
) -> str:
    """Download one experiment as whole-experiment zip archives.

    Two bulk requests are made against XNAT's REST zip-export endpoint (one
    for scans, one for session-level resources) via pyxnat, rather than one
    HTTP request per file. Each zip is flattened into the experiment's output
    directory (stripping XNAT's own wrapper folder, see
    ``_extract_zip_flattened``), so the on-disk layout follows XNAT's own
    scan/resource folder naming without an extra EXPERIMENT/EXPERIMENT level.
    """
    label = f"{project}/{subject}/{experiment}"
    try:
        exp_obj = (
            interface.select.project(project)
            .subject(subject)
            .experiment(experiment)
        )
        if not exp_obj.exists():
            return STATUS_NONEXISTENT
    except Exception as e:
        report(f"Error looking up {label}: {e}")
        return STATUS_FAILURE

    experiment_root = output_dir / project / subject / experiment
    experiment_root.mkdir(parents=True, exist_ok=True)

    got_scans = False
    got_resources = False
    failed = False

    try:
        zip_path = Path(exp_obj.scans().download(str(experiment_root), extract=False))
        if progress is not None:
            progress.add_completed(zip_path.stat().st_size)
        _extract_zip_flattened(zip_path, experiment_root)
        got_scans = True
    except LookupError:
        pass  # no scans on this experiment
    except Exception as e:
        report(f"  Error downloading scans for {label}: {_describe_download_error(e)}")
        failed = True

    try:
        zip_path = Path(downloadutils.download(
            str(experiment_root), exp_obj.resources(), extract=False
        ))
        if progress is not None:
            progress.add_completed(zip_path.stat().st_size)
        _extract_zip_flattened(zip_path, experiment_root)
        got_resources = True
    except LookupError:
        pass  # no session-level resources on this experiment
    except Exception as e:
        report(f"  Error downloading resources for {label}: {_describe_download_error(e)}")
        failed = True

    if failed:
        return STATUS_FAILURE
    if not got_scans and not got_resources:
        return STATUS_EMPTY
    return STATUS_COMPLETE


def _read_csv_rows(path: Path) -> list[tuple[str, str, str, int | None]]:
    if not path.exists():
        sys.exit(f"Error: input CSV not found: {path}")
    rows: list[tuple[str, str, str, int | None]] = []
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
            raw_size = (row.get("ESTIMATED_SIZE_BYTES") or "").strip()
            estimated_size: int | None = None
            if raw_size:
                try:
                    estimated_size = int(raw_size)
                except ValueError:
                    estimated_size = None
            rows.append((p, s, e, estimated_size))
    return rows


def _archive_and_maybe_delete(
    output_dir: Path,
    project: str,
    subject: str,
    experiment: str,
    do_archive: bool,
    do_delete: bool,
    report: Callable[[str], None],
) -> None:
    if not do_archive:
        return
    label = f"{project}/{subject}/{experiment}"
    a_status, a_detail = archive_experiment(
        output_dir, project, subject, experiment
    )
    line = f"  archive {label}: {a_status}"
    if a_detail:
        line += f" — {a_detail}"
    report(line)
    if do_delete and a_status in ARCHIVE_OK_STATUSES:
        delete_experiment_dir(output_dir, project, subject, experiment)


def _run_single(
    server: str,
    user: str,
    password: str,
    project: str,
    subject: str,
    experiment: str,
    output_dir: Path,
    log_writer: _LogWriter,
    do_archive: bool,
    do_delete: bool,
) -> str:
    iface = Interface(server=server, user=user, password=password)
    try:
        start = _logging_now()
        status = _process_experiment(
            iface, project, subject, experiment, output_dir, _safe_print
        )
    finally:
        try:
            iface.disconnect()
        except Exception:
            pass
    log_writer.write(start, project, subject, experiment, status)
    _archive_and_maybe_delete(
        output_dir,
        project,
        subject,
        experiment,
        do_archive,
        do_delete,
        _safe_print,
    )
    return status


def _run_csv(
    server: str,
    user: str,
    password: str,
    rows: list[tuple[str, str, str, int | None]],
    output_dir: Path,
    n_parallel_experiments: int,
    log_writer: _LogWriter,
    do_archive: bool,
    do_delete: bool,
) -> dict[str, int]:
    counts = {
        STATUS_COMPLETE: 0,
        STATUS_FAILURE: 0,
        STATUS_NONEXISTENT: 0,
        STATUS_EMPTY: 0,
    }

    def _worker(row: tuple[str, str, str, int | None]) -> str:
        p, s, e, estimated_size = row
        iface = _get_thread_interface(server, user, password)
        label = f"{p}/{s}/{e}"
        experiment_root = output_dir / p / s / e
        progress = _ExperimentProgress()
        stop_event = threading.Event()
        monitor = threading.Thread(
            target=_report_progress,
            args=(label, experiment_root, estimated_size, progress, stop_event),
            daemon=True,
        )
        monitor.start()
        try:
            start = _logging_now()
            status = _process_experiment(
                iface, p, s, e, output_dir, _safe_print, progress
            )
        finally:
            stop_event.set()
            monitor.join()
        log_writer.write(start, p, s, e, status)
        _archive_and_maybe_delete(
            output_dir, p, s, e, do_archive, do_delete, _safe_print
        )
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
    if args.triplet is not None and args.ndownload != 1:
        sys.exit(
            "Error: -n/--ndownload only applies to --csv/--input downloads."
        )
    if args.delete and not args.archive:
        sys.exit("Error: -d/--delete requires -a/--archive.")

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
            log_writer,
            args.archive,
            args.delete,
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
        args.archive,
        args.delete,
    )
    total = sum(counts.values())
    print(f"\nProcessed {total} experiment(s):")
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
