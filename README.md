# XNAT Command Line Interface (CLI)

A PyXNAT-backed command-line client for logging into an XNAT server and querying experiments or downloading files.

## Contents

- [`src/xnatcli/`](src/xnatcli/) — installable package that provides the `xnatcli` CLI (`xnatcli login`, `xnatcli download`, `xnatcli query`), built on [PyXNAT](https://pyxnat.github.io/pyxnat/index.html).

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
- [`src/xnatcli/login.py`](src/xnatcli/login.py) — interactive credential capture, verification, and on-disk storage; also exposes `load_credentials` for the other subcommands.
- [`src/xnatcli/download.py`](src/xnatcli/download.py) — experiment download.
- [`src/xnatcli/query.py`](src/xnatcli/query.py) — CSV listing of (project, subject, experiment) triplets.

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

Downloads every file belonging to one XNAT experiment (single-experiment mode, `-1`) or every experiment listed in an `xnatcli query` CSV (batch mode, `--csv`). For each experiment, all scans (with their resources and files) plus any session-level resources are written.

1. Loads credentials from `~/.xnatcli/credentials.cfg`; if the file is missing or incomplete, exits with a message telling you to run `xnatcli login`.
2. Connects to the stored server via PyXNAT.
3. For each experiment, walks `project → subject → experiment`, then iterates scans, their resources, and every file in each resource. Also iterates session-level resources on the experiment itself.
4. Writes each file to:
   - `OUTPUT_DIR/PROJECT_ID/SUBJECT_ID/EXPERIMENT_ID/scans/<scan_id>/<resource_label>/<filename>` for scan files
   - `OUTPUT_DIR/PROJECT_ID/SUBJECT_ID/EXPERIMENT_ID/resources/<resource_label>/<filename>` for session-level resource files

```bash
# Single experiment
xnatcli download -1 PROJECT_ID SUBJECT_ID EXPERIMENT_ID -o OUTPUT_DIR

# Batch from a query CSV
xnatcli download --csv PATH/TO/QUERY.csv -o OUTPUT_DIR
```

`-1` and `--csv` are mutually exclusive; exactly one must be supplied.

| Argument | Description |
| --- | --- |
| `-1 PROJECT_ID SUBJECT_ID EXPERIMENT_ID` | Download a single experiment from explicit IDs. |
| `-c`, `--csv`, `-i`, `--input` | Path to a CSV file (`xnatcli query` output) listing experiments to download. Must contain the columns `PROJECT_ID`, `SUBJECT_ID`, `EXPERIMENT_ID`. |
| `-o`, `--output` | **Required.** Directory to write the downloaded files into (created if missing). |
| `-n`, `--ndownload` | *Optional.* Number of parallel downloads (default `1`). Per-experiment for `--csv`; per-file for `-1`. |
| `-l`, `--log` | *Optional.* Write a per-experiment log CSV to `OUTPUT_DIR/log/download_<YYYYMMDD_HHMM>_log.csv` (local time, captured at run start). |

### Per-experiment STATUS (and exit code)

In `--csv` mode, the run continues through all rows even if some fail and exits with a summary; in `-1` mode, the single STATUS determines the exit code. Possible STATUS values:

| STATUS | Meaning |
| --- | --- |
| `COMPLETE` | Every file in the experiment downloaded successfully. |
| `PARTIAL` | At least one file succeeded and at least one failed. |
| `FAILURE` | The experiment exists with files, but every file download raised. |
| `NONEXISTENT` | The experiment lookup did not find anything on the server. |
| `EMPTY` | The experiment exists but has zero files. |

Exit code is `0` if every processed experiment is `COMPLETE` or `EMPTY`, and `1` otherwise.

### Download log CSV (`-l`/`--log`)

When `-l/--log` is supplied, a CSV is written at `OUTPUT_DIR/log/download_<YYYYMMDD_HHMM>_log.csv`, where the timestamp is the local-time start of the run. The header is always:

```text
DATESTAMP,PROJECT_ID,SUBJECT_ID,EXPERIMENT_ID,STATUS
```

`DATESTAMP` is the per-experiment download attempt begin time, formatted to match Python's `logging` module default `asctime` (`YYYY-MM-DD HH:MM:SS,mmm`, local time). One row is appended per processed experiment; rows are written under a lock so concurrent workers do not interleave.

## `xnatcli query`

Writes a CSV of `(PROJECT_ID, SUBJECT_ID, EXPERIMENT_ID)` triplets — every experiment in a project, or every experiment under a single subject in a project. Values are the canonical XNAT accession IDs (not user-supplied labels), so the CSV is stable regardless of how arguments were typed.

1. Loads credentials from `~/.xnatcli/credentials.cfg`; if the file is missing or incomplete, exits with a message telling you to run `xnatcli login`.
2. Connects to the stored server via PyXNAT.
3. Verifies the project exists (and the subject, if provided); exits with an error if not.
4. Iterates subjects and experiments and writes the triplets to a CSV with header `PROJECT_ID,SUBJECT_ID,EXPERIMENT_ID`. If the project (or subject) exists but has no experiments, a header-only CSV is written. An existing output file is overwritten silently.

```bash
xnatcli query PROJECT_ID [SUBJECT_ID] -o OUTPUT_DIR
```

The output filename is:

- `OUTPUT_DIR/PROJECT_ID-<PROJECT_ID>.csv` when only `PROJECT_ID` is supplied.
- `OUTPUT_DIR/PROJECT_ID-<PROJECT_ID>_SUBJECT_ID-<SUBJECT_ID>.csv` when both positional arguments are supplied.

| Argument | Description |
| --- | --- |
| `PROJECT_ID` | XNAT project ID. |
| `SUBJECT_ID` | *Optional.* XNAT subject ID or label. If omitted, all subjects in the project are listed. |
| `-o`, `--output` | **Required.** Directory to write the CSV file into (created if missing). |
