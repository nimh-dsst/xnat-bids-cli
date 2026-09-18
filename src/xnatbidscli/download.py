import argparse
import csv
import os
import re
import shutil
import sys
import threading
import time
import zipfile
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote

import requests
from pyxnat import Interface
from pyxnat.core import uriutil

from .archive import (
    OK_STATUSES as ARCHIVE_OK_STATUSES,
    archive_experiment,
    delete_experiment_dir,
)
from .login import load_credentials
from .sysinfo import get_system_username

STATUS_COMPLETE = "COMPLETE"
STATUS_FAILURE = "FAILURE"
STATUS_NONEXISTENT = "NONEXISTENT"
STATUS_EMPTY = "EMPTY"

_OK_STATUSES = {STATUS_COMPLETE, STATUS_EMPTY}

_thread_iface = threading.local()


def _enable_windows_ansi() -> None:
    """Best-effort: turn on ANSI escape processing in legacy Windows consoles.

    Windows Terminal and modern PowerShell already interpret these
    sequences; this only matters for cmd.exe/conhost, which needs the
    ENABLE_VIRTUAL_TERMINAL_PROCESSING console mode flag set explicitly.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # VT processing
    except Exception:
        pass


class _ProgressBoard:
    """Keeps each active experiment's download-progress line updating in place.

    Backed by ANSI cursor-movement escapes so `-n`'s concurrent downloads
    each get one persistently-updating line instead of a fresh line every
    interval. Falls back to plain sequential prints when stdout isn't a
    terminal (e.g. redirected to a file), since in-place redraws wouldn't
    render there anyway.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._lines: dict[str, str] = {}
        self._rendered = 0
        self._enabled = sys.stdout.isatty()
        if self._enabled:
            _enable_windows_ansi()

    def update(self, label: str, text: str) -> None:
        """Set (or add) `label`'s line to `text` and repaint the board."""
        with self._lock:
            if not self._enabled:
                print(text)
                return
            self._lines[label] = text
            self._redraw()

    def finish(self, label: str) -> None:
        """Remove `label`'s line once that experiment stops downloading."""
        with self._lock:
            if self._lines.pop(label, None) is not None and self._enabled:
                self._redraw()

    def log(self, msg: str) -> None:
        """Print a normal message above the progress block, then redraw it."""
        with self._lock:
            if not self._enabled:
                print(msg)
                return
            if self._rendered:
                sys.stdout.write(f"\x1b[{self._rendered}A\x1b[0J")
            sys.stdout.write(msg + "\n")
            self._rendered = 0
            self._redraw()

    def _redraw(self) -> None:
        """Repaint the progress block in place. Caller holds `self._lock`."""
        if self._rendered:
            sys.stdout.write(f"\x1b[{self._rendered}A")
        for text in self._lines.values():
            sys.stdout.write(f"\r\x1b[K{text}\n")
        extra = self._rendered - len(self._lines)
        if extra > 0:
            for _ in range(extra):
                sys.stdout.write("\x1b[K\n")
            sys.stdout.write(f"\x1b[{extra}A")
        self._rendered = len(self._lines)
        sys.stdout.flush()


_board = _ProgressBoard()


def _safe_print(msg: str) -> None:
    _board.log(msg)


def _logging_now() -> str:
    # Matches logging module's default %(asctime)s: "YYYY-MM-DD HH:MM:SS,mmm"
    now = datetime.now()
    return f"{now.strftime('%Y-%m-%d %H:%M:%S')},{now.microsecond // 1000:03d}"


class _LogWriter:
    def __init__(self, path: Path | None):
        self._path = path
        self._lock = threading.Lock()
        self._user = get_system_username()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", newline="") as f:
                csv.writer(f).writerow(
                    [
                        "DATESTAMP",
                        "USER",
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
                [datestamp, self._user, project, subject, experiment, status]
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
    """Keep one download-progress line for this experiment updating in place.

    Runs in its own thread, polling every `interval` seconds; one such
    thread runs per experiment currently downloading, so under `-n` each
    active worker keeps its own persistent line via `_board` (or, outside a
    terminal, its own sequence of plain printed lines).
    """
    try:
        while not stop_event.wait(interval):
            downloaded = progress.current_bytes(experiment_root)
            if estimated_total:
                pct = min(100.0, downloaded / estimated_total * 100)
                text = (
                    f"  [{label}] {pct:5.1f}% "
                    f"({_human_bytes(downloaded)} / {_human_bytes(estimated_total)} est.)"
                )
            else:
                text = f"  [{label}] {_human_bytes(downloaded)} downloaded"
            _board.update(label, text)
    finally:
        _board.finish(label)


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


_CONTENT_DISPOSITION_FILENAME_RE = re.compile(
    r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', re.IGNORECASE
)


def _filename_from_content_disposition(header: str) -> str | None:
    """Pull a bare filename out of a Content-Disposition header, if present."""
    match = _CONTENT_DISPOSITION_FILENAME_RE.search(header)
    if not match:
        return None
    # unquote handles the percent-encoding an RFC 5987 filename* uses;
    # Path(...).name strips any directory components a server might send.
    name = Path(unquote(match.group(1).strip())).name
    return name or None


def _resource_identifier(resource) -> str:
    """A filesystem-safe identifier for a pyxnat Resource, from its own URI."""
    return Path(uriutil.uri_last(resource._uri)).name or "unknown"


def _download_single_resource_zip(resource, dest_dir: Path) -> tuple[Path, str]:
    """Download one named session-level resource as a zip archive.

    Mirrors pyxnat's own ``Resource.get()`` (``.../resources/{ID}/files?format=zip``)
    — the per-resource download XNAT actually supports; see ``_download_resources``
    for why there's no bulk equivalent — but keeps the response's
    ``Content-Disposition`` header around afterward for
    ``_extract_zip_flattened_or_rescue``.
    """
    url = resource._uri + "/files?format=zip"
    response = resource._intf.get(url, stream=True)
    try:
        if not response.ok:
            raise RuntimeError(f"HTTP {response.status_code} {response.reason}")
        content_disposition = response.headers.get("Content-Disposition", "")
        zip_path = dest_dir / f"resource_{_resource_identifier(resource)}.zip"
        with zip_path.open("wb") as f:
            count = 0
            for chunk in response.iter_content(chunk_size=1024):
                if chunk:
                    f.write(chunk)
                    count += 1
                    if count % 10 == 0:
                        f.flush()
            f.flush()
    finally:
        response.close()
    return zip_path, content_disposition


def _download_resources(
    exp_obj, dest_dir: Path, progress: "_ExperimentProgress | None"
) -> None:
    """Download and extract every session-level resource for an experiment.

    XNAT has no bulk "download every resource at once" endpoint the way scans
    do (``.../scans/ALL/files?format=zip``, where ``ALL`` is a scan-*type*
    wildcard); the ``/resources`` collection has no ``.download()``/``.get()``
    method in pyxnat at all — only the individual ``Resource`` element class
    does, one resource at a time. This mirrors that real, working pattern via
    ``_download_single_resource_zip``, and keeps going past one resource's
    failure so a single bad resource doesn't lose the rest.

    Raises LookupError if there are no session-level resources at all, or an
    Exception summarizing every resource that failed, raised only after every
    resource has been attempted (so successful ones still land on disk).
    """
    resources = list(exp_obj.resources())
    if not resources:
        raise LookupError("There are no resources to download")

    errors: list[str] = []
    for resource in resources:
        try:
            zip_path, content_disposition = _download_single_resource_zip(
                resource, dest_dir
            )
            if progress is not None:
                progress.add_completed(zip_path.stat().st_size)
            _extract_zip_flattened_or_rescue(zip_path, dest_dir, content_disposition)
        except Exception as e:
            errors.append(
                f"resource '{_resource_identifier(resource)}': "
                f"{_describe_download_error(e)}"
            )

    if errors:
        raise RuntimeError("; ".join(errors))


def _extract_zip_flattened_or_rescue(
    zip_path: Path, dest_dir: Path, content_disposition: str
) -> None:
    """Extract zip_path into dest_dir, rescuing XNAT's single-file quirk.

    XNAT's zip-export endpoint is meant to always return a zip archive, but
    when a session's resources resolve to exactly one file it sometimes
    streams that file directly instead (a server-side behavior, not
    specific to any one experiment). When the downloaded bytes don't open as
    a zip, save them as that one file — named from the response's
    Content-Disposition header, falling back to the temp file's own name —
    instead of failing the whole experiment.
    """
    try:
        _extract_zip_flattened(zip_path, dest_dir)
    except zipfile.BadZipFile:
        filename = _filename_from_content_disposition(content_disposition) or zip_path.stem
        zip_path.replace(dest_dir / filename)


def _process_experiment(
    interface: Interface,
    project: str,
    subject: str,
    experiment: str,
    output_dir: Path,
    report: Callable[[str], None],
    progress: _ExperimentProgress | None = None,
    local_subject: str | None = None,
    local_experiment: str | None = None,
) -> str:
    """Download one experiment as whole-experiment zip archives.

    Bulk requests are made against XNAT's REST zip-export endpoint via
    pyxnat, rather than one HTTP request per file: one for all scans, and
    one per session-level resource (see ``_download_resources`` for why
    resources can't be fetched in a single bulk request the way scans can).
    Each zip is flattened into the experiment's output directory (stripping
    XNAT's own wrapper folder, see ``_extract_zip_flattened``), so the
    on-disk layout follows XNAT's own scan/resource folder naming without an
    extra EXPERIMENT/EXPERIMENT level.

    `local_subject`/`local_experiment` (from `SUBJECT_BIDS_RENAME`/
    `EXPERIMENT_BIDS_RENAME`) name the on-disk directory when they differ
    from `subject`/`experiment`, which are always used to look up the
    experiment on the XNAT server itself.
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

    experiment_root = (
        output_dir / project / (local_subject or subject) / (local_experiment or experiment)
    )
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
        _download_resources(exp_obj, experiment_root, progress)
        got_resources = True
    except LookupError:
        pass  # no session-level resources on this experiment
    except Exception as e:
        report(f"  Error downloading resources for {label}: {e}")
        failed = True

    if failed:
        return STATUS_FAILURE
    if not got_scans and not got_resources:
        return STATUS_EMPTY
    return STATUS_COMPLETE


def _format_bids_rename(value: str, prefix: str) -> str | None:
    """Normalize a *_BIDS_RENAME cell, prepending `prefix` if not already present.

    Returns None if the value (after stripping an already-present prefix) is
    not purely alphanumeric, signaling an invalid rename value.
    """
    remainder = value[len(prefix):] if value.startswith(prefix) else value
    if not remainder.isalnum():
        return None
    return value if value.startswith(prefix) else prefix + value


def _read_csv_rows(
    path: Path,
) -> list[tuple[str, str, str, int | None, str | None, str | None]]:
    if not path.exists():
        sys.exit(f"Error: input CSV not found: {path}")
    rows: list[tuple[str, str, str, int | None, str | None, str | None]] = []
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

            subject_rename: str | None = None
            raw_subject_rename = (row.get("SUBJECT_BIDS_RENAME") or "").strip()
            if raw_subject_rename:
                subject_rename = _format_bids_rename(raw_subject_rename, "sub-")
                if subject_rename is None:
                    sys.exit(
                        f"Error: row {i} of {path} has an invalid "
                        f"SUBJECT_BIDS_RENAME value '{raw_subject_rename}': "
                        "must be alphanumeric only (after an optional "
                        "'sub-' prefix)."
                    )

            experiment_rename: str | None = None
            raw_experiment_rename = (row.get("EXPERIMENT_BIDS_RENAME") or "").strip()
            if raw_experiment_rename:
                experiment_rename = _format_bids_rename(raw_experiment_rename, "ses-")
                if experiment_rename is None:
                    sys.exit(
                        f"Error: row {i} of {path} has an invalid "
                        f"EXPERIMENT_BIDS_RENAME value '{raw_experiment_rename}': "
                        "must be alphanumeric only (after an optional "
                        "'ses-' prefix)."
                    )

            rows.append((p, s, e, estimated_size, subject_rename, experiment_rename))
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
    local_subject: str | None = None,
    local_experiment: str | None = None,
) -> str:
    local_s = local_subject or subject
    local_e = local_experiment or experiment
    iface = Interface(server=server, user=user, password=password)
    try:
        start = _logging_now()
        status = _process_experiment(
            iface, project, subject, experiment, output_dir, _safe_print,
            local_subject=local_s, local_experiment=local_e,
        )
    finally:
        try:
            iface.disconnect()
        except Exception:
            pass
    log_writer.write(start, project, local_s, local_e, status)
    _archive_and_maybe_delete(
        output_dir,
        project,
        local_s,
        local_e,
        do_archive,
        do_delete,
        _safe_print,
    )
    return status


def _run_csv(
    server: str,
    user: str,
    password: str,
    rows: list[tuple[str, str, str, int | None, str | None, str | None]],
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

    def _worker(row: tuple[str, str, str, int | None, str | None, str | None]) -> str:
        p, s, e, estimated_size, subject_rename, experiment_rename = row
        local_s = subject_rename or s
        local_e = experiment_rename or e
        iface = _get_thread_interface(server, user, password)
        label = f"{p}/{s}/{e}"
        experiment_root = output_dir / p / local_s / local_e
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
                iface, p, s, e, output_dir, _safe_print, progress,
                local_s, local_e,
            )
        finally:
            stop_event.set()
            monitor.join()
        log_writer.write(start, p, local_s, local_e, status)
        _archive_and_maybe_delete(
            output_dir, p, local_s, local_e, do_archive, do_delete, _safe_print
        )
        return status

    if n_parallel_experiments <= 1:
        try:
            for triplet in rows:
                counts[_worker(triplet)] += 1
        except KeyboardInterrupt:
            _board.log("\nInterrupted: stopping before starting the next experiment.")
            sys.stdout.flush()
            sys.exit(130)
        finally:
            _close_thread_interface()
    else:
        ex = ThreadPoolExecutor(max_workers=n_parallel_experiments)
        pending = {ex.submit(_worker, t) for t in rows}
        try:
            while pending:
                # A short timeout (rather than an unbounded wait) hands control
                # back to the interpreter every 0.5s, which is what lets a
                # pending Ctrl+C actually get raised here instead of sitting
                # queued until the whole batch finishes.
                done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
                for fut in done:
                    counts[fut.result()] += 1
        except KeyboardInterrupt:
            still_running = sum(1 for fut in pending if fut.running())
            queued = len(pending) - still_running
            for fut in pending:
                fut.cancel()  # No-op for already-running futures.
            _board.log(
                f"\nInterrupted: {queued} queued download(s) cancelled; "
                f"{still_running} already in progress were abandoned "
                "mid-transfer (their output may be incomplete)."
            )
            sys.stdout.flush()
            # ThreadPoolExecutor's worker threads are non-daemon, and CPython
            # joins non-daemon threads on interpreter shutdown regardless of
            # sys.exit()/exceptions — which is exactly what made Ctrl+C
            # appear to do nothing while downloads already in flight kept
            # running. os._exit() skips that join entirely.
            os._exit(130)
        ex.shutdown(wait=True)
        # Worker threads' Interface objects are GC'd when the pool shuts down.

    return counts


def _resolve_accession(
    interface: Interface, accession: str
) -> tuple[str, str, str, str | None]:
    """Resolve a unique XNAT accession number to its project/subject/experiment.

    Tries `accession` as a subject ID, then as an experiment ID — XNAT IDs
    are unique across the whole server regardless of datatype, so no
    project needs to be known up front. pyxnat has no built-in server-wide
    subject lookup (only ``select.project()``/``select.experiment()`` are
    exposed at the root), so this reaches XNAT's root-level ``/subjects``
    and ``/experiments`` listings directly via ``interface._get_json``, the
    same primitive used elsewhere in this module for endpoints pyxnat
    doesn't wrap.

    Parameters
    ----------
    interface : Interface
        Connected pyxnat interface.
    accession : str
        The unique XNAT ID to resolve. Not a label — labels are only
        unique within their parent (subject labels within a project,
        experiment labels within a subject), not server-wide.

    Returns
    -------
    tuple[str, str, str, str | None]
        ``("subject", project, subject_id, None)`` or
        ``("experiment", project, subject_id, experiment_id)``.
    """
    interface._get_entry_point()
    encoded = quote(accession, safe="")

    try:
        rows = interface._get_json(
            f"{interface._entry}/subjects?ID={encoded}&columns=ID,project&format=json"
        )
        if rows:
            return ("subject", rows[0]["project"], rows[0]["ID"], None)

        rows = interface._get_json(
            f"{interface._entry}/experiments?ID={encoded}"
            "&columns=ID,project,subject_ID&format=json"
        )
        if rows:
            return (
                "experiment", rows[0]["project"], rows[0]["subject_ID"], rows[0]["ID"]
            )
    except Exception as e:
        sys.exit(f"Error: could not resolve accession '{accession}': {e}")

    sys.exit(
        f"Error: accession '{accession}' not found as either a subject or "
        "an experiment on the configured server."
    )


def _download_accession(
    server: str,
    user: str,
    password: str,
    accession: str,
    output_dir: Path,
    log_writer: _LogWriter,
    do_archive: bool,
    do_delete: bool,
    n_parallel_experiments: int,
    local_subject: str | None,
    local_experiment: str | None,
    log_path: Path | None,
) -> int:
    """Handle `download --accession`: resolve it, then delegate like -1/--csv.

    A subject accession downloads every experiment belonging to that
    subject (like `--csv` batch mode); an experiment accession downloads
    just that one experiment (like `-1`).
    """
    interface = Interface(server=server, user=user, password=password)
    try:
        kind, project, subject_id, experiment_id = _resolve_accession(
            interface, accession
        )
    finally:
        try:
            interface.disconnect()
        except Exception:
            pass

    if kind == "subject":
        if local_experiment is not None:
            sys.exit(
                f"Error: --rename-experiment cannot be used with subject "
                f"accession '{accession}', since a subject may have more "
                "than one experiment."
            )
        interface = Interface(server=server, user=user, password=password)
        try:
            subj_obj = interface.select.project(project).subject(subject_id)
            experiment_labels = [e.label() for e in subj_obj.experiments()]
        except Exception as e:
            sys.exit(
                f"Error: could not list experiments for subject accession "
                f"'{accession}': {e}"
            )
        finally:
            try:
                interface.disconnect()
            except Exception:
                pass
        if not experiment_labels:
            sys.exit(
                f"Error: subject accession '{accession}' has no experiments "
                "to download."
            )
        rows = [
            (project, subject_id, label, None, local_subject, None)
            for label in experiment_labels
        ]
        counts = _run_csv(
            server, user, password, rows, output_dir, n_parallel_experiments,
            log_writer, do_archive, do_delete,
        )
        total = sum(counts.values())
        print(f"\nProcessed {total} experiment(s) for subject accession {accession}:")
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

    status = _run_single(
        server, user, password, project, subject_id, experiment_id,
        output_dir, log_writer, do_archive, do_delete,
        local_subject, local_experiment,
    )
    print(
        f"Status for accession {accession} "
        f"({project}/{subject_id}/{experiment_id}): {status}"
    )
    if log_path is not None:
        print(f"Log written to {log_path}")
    return 0 if status in _OK_STATUSES else 1


def download_cmd(args: argparse.Namespace) -> int:
    if args.ndownload < 1:
        sys.exit("Error: -n/--ndownload must be >= 1.")
    if args.triplet is not None and args.ndownload != 1:
        sys.exit(
            "Error: -n/--ndownload only applies to --csv/--input or a "
            "subject --accession."
        )
    if args.delete and not args.archive:
        sys.exit("Error: -d/--delete requires -a/--archive.")
    if (
        args.triplet is None
        and args.accession is None
        and (args.rename_subject or args.rename_experiment)
    ):
        sys.exit(
            "Error: --rename-subject/--rename-experiment only apply to "
            "-1 or --accession downloads."
        )

    local_subject: str | None = None
    if args.rename_subject:
        local_subject = _format_bids_rename(args.rename_subject, "sub-")
        if local_subject is None:
            sys.exit(
                f"Error: invalid --rename-subject value '{args.rename_subject}': "
                "must be alphanumeric only (after an optional 'sub-' prefix)."
            )

    local_experiment: str | None = None
    if args.rename_experiment:
        local_experiment = _format_bids_rename(args.rename_experiment, "ses-")
        if local_experiment is None:
            sys.exit(
                f"Error: invalid --rename-experiment value "
                f"'{args.rename_experiment}': must be alphanumeric only "
                "(after an optional 'ses-' prefix)."
            )

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
            local_subject,
            local_experiment,
        )
        print(
            f"Status for {project}/{subject}/{experiment}: {status}"
        )
        if log_path is not None:
            print(f"Log written to {log_path}")
        return 0 if status in _OK_STATUSES else 1

    if args.accession is not None:
        return _download_accession(
            server,
            user,
            password,
            args.accession,
            output_dir,
            log_writer,
            args.archive,
            args.delete,
            args.ndownload,
            local_subject,
            local_experiment,
            log_path,
        )

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
