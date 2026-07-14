import argparse
import sys

from .bidsmap import bidsmap_cmd
from .cubids import cubids_cmd
from .download import download_cmd
from .login import login_cmd
from .mriconfig import mriconfig_cmd
from .mriconvert import mriconvert_cmd
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

    mriconfig_parser = subparsers.add_parser(
        "mriconfig",
        help="Run dcm2bids_helper on one or many downloaded XNAT experiment "
        "directories and draft a project-level dcm2bids config.",
    )
    mriconfig_parser.add_argument(
        "-i",
        "--input",
        required=True,
        metavar="INPUT_DIR",
        help="Root directory holding PROJECT/SUBJECT/EXPERIMENT "
        "subdirectories (i.e., the output of `xnatcli download`).",
    )
    mriconfig_source = (
        mriconfig_parser.add_mutually_exclusive_group(required=True)
    )
    mriconfig_source.add_argument(
        "-1",
        dest="triplet",
        nargs=3,
        metavar=("PROJECT", "SUBJECT", "EXPERIMENT"),
        help="Run dcm2bids_helper on a single experiment. Each value must match the "
        "corresponding directory name under INPUT_DIR.",
    )
    mriconfig_source.add_argument(
        "-s",
        "--subject",
        nargs=2,
        metavar=("PROJECT", "SUBJECT"),
        help="Run dcm2bids_helper on every experiment of one subject.",
    )
    mriconfig_source.add_argument(
        "-p",
        "--project",
        metavar="PROJECT",
        help="Run dcm2bids_helper on every experiment of every subject in a project.",
    )
    mriconfig_parser.add_argument(
        "-o",
        "--output",
        required=True,
        metavar="OUTPUT_DIR",
        help="Directory to write the mriconfig output into. The dcm2bids_helper "
        "results land under OUTPUT_DIR/PROJECT-<PROJECT>_mriconfig/.",
    )
    mriconfig_parser.add_argument(
        "-n",
        "--nprep",
        type=int,
        default=1,
        metavar="N",
        help="Number of parallel dcm2bids_helper invocations, one per "
        "experiment per core (default 1).",
    )
    mriconfig_parser.add_argument(
        "-l",
        "--log",
        action="store_true",
        help="Write a per-experiment log CSV to "
        "OUTPUT_DIR/log/mriconfig_<YYYYMMDD_HHMMSS>_log.csv.",
    )
    mriconfig_parser.add_argument(
        "-d",
        "--delete",
        action="store_true",
        help="Delete *.nii.gz files from each experiment's dcm2bids_helper subdir "
        "(OUTPUT_DIR/PROJECT-<PROJECT>_mriconfig/tmp_dcm2bids/helper/<EXPERIMENT>/) "
        "right after dcm2bids_helper returns, regardless of STATUS. JSON "
        "sidecars (used by the config draft) are kept.",
    )
    mriconfig_parser.add_argument(
        "-m",
        "--maps",
        action="store_true",
        help="Skip running dcm2bids_helper; only (re)draft the dcm2bids config "
        "from the existing dcm2bids_helper JSON sidecars already under "
        "OUTPUT_DIR/PROJECT-<PROJECT>_mriconfig/. dcm2bids_helper and dcm2niix "
        "are not required with this option.",
    )
    mriconfig_parser.set_defaults(func=mriconfig_cmd)

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
        "-y",
        "--physio",
        dest="physio_parent",
        metavar="PHYSIO_PARENT_DIR",
        default=None,
        help="Optional absolute path to the flat directory holding all raw "
        "physio recordings for this project. Recorded as the top-level "
        "'PhysioParent' key in OUTPUT_DIR/PROJECT-<P>_mriconvert_qc.json for "
        "xnatcli physioconvert to resolve OUTPUT_DIR/PROJECT-<P>_mriconvert_qc.tsv's "
        "'physio' column against. If omitted, a PhysioParent recorded on a "
        "prior run is preserved.",
    )
    mriconvert_parser.add_argument(
        "-c",
        "--config",
        metavar="CONFIG_FILE",
        help="Path to the dcm2bids config JSON to use (e.g., the one drafted "
        "by `xnatcli mriconfig`). Required unless -m/--maps is given. "
        "Recorded as the top-level 'Dcm2BidsConfigPath' key in "
        "OUTPUT_DIR/PROJECT-<P>_mriconvert_qc.json. If omitted (only "
        "possible with -m/--maps), a Dcm2BidsConfigPath recorded on a prior "
        "run is preserved.",
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
        help="Skip the dcm2bids conversion; only (re)generate "
        "OUTPUT_DIR/PROJECT-<P>_mriconvert_qc.tsv (and copy "
        "OUTPUT_DIR/PROJECT-<P>_mriconvert_qc.json) for every project in scope from "
        "the already-converted BIDS data under OUTPUT_DIR. -c/--config, pydicom, "
        "dcm2bids, and dcm2niix are not required with this option.",
    )
    mriconvert_parser.set_defaults(func=mriconvert_cmd)

    physioconvert_parser = subparsers.add_parser(
        "physioconvert",
        help="Convert physio recordings associated (via mriconvert_qc.tsv's "
        "'physio' column) with an xnatcli mriconvert BIDS dataset, via "
        "phys2bids.",
    )
    physioconvert_parser.add_argument(
        "-o",
        "--output",
        required=True,
        metavar="OUTPUT_DIR",
        help="Same BIDS root xnatcli mriconvert wrote to (OUTPUT_DIR must hold "
        "PROJECT-<P>_mriconvert_qc.tsv/PROJECT-<P>_mriconvert_qc.json). Physio "
        "outputs are written directly into "
        "OUTPUT_DIR/PROJECT/sub-X/ses-Y/<datatype>/ alongside the associated "
        ".nii.gz.",
    )
    physioconvert_parser.add_argument(
        "-p",
        "--project",
        required=True,
        metavar="PROJECT",
        help="Project directory name under OUTPUT_DIR identifying the BIDS "
        "dataset produced by xnatcli mriconvert.",
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
        "OUTPUT_DIR/log/physioconvert_<YYYYMMDD_HHMMSS>_log.csv, and mirror "
        "everything printed to stdout/stderr into a companion text log at "
        "OUTPUT_DIR/log/physioconvert_<YYYYMMDD_HHMMSS>_log.txt.",
    )
    physioconvert_parser.set_defaults(func=physioconvert_cmd)

    bidsmap_parser = subparsers.add_parser(
        "bidsmap",
        help="Generate (or update) a participant/session map TSV for a BIDS "
        "dataset at INPUT_DIR/PROJECT/ produced by xnatcli mriconvert.",
    )
    bidsmap_parser.add_argument(
        "-i",
        "--input",
        required=True,
        metavar="INPUT_DIR",
        help="Root directory holding the BIDS dataset at INPUT_DIR/PROJECT/ "
        "(i.e., the output of `xnatcli mriconvert`). The map TSV is written "
        "here as PROJECT-<PROJECT>_bidsmap.tsv.",
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
        "-o",
        "--output",
        metavar="OUTPUT_DIR",
        help="When provided, apply all renames from PROJECT-<PROJECT>_bidsmap.tsv "
        "(participant_rename, session_rename) — and mriconvert_qc.tsv's own rename "
        "column — by recursively copying the BIDS dataset to OUTPUT_DIR/PROJECT/ "
        "with every rename applied. The map TSV generation always runs first "
        "regardless. OUTPUT_DIR/PROJECT/ must not already exist. Skips "
        "tmp_dcm2bids and log scratch directories.",
    )
    bidsmap_parser.set_defaults(func=bidsmap_cmd)

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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
