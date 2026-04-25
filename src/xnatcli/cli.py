import argparse
import sys

from .download import download_cmd
from .login import login_cmd
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
        metavar=("PROJECT_ID", "SUBJECT_ID", "EXPERIMENT_ID"),
        help="Download a single experiment from explicit IDs.",
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
        help="Write a download log CSV to OUTPUT_DIR/log/download_<YYYYMMDD_HHMM>_log.csv.",
    )
    download_parser.set_defaults(func=download_cmd)

    query_parser = subparsers.add_parser(
        "query",
        help="Write a CSV of (project, subject, experiment) triplets for a project or subject.",
    )
    query_parser.add_argument(
        "project_id", metavar="PROJECT_ID", help="XNAT project ID."
    )
    query_parser.add_argument(
        "subject_id",
        metavar="SUBJECT_ID",
        nargs="?",
        default=None,
        help="Optional XNAT subject ID or label. If omitted, all subjects in the project are listed.",
    )
    query_parser.add_argument(
        "-o",
        "--output",
        required=True,
        metavar="OUTPUT_DIR",
        help="Directory to write the CSV file into.",
    )
    query_parser.set_defaults(func=query_cmd)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
