import argparse
import csv
import sys
from pathlib import Path

from pyxnat import Interface

from .login import load_credentials


def _collect_triplets(
    interface: Interface,
    project_id: str,
    subject_id: str | None,
) -> list[tuple[str, str, str]]:
    project = interface.select.project(project_id)
    if not project.exists():
        sys.exit(
            f"Error: project '{project_id}' not found on the configured server."
        )
    canonical_project = project.id()

    triplets: list[tuple[str, str, str]] = []

    if subject_id is None:
        for subject in project.subjects():
            canonical_subject = subject.id()
            for experiment in subject.experiments():
                triplets.append(
                    (canonical_project, canonical_subject, experiment.id())
                )
    else:
        subject = project.subject(subject_id)
        if not subject.exists():
            sys.exit(
                f"Error: subject '{subject_id}' not found in project "
                f"'{project_id}' on the configured server."
            )
        canonical_subject = subject.id()
        for experiment in subject.experiments():
            triplets.append(
                (canonical_project, canonical_subject, experiment.id())
            )

    return triplets


def query_cmd(args: argparse.Namespace) -> int:
    server, username, password = load_credentials()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.subject_id is None:
        filename = f"PROJECT_ID-{args.project_id}.csv"
    else:
        filename = (
            f"PROJECT_ID-{args.project_id}_SUBJECT_ID-{args.subject_id}.csv"
        )
    output_path = output_dir / filename

    interface = None
    try:
        interface = Interface(server=server, user=username, password=password)
        triplets = _collect_triplets(
            interface, args.project_id, args.subject_id
        )
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
        writer.writerow(["PROJECT_ID", "SUBJECT_ID", "EXPERIMENT_ID"])
        writer.writerows(triplets)

    print(f"Wrote {len(triplets)} row(s) to {output_path}")
    return 0
