import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def _require_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        sys.exit(
            f"Error: required tool '{name}' was not found on PATH. "
            "Install it and try again."
        )
    return path


def bidsprep_cmd(args: argparse.Namespace) -> int:
    experiment_dir = Path(args.experiment_dir).resolve()
    if not experiment_dir.is_dir():
        sys.exit(f"Error: input is not a directory: {experiment_dir}")

    scans_dir = experiment_dir / "scans"
    if not scans_dir.is_dir():
        sys.exit(
            f"Error: expected 'scans/' subdirectory under {experiment_dir}"
        )

    # Layout: <...>/PROJECT_ID/SUBJECT_ID/EXPERIMENT_ID
    project_dir = experiment_dir.parent.parent
    project_id = project_dir.name
    if not project_id or project_dir == experiment_dir:
        sys.exit(
            "Error: could not derive PROJECT_ID from two directories above "
            f"{experiment_dir}"
        )

    helper = _require_tool("dcm2bids_helper")
    _require_tool("dcm2niix")

    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"PROJECT_ID-{project_id}_bidsprep"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    cmd = [helper, "-d", str(scans_dir), "-o", str(target)]
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        sys.exit(
            f"Error: dcm2bids_helper exited with code {result.returncode}."
        )
    print(f"Helper output written to {target}")
    return 0
