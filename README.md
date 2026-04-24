# XNAT Command Line Interface (CLI)

A small PyXNAT-backed command-line client for logging into an XNAT server and downloading files, known as `xnatcli`.

## Contents

- [`src/xnatcli/`](src/xnatcli/) — installable package that provides the `xnatcli` CLI (`xnatcli login`, `xnatcli download`), built on [PyXNAT](https://pyxnat.github.io/pyxnat/index.html).

## Setup

The project uses [uv](https://docs.astral.sh/uv/) and Python ≥ 3.11. Runtime dependency: `pyxnat`.

```bash
uv sync
uv pip install -e .
```

The second command makes the `xnatcli` command available on your `PATH` inside the project's virtualenv. Activate the venv (`source .venv/bin/activate` on Unix, `.venv\Scripts\activate` on Windows) to use `xnatcli` directly, or invoke it with `uv run xnatcli …`.

## Design

The package is organized as:

- [`src/xnatcli/cli.py`](src/xnatcli/cli.py) — argparse setup and subcommand dispatch.
- [`src/xnatcli/login.py`](src/xnatcli/login.py) — interactive credential capture, verification, and on-disk storage.
- [`src/xnatcli/download.py`](src/xnatcli/download.py) — credential loading and experiment download.

Credentials live in `~/.xnatcli/credentials.cfg`, a plain-text [configparser](https://docs.python.org/3/library/configparser.html) file with a single `[xnatcli]` section storing `server`, `username`, and `password`. The file is created via `os.open` with mode `0o600` and re-`chmod`-ed to `0o600` after writing so only the owner can read or write it. (On Windows, `os.chmod` only toggles the read-only bit — the permissions model there is ACL-based; the `0o600` call still runs for portability.)

## `xnatcli login`

Interactively collects credentials, verifies them against the server, and writes them to disk.

1. Prompts for the XNAT server URL in plain text (e.g., `https://fmrif-xnat.nimh.nih.gov`).
2. Prompts for the username in plain text.
3. Prompts for the password via `getpass.getpass` so the characters are not echoed.
4. Connects to the server with PyXNAT and performs an authenticated request (`select.projects().get()`) to verify the credentials. Auth failures, connection failures, and other errors exit with distinct messages.
5. On success, writes `~/.xnatcli/credentials.cfg` with mode `0o600`.

```bash
xnatcli login
```

Re-running `xnatcli login` overwrites the stored credentials.

## `xnatcli download`

Downloads every file belonging to a single XNAT experiment — all scans (with their resources and files) plus any session-level resources.

1. Loads credentials from `~/.xnatcli/credentials.cfg`; if the file is missing or incomplete, exits with a message telling you to run `xnatcli login`.
2. Connects to the stored server via PyXNAT.
3. Walks `project → subject → experiment`, then iterates scans, their resources, and every file in each resource. Also iterates session-level resources on the experiment itself.
4. Writes each file to:
   - `OUTPUT_DIR/PROJECT_ID/SUBJECT_ID/EXPERIMENT_ID/scans/<scan_id>/<resource_label>/<filename>` for scan files
   - `OUTPUT_DIR/PROJECT_ID/SUBJECT_ID/EXPERIMENT_ID/resources/<resource_label>/<filename>` for session-level resource files

```bash
xnatcli download PROJECT_ID SUBJECT_ID EXPERIMENT_ID -o OUTPUT_DIR
```

| Argument | Description |
| --- | --- |
| `PROJECT_ID` | XNAT project ID. |
| `SUBJECT_ID` | XNAT subject ID or label. |
| `EXPERIMENT_ID` | XNAT experiment accession ID or label. |
| `-o`, `--output` | **Required.** Directory to write the downloaded files into (created if missing). |
