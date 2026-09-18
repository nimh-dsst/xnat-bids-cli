import shutil
import tarfile
from pathlib import Path

STATUS_COMPLETE = "COMPLETE"
STATUS_SKIPPED = "SKIPPED"
STATUS_FAILURE = "FAILURE"
STATUS_NONEXISTENT = "NONEXISTENT"

OK_STATUSES = {STATUS_COMPLETE, STATUS_SKIPPED}


def archive_path(
    data_root: Path, project: str, subject: str, experiment: str
) -> Path:
    return (
        data_root
        / "archive"
        / f"PROJECT-{project}_SUBJECT-{subject}_EXPERIMENT-{experiment}.tar.gz"
    )


def archive_experiment(
    data_root: Path, project: str, subject: str, experiment: str
) -> tuple[str, str | None]:
    """Tar+gzip data_root/PROJECT/SUBJECT/EXPERIMENT into data_root/archive/.

    Returns (status, detail). Existing archives are left untouched and reported
    as SKIPPED; the tarball is written to a .tmp sibling and renamed into place
    only on success so an interrupted run never leaves a partial file behind.
    """
    src = data_root / project / subject / experiment
    if not src.is_dir():
        return STATUS_NONEXISTENT, f"source directory not found: {src}"

    dest = archive_path(data_root, project, subject, experiment)
    if dest.exists():
        return STATUS_SKIPPED, f"archive already exists: {dest}"

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    try:
        with tarfile.open(tmp, "w:gz") as tar:
            tar.add(src, arcname=experiment)
        tmp.replace(dest)
    except Exception as e:
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass
        return STATUS_FAILURE, f"error creating archive: {e}"
    return STATUS_COMPLETE, None


def delete_experiment_dir(
    data_root: Path, project: str, subject: str, experiment: str
) -> None:
    """Delete the EXPERIMENT dir and prune empty SUBJECT/PROJECT parents."""
    exp_dir = data_root / project / subject / experiment
    if exp_dir.exists():
        shutil.rmtree(exp_dir, ignore_errors=True)
    for parent in (data_root / project / subject, data_root / project):
        try:
            parent.rmdir()
        except OSError:
            return
