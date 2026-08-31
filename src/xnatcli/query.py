import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

from pyxnat import Interface

from .login import load_credentials


def _experiment_date_yyyymmdd(exp_obj) -> str:
    try:
        raw = exp_obj.attrs.get("date")
    except Exception:
        return ""
    if not raw:
        return ""
    try:
        return datetime.strptime(str(raw).strip(), "%Y-%m-%d").strftime("%Y%m%d")
    except ValueError:
        return ""


# ESTIMATED_SIZE_BYTES values for an experiment that has files but where
# none of them yielded a usable size, so a bare "0" would be indistinguishable
# from a genuinely empty experiment.
SIZE_FILES_WITH_UNLABELED_SIZE = "FILES_WITH_UNLABELED_SIZE"
SIZE_UNPARSEABLE_SIZE_VALUES = "UNPARSEABLE_SIZE_VALUES"


def _experiment_size_bytes(interface: Interface, exp_obj, label: str) -> int | str | None:
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
    int | str | None
        Total size in bytes when at least one file contributed a usable
        size, or when the experiment genuinely has zero files (a real
        ``0``). ``None`` if the size could not be determined at all (the
        ``/files`` request itself failed) — written as a blank CSV cell.
        Otherwise one of two categorical strings, for an experiment that
        has files but where none of them summed to anything:
        ``SIZE_UNPARSEABLE_SIZE_VALUES`` if any file's ``Size`` value was
        non-numeric (takes priority, since it signals a data anomaly
        rather than a merely-missing value), else
        ``SIZE_FILES_WITH_UNLABELED_SIZE`` if every file's ``Size`` field
        was missing/empty.
    """
    try:
        rows = interface._get_json(f"{exp_obj._uri}/files?format=json")
    except Exception as e:
        print(f"Warning: could not determine size for {label}: {e}", file=sys.stderr)
        return None

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

    if total > 0 or not rows:
        return total
    if has_unparseable:
        return SIZE_UNPARSEABLE_SIZE_VALUES
    if has_unlabeled:
        return SIZE_FILES_WITH_UNLABELED_SIZE
    return total


def _collect_rows(
    interface: Interface,
    project: str,
    subject: str | None,
) -> list[tuple[str, str, str, str, str, str, int | str | None]]:
    proj_obj = interface.select.project(project)
    if not proj_obj.exists():
        sys.exit(
            f"Error: project '{project}' not found on the configured server."
        )
    canonical_project = proj_obj.id()

    rows: list[tuple[str, str, str, str, str, str, int | str | None]] = []

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
        for exp_obj in subj_obj.experiments():
            exp_label = exp_obj.label()
            rows.append((
                canonical_project,
                subj_label,
                subj_id,
                exp_label,
                exp_obj.id(),
                _experiment_date_yyyymmdd(exp_obj),
                _experiment_size_bytes(
                    interface, exp_obj, f"{subj_label}/{exp_label}"
                ),
            ))

    rows.sort(key=lambda row: (row[1], row[3]))

    return rows


def query_cmd(args: argparse.Namespace) -> int:
    server, username, password = load_credentials()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.subject is None:
        filename = f"PROJECT-{args.project}.csv"
    else:
        filename = f"PROJECT-{args.project}_SUBJECT-{args.subject}.csv"
    output_path = output_dir / filename

    interface = None
    try:
        interface = Interface(server=server, user=username, password=password)
        rows = _collect_rows(interface, args.project, args.subject)
    except SystemExit:
        raise
    except Exception as e:
        sys.exit(f"Error: query failed: {e}")
    finally:
        if interface is not None:
            try:
                interface.disconnect()
            except Exception:
                pass

    with output_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "PROJECT",
            "SUBJECT_LABEL",
            "SUBJECT_ID",
            "EXPERIMENT_LABEL",
            "EXPERIMENT_ID",
            "EXPERIMENT_DATE",
            "ESTIMATED_SIZE_BYTES",
        ])
        writer.writerows(rows)

    print(f"Wrote {len(rows)} row(s) to {output_path}")
    return 0
