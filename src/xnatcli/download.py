import argparse
import configparser
import sys
from pathlib import Path

from pyxnat import Interface

from .login import CONFIG_PATH


def _load_credentials() -> tuple[str, str, str]:
    if not CONFIG_PATH.exists():
        sys.exit(
            f"Error: no credentials found at {CONFIG_PATH}. Run 'xnatcli login' first."
        )
    config = configparser.ConfigParser()
    config.read(CONFIG_PATH)
    if "xnatcli" not in config:
        sys.exit(
            f"Error: {CONFIG_PATH} is missing the [xnatcli] section. Run 'xnatcli login' again."
        )
    section = config["xnatcli"]
    for key in ("server", "username", "password"):
        if not section.get(key):
            sys.exit(
                f"Error: {CONFIG_PATH} is missing '{key}'. Run 'xnatcli login' again."
            )
    return section["server"], section["username"], section["password"]


def _download_experiment(
    interface: Interface,
    project_id: str,
    subject_id: str,
    experiment_id: str,
    output_dir: Path,
) -> int:
    experiment = (
        interface.select.project(project_id)
        .subject(subject_id)
        .experiment(experiment_id)
    )
    if not experiment.exists():
        sys.exit(
            f"Error: experiment '{experiment_id}' not found "
            f"(project='{project_id}', subject='{subject_id}') on the configured server."
        )

    experiment_root = output_dir / experiment_id
    count = 0

    for scan in experiment.scans():
        scan_id = scan.id()
        for resource in scan.resources():
            resource_label = resource.label()
            for f in resource.files():
                dest = (
                    experiment_root
                    / "scans"
                    / scan_id
                    / resource_label
                    / f.label()
                )
                dest.parent.mkdir(parents=True, exist_ok=True)
                f.get(str(dest))
                print(f"  {dest}")
                count += 1

    for resource in experiment.resources():
        resource_label = resource.label()
        for f in resource.files():
            dest = experiment_root / "resources" / resource_label / f.label()
            dest.parent.mkdir(parents=True, exist_ok=True)
            f.get(str(dest))
            print(f"  {dest}")
            count += 1

    return count


def download_cmd(args: argparse.Namespace) -> int:
    server, username, password = _load_credentials()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    interface = None
    try:
        interface = Interface(server=server, user=username, password=password)
        count = _download_experiment(
            interface,
            args.project_id,
            args.subject_id,
            args.experiment_id,
            output_dir,
        )
    except SystemExit:
        raise
    except Exception as e:
        sys.exit(f"Error: download failed: {e}")
    finally:
        if interface is not None:
            try:
                interface.disconnect()
            except Exception:
                pass

    print(f"Downloaded {count} file(s) to {output_dir / args.experiment_id}")
    return 0
