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


def _collect_rows(
    interface: Interface,
    project: str,
    subject: str | None,
) -> list[tuple[str, str, str, str, str, str]]:
    proj_obj = interface.select.project(project)
    if not proj_obj.exists():
        sys.exit(
            f"Error: project '{project}' not found on the configured server."
        )
    canonical_project = proj_obj.id()

    rows: list[tuple[str, str, str, str, str, str]] = []

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
            rows.append((
                canonical_project,
                subj_label,
                subj_id,
                exp_obj.label(),
                exp_obj.id(),
                _experiment_date_yyyymmdd(exp_obj),
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
        ])
        writer.writerows(rows)

    print(f"Wrote {len(rows)} row(s) to {output_path}")
    return 0
