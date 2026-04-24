import argparse
import sys

from .download import download_cmd
from .login import login_cmd


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
        help="Download every file from a single XNAT experiment.",
    )
    download_parser.add_argument(
        "project_id", metavar="PROJECT_ID", help="XNAT project ID."
    )
    download_parser.add_argument(
        "subject_id", metavar="SUBJECT_ID", help="XNAT subject ID or label."
    )
    download_parser.add_argument(
        "experiment_id",
        metavar="EXPERIMENT_ID",
        help="XNAT experiment accession ID or label.",
    )
    download_parser.add_argument(
        "-o",
        "--output",
        required=True,
        metavar="OUTPUT_DIR",
        help="Directory to write the downloaded files into.",
    )
    download_parser.set_defaults(func=download_cmd)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
