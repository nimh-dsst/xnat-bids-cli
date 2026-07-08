import argparse
import sys

from .bidsmap import bidsmap_cmd
from .cubids import cubids_cmd
from .download import download_cmd
from .login import login_cmd
from .mriconvert import mriconvert_cmd
from .mrihelp import mrihelp_cmd
from .physioconvert import physioconvert_cmd
from .query import query_cmd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xnatcli",
        description="Command-line client for XNAT servers.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    login_parser = subparsers.add_parser(
        "login",
        help="Prompt for XNAT server, username, and password; verify; save to ~/.xnatcli/credentials.cfg.",
    )
    login_parser.set_defaults(func=login_cmd)

    download_parser = subparsers.add_parser(
        "download",
        help="Download every file from one XNAT experiment (-1) or many (--csv).",
    )
    download_source = download_parser.add_mutually_exclusive_group(required=True)
    download_source.add_argument(
        "-1",
        dest="triplet",
        nargs=3,
        metavar=("PROJECT", "SUBJECT", "EXPERIMENT"),
        help="Download a single experiment. Each value may be either the "
        "XNAT ID or the user-facing label.",
    )
    download_source.add_argument(
        "-c",
        "--csv",
        "-i",
        "--input",
        dest="input",
        metavar="CSV_FILE",
        help="Path to a CSV file (xnatcli query output) listing experiments to download.",
    )
    download_parser.add_argument(
        "-o",
        "--output",
        required=True,
        metavar="OUTPUT_DIR",
        help="Directory to write the downloaded files into.",
    )
    download_parser.add_argument(
        "-n",
        "--ndownload",
        type=int,
        default=1,
        metavar="N",
        help="Number of parallel downloads (default 1). Per-experiment for --csv input; per-file for -1 input.",
    )
    download_parser.add_argument(
        "-l",
        "--log",
        action="store_true",
        help="Write a download log CSV to OUTPUT_DIR/log/download_<YYYYMMDD_HHMMSS>_log.csv.",
    )
    download_parser.add_argument(
        "-a",
        "--archive",
        action="store_true",
        help="After downloading each experiment, tar+gzip its "
        "OUTPUT_DIR/PROJECT/SUBJECT/EXPERIMENT directory into "
        "OUTPUT_DIR/archive/PROJECT-<P>_SUBJECT-<S>_EXPERIMENT-<E>.tar.gz. "
        "Existing archives are skipped with a warning.",
    )
    download_parser.add_argument(
        "-d",
        "--delete",
        action="store_true",
        help="After a successful archive (requires --archive), delete the "
        "OUTPUT_DIR/PROJECT/SUBJECT/EXPERIMENT directory. The SUBJECT and "
        "PROJECT parent directories are also removed if they become empty.",
    )
    download_parser.set_defaults(func=download_cmd)

    query_parser = subparsers.add_parser(
        "query",
        help="Write a CSV of (project, subject, experiment) triplets for a project or subject.",
    )
    query_parser.add_argument(
        "project",
        metavar="PROJECT",
        help="XNAT project (ID or label).",
    )
    query_parser.add_argument(
        "subject",
        metavar="SUBJECT",
        nargs="?",
        default=None,
        help="Optional XNAT subject (ID or label). If omitted, all subjects "
        "in the project are listed.",
    )
    query_parser.add_argument(
        "-o",
        "--output",
        required=True,
        metavar="OUTPUT_DIR",
        help="Directory to write the CSV file into.",
    )
    query_parser.set_defaults(func=query_cmd)

    mrihelp_parser = subparsers.add_parser(
        "mrihelp",
        help="Run dcm2bids_helper on one or many downloaded XNAT experiment "
        "directories and draft a project-level dcm2bids config.",
    )
    mrihelp_parser.add_argument(
        "-i",
        "--input",
        required=True,
        metavar="INPUT_DIR",
        help="Root directory holding PROJECT/SUBJECT/EXPERIMENT "
        "subdirectories (i.e., the output of `xnatcli download`).",
    )
    mrihelp_source = (
        mrihelp_parser.add_mutually_exclusive_group(required=True)
    )
    mrihelp_source.add_argument(
        "-1",
        dest="triplet",
        nargs=3,
        metavar=("PROJECT", "SUBJECT", "EXPERIMENT"),
        help="Run helper on a single experiment. Each value must match the "
        "corresponding directory name under INPUT_DIR.",
    )
    mrihelp_source.add_argument(
        "-s",
        "--subject",
        nargs=2,
        metavar=("PROJECT", "SUBJECT"),
        help="Run helper on every experiment of one subject.",
    )
    mrihelp_source.add_argument(
        "-p",
        "--project",
        metavar="PROJECT",
        help="Run helper on every experiment of every subject in a project.",
    )
    mrihelp_parser.add_argument(
        "-o",
        "--output",
        required=True,
        metavar="OUTPUT_DIR",
        help="Directory to write the mrihelp output into. The helper "
        "results land under OUTPUT_DIR/PROJECT-<PROJECT>_mrihelp/.",
    )
    mrihelp_parser.add_argument(
        "-n",
        "--nprep",
        type=int,
        default=1,
        metavar="N",
        help="Number of parallel dcm2bids_helper invocations, one per "
        "experiment per core (default 1).",
    )
    mrihelp_parser.add_argument(
        "-l",
        "--log",
        action="store_true",
        help="Write a per-experiment log CSV to "
        "OUTPUT_DIR/log/mrihelp_<YYYYMMDD_HHMMSS>_log.csv.",
    )
    mrihelp_parser.add_argument(
        "-d",
        "--delete",
        action="store_true",
        help="Delete *.nii.gz files from each experiment's helper subdir "
        "(OUTPUT_DIR/PROJECT-<PROJECT>_mrihelp/tmp_dcm2bids/helper/<EXPERIMENT>/) "
        "right after dcm2bids_helper returns, regardless of STATUS. JSON "
        "sidecars (used by the config draft) are kept.",
    )
    mrihelp_parser.add_argument(
        "-m",
        "--maps",
        action="store_true",
        help="Skip running dcm2bids_helper; only (re)draft the dcm2bids config "
        "from the existing helper JSON sidecars already under "
        "OUTPUT_DIR/PROJECT-<PROJECT>_mrihelp/. dcm2bids_helper and dcm2niix "
        "are not required with this option.",
    )
    mrihelp_parser.set_defaults(func=mrihelp_cmd)

    mriconvert_parser = subparsers.add_parser(
        "mriconvert",
        help="Convert XNAT-downloaded sessions to BIDS via dcm2bids.",
    )
    mriconvert_parser.add_argument(
        "-i",
        "--input",
        required=True,
        metavar="INPUT_DIR",
        help="Root directory holding PROJECT/SUBJECT/EXPERIMENT "
        "subdirectories (i.e., the output of `xnatcli download`). The "
        "directory names should match those written by `xnatcli download` "
        "(XNAT IDs for the project, labels for subject and experiment).",
    )
    mriconvert_source = (
        mriconvert_parser.add_mutually_exclusive_group(required=True)
    )
    mriconvert_source.add_argument(
        "-1",
        dest="triplet",
        nargs=3,
        metavar=("PROJECT", "SUBJECT", "EXPERIMENT"),
        help="Convert a single session. Each value must match the "
        "corresponding directory name under INPUT_DIR.",
    )
    mriconvert_source.add_argument(
        "-s",
        "--subject",
        nargs=2,
        metavar=("PROJECT", "SUBJECT"),
        help="Convert all sessions of one subject. Values must match the "
        "corresponding directory names under INPUT_DIR.",
    )
    mriconvert_source.add_argument(
        "-p",
        "--project",
        metavar="PROJECT",
        help="Convert all sessions of all subjects in a project. Value "
        "must match the project directory name under INPUT_DIR.",
    )
    mriconvert_parser.add_argument(
        "-o",
        "--output",
        required=True,
        metavar="OUTPUT_DIR",
        help="Directory to write BIDS-converted data into. Each project's "
        "BIDS dataset lives at OUTPUT_DIR/PROJECT/.",
    )
    mriconvert_parser.add_argument(
        "-c",
        "--config",
        metavar="CONFIG_FILE",
        help="Path to the dcm2bids config JSON to use (e.g., the one drafted "
        "by `xnatcli mrihelp`). Required unless -m/--maps is given.",
    )
    mriconvert_parser.add_argument(
        "-n",
        "--nconvert",
        type=int,
        default=1,
        metavar="N",
        help="Number of parallel session conversions (default 1).",
    )
    mriconvert_parser.add_argument(
        "-l",
        "--log",
        action="store_true",
        help="Write a per-session log CSV to "
        "OUTPUT_DIR/log/mriconvert_<YYYYMMDD_HHMMSS>_log.csv.",
    )
    mriconvert_parser.add_argument(
        "-a",
        "--archive",
        action="store_true",
        help="For every session in scope, tar+gzip its "
        "INPUT_DIR/PROJECT/SUBJECT/EXPERIMENT directory into "
        "INPUT_DIR/archive/PROJECT-<P>_SUBJECT-<S>_EXPERIMENT-<E>.tar.gz. "
        "Existing archives are skipped with a warning. Archiving runs "
        "regardless of the dcm2bids conversion outcome.",
    )
    mriconvert_parser.add_argument(
        "-d",
        "--delete",
        action="store_true",
        help="Delete each session's input directory "
        "INPUT_DIR/PROJECT/SUBJECT/EXPERIMENT after it is safely preserved. "
        "Without --archive, deletion runs only when the session converted "
        "with STATUS=COMPLETE or STATUS=EMPTY. With --archive, deletion "
        "runs after a successful archive regardless of conversion status. "
        "The SUBJECT and PROJECT parent directories are also removed if "
        "they become empty.",
    )
    mriconvert_parser.add_argument(
        "-m",
        "--maps",
        action="store_true",
        help="Skip the dcm2bids conversion; only (re)generate mriscans.tsv (and "
        "copy mriscans.json) for every project in scope from the already-converted "
        "BIDS data under OUTPUT_DIR. -c/--config, pydicom, dcm2bids, and "
        "dcm2niix are not required with this option.",
    )
    mriconvert_parser.set_defaults(func=mriconvert_cmd)

    cubids_parser = subparsers.add_parser(
        "cubids",
        help="Run cubids add-nifti-info and cubids group on a BIDS dataset.",
    )
    cubids_parser.add_argument(
        "-i",
        "--input",
        required=True,
        metavar="INPUT_DIR",
        help="Parent directory holding the BIDS dataset at INPUT_DIR/PROJECT/ "
        "(i.e., the output of `xnatcli mriconvert`). CuBIDS outputs land "
        "under INPUT_DIR/PROJECT-<PROJECT>_cubids/.",
    )
    cubids_parser.add_argument(
        "-p",
        "--project",
        required=True,
        metavar="PROJECT",
        help="Project directory name under INPUT_DIR identifying the BIDS "
        "dataset to process.",
    )
    cubids_parser.add_argument(
        "-l",
        "--log",
        action="store_true",
        help="Write a per-step log CSV to "
        "INPUT_DIR/PROJECT-<PROJECT>_cubids/log/cubids_<YYYYMMDD_HHMMSS>_log.csv.",
    )
    cubids_parser.set_defaults(func=cubids_cmd)

    bidsmap_parser = subparsers.add_parser(
        "bidsmap",
        help="Generate (or update) a participant/session map TSV for a BIDS "
        "dataset at INPUT_DIR/PROJECT/, for either the mri or physio modality.",
    )
    bidsmap_parser.add_argument(
        "-i",
        "--input",
        required=True,
        metavar="INPUT_DIR",
        help="Root directory holding the BIDS dataset at INPUT_DIR/PROJECT/ "
        "(i.e., the output of `xnatcli mriconvert` for -m mri, or the parent "
        "of `xnatcli physioconvert`'s OUTPUT_DIR/PROJECT/ for -m physio). The "
        "map TSV is written here as PROJECT-<PROJECT>_bidsmap.tsv.",
    )
    bidsmap_parser.add_argument(
        "-p",
        "--project",
        required=True,
        metavar="PROJECT",
        help="Project directory name under INPUT_DIR identifying the BIDS "
        "dataset to scan for participants and sessions.",
    )
    bidsmap_parser.add_argument(
        "-m",
        "--modality",
        required=True,
        choices=["mri", "physio"],
        help="Which BIDS dataset shape INPUT_DIR/PROJECT/ holds. 'mri' expects "
        ".nii.gz files with .json/.bval/.bvec sidecars and honors "
        "mriscans.tsv's rename column and QC-exclusion columns; on -o its "
        "manifest is promoted to scans.tsv/scans.json in the mapped output. "
        "'physio' expects .tsv.gz files with .json sidecars and honors "
        "physioscans.tsv's rename column and QC-exclusion columns the same "
        "way (rename only applies when a source produced a single output "
        "file); its manifest stays named physioscans.tsv/physioscans.json "
        "(not promoted to scans.tsv, so it never collides with mri's) and "
        "patches physioscans.tsv instead of participants.tsv.",
    )
    bidsmap_parser.add_argument(
        "-o",
        "--output",
        metavar="OUTPUT_DIR",
        help="When provided, apply all renames from PROJECT-<PROJECT>_bidsmap.tsv "
        "(participant_rename, session_rename) — and the modality's own manifest "
        "rename column (see -m/--modality) — by recursively copying the BIDS "
        "dataset to OUTPUT_DIR/PROJECT/ with every rename applied. The map TSV "
        "generation always runs first regardless. OUTPUT_DIR/PROJECT/ must not "
        "already exist. Skips tmp_dcm2bids, tmp_phys2bids, and log scratch "
        "directories.",
    )
    bidsmap_parser.set_defaults(func=bidsmap_cmd)

    physioconvert_parser = subparsers.add_parser(
        "physioconvert",
        help="Convert physiological recordings found under a directory tree to "
        "BIDS physio files via phys2bids.",
    )
    physioconvert_parser.add_argument(
        "-i",
        "--input",
        required=True,
        metavar="INPUT_DIR",
        help="Root directory to walk recursively for phys2bids-supported "
        "physio files (.acq/.txt/.mat/.gep/.smr).",
    )
    physioconvert_parser.add_argument(
        "-p",
        "--project",
        required=True,
        metavar="PROJECT",
        help="Name of the BIDS project directory to nest outputs under, "
        "i.e. OUTPUT_DIR/PROJECT/.",
    )
    physioconvert_parser.add_argument(
        "-o",
        "--output",
        required=True,
        metavar="OUTPUT_DIR",
        help="Directory to write BIDS physio files into. Each project's "
        "physio outputs land under OUTPUT_DIR/PROJECT/, where a "
        "physioscans.tsv (source path, best-guess BIDS entities, per-file "
        "metrics, and bids_name/rename/QC review columns) and its "
        "physioscans.json data dictionary are written/updated at its root.",
    )
    physioconvert_parser.add_argument(
        "-n",
        "--nphysio",
        type=int,
        default=1,
        metavar="N",
        help="Number of physio files to convert in parallel, one phys2bids "
        "conversion per process (default 1).",
    )
    physioconvert_parser.add_argument(
        "-l",
        "--log",
        action="store_true",
        help="Write a per-file log CSV to "
        "OUTPUT_DIR/PROJECT/log/physioconvert_<YYYYMMDD_HHMMSS>_log.csv.",
    )
    physioconvert_parser.add_argument(
        "-m",
        "--maps",
        action="store_true",
        help="Skip the phys2bids conversion; only (re)generate "
        "physioscans.tsv (and copy its JSON data dictionary) by re-reading "
        "each physio file's metrics and entities, preserving the converted "
        "output paths and bids_name/rename/QC review columns from the "
        "existing physioscans.tsv.",
    )
    physioconvert_parser.set_defaults(func=physioconvert_cmd)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
