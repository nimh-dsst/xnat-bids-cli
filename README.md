# XNAT CLI for BIDS

A command-line interface for logging into an Extensible Neuroimaging Archive Toolkit (XNAT) server, querying experiments and downloading files, then converting to the Brain Imaging Data Structure (BIDS) standard format.

## Contents

- [`src/xnatcli/`](src/xnatcli/) — installable package that provides the `xnatcli` CLI (`xnatcli login`, `xnatcli download`, `xnatcli query`, `xnatcli bidsprep`, `xnatcli bidsconvert`, `xnatcli cubids`), built on [PyXNAT](https://pyxnat.github.io/pyxnat/index.html).

## Setup

The project uses [uv](https://docs.astral.sh/uv/) and Python ≥ 3.11. Runtime dependencies: `pyxnat`, `dcm2bids`, `dcm2niix` (the [`dcm2niix`](https://pypi.org/project/dcm2niix/) PyPI package vendors the binary onto your `PATH`), `pydicom`, `cubids`.

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
- [`src/xnatcli/bidsprep.py`](src/xnatcli/bidsprep.py) — runs `dcm2bids_helper` against a downloaded experiment directory.
- [`src/xnatcli/bidsconvert.py`](src/xnatcli/bidsconvert.py) — converts downloaded XNAT sessions to BIDS via `dcm2bids`.
- [`src/xnatcli/cubids.py`](src/xnatcli/cubids.py) — runs [`CuBIDS`](https://cubids.readthedocs.io/) `add-nifti-info` and `group` on a BIDS dataset.

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
   - `OUTPUT_DIR/PROJECT/SUBJECT/EXPERIMENT/scans/<scan_id>/<resource_label>/<filename>` for scan files
   - `OUTPUT_DIR/PROJECT/SUBJECT/EXPERIMENT/resources/<resource_label>/<filename>` for session-level resource files

   `PROJECT` is the canonical XNAT project ID; `SUBJECT` and `EXPERIMENT` are the user-facing labels emitted by `xnatcli query`.

```bash
# Single experiment
xnatcli download -1 PROJECT SUBJECT EXPERIMENT -o OUTPUT_DIR

# Batch from a query CSV
xnatcli download --csv PATH/TO/QUERY.csv -o OUTPUT_DIR
```

`-1` and `--csv` are mutually exclusive; exactly one must be supplied.

| Argument | Description |
| --- | --- |
| `-1 PROJECT SUBJECT EXPERIMENT` | Download a single experiment. Each value may be either the XNAT ID or the user-facing label. |
| `-c`, `--csv`, `-i`, `--input` | Path to a CSV file (`xnatcli query` output) listing experiments to download. Must contain the columns `PROJECT`, `SUBJECT_LABEL`, `EXPERIMENT_LABEL`; any other columns (e.g., `SUBJECT_ID`, `EXPERIMENT_ID`, `EXPERIMENT_DATE`) are ignored. |
| `-o`, `--output` | **Required.** Directory to write the downloaded files into (created if missing). |
| `-n`, `--ndownload` | *Optional.* Number of parallel downloads (default `1`). Per-experiment for `--csv`; per-file for `-1`. |
| `-l`, `--log` | *Optional.* Write a per-experiment log CSV to `OUTPUT_DIR/log/download_<YYYYMMDD_HHMMSS>_log.csv` (local time, captured at run start). |

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

When `-l/--log` is supplied, a CSV is written at `OUTPUT_DIR/log/download_<YYYYMMDD_HHMMSS>_log.csv`, where the timestamp is the local-time start of the run. The header is always:

```text
DATESTAMP,PROJECT,SUBJECT,EXPERIMENT,STATUS
```

`DATESTAMP` is the per-experiment download attempt begin time, formatted to match Python's `logging` module default `asctime` (`YYYY-MM-DD HH:MM:SS,mmm`, local time). One row is appended per processed experiment; rows are written under a lock so concurrent workers do not interleave.

## `xnatcli query`

Writes a CSV of one row per experiment — every experiment in a project, or every experiment under a single subject in a project. The columns are:

| Column | Source |
| --- | --- |
| `PROJECT` | Canonical XNAT project ID. |
| `SUBJECT_LABEL` | User-facing subject label (e.g., `sub-001`). Used by downstream commands and on-disk paths. |
| `SUBJECT_ID` | XNAT accession ID for the subject (e.g., `XNAT_S00001`). |
| `EXPERIMENT_LABEL` | User-facing experiment label (e.g., `ses-baseline`). Used by downstream commands and on-disk paths. |
| `EXPERIMENT_ID` | XNAT accession ID for the experiment (e.g., `XNAT_E00001`). |
| `EXPERIMENT_DATE` | Experiment date in `YYYYMMDD` format. Empty if unset on the server or unparseable. |

> **Note:** XNAT enforces label uniqueness within a project for both subjects and experiments. If a server somehow contains duplicate labels, the resulting CSV may contain rows that downstream commands cannot disambiguate.

1. Loads credentials from `~/.xnatcli/credentials.cfg`; if the file is missing or incomplete, exits with a message telling you to run `xnatcli login`.
2. Connects to the stored server via PyXNAT.
3. Verifies the project exists (and the subject, if provided); exits with an error if not.
4. Iterates subjects and experiments and writes one row per experiment with header `PROJECT,SUBJECT_LABEL,SUBJECT_ID,EXPERIMENT_LABEL,EXPERIMENT_ID,EXPERIMENT_DATE`. If the project (or subject) exists but has no experiments, a header-only CSV is written. An existing output file is overwritten silently.

```bash
xnatcli query PROJECT [SUBJECT] -o OUTPUT_DIR
```

The output filename is:

- `OUTPUT_DIR/PROJECT-<PROJECT>.csv` when only `PROJECT` is supplied.
- `OUTPUT_DIR/PROJECT-<PROJECT>_SUBJECT-<SUBJECT>.csv` when both positional arguments are supplied.

| Argument | Description |
| --- | --- |
| `PROJECT` | XNAT project (ID or label). |
| `SUBJECT` | *Optional.* XNAT subject (ID or label). If omitted, all subjects in the project are listed. |
| `-o`, `--output` | **Required.** Directory to write the CSV file into (created if missing). |

## `xnatcli bidsprep`

Runs `dcm2bids_helper` (from [`Dcm2Bids`](https://unfmontreal.github.io/Dcm2Bids/)) on a single downloaded XNAT experiment directory and writes the helper's output under a per-project bidsprep directory.

1. Validates that the input is a directory and that it contains a `scans/` subdirectory (matching the layout produced by `xnatcli download`).
2. Derives `PROJECT` from two parent directories above the input — i.e., assumes the layout `<...>/PROJECT/SUBJECT/EXPERIMENT`.
3. Verifies that both `dcm2bids_helper` and `dcm2niix` are on `PATH`; exits with an error if either is missing.
4. Computes the target directory `OUTPUT_DIR/PROJECT-<PROJECT>_bidsprep/`. If it already exists, it is removed and recreated (overwrite).
5. Invokes `dcm2bids_helper -d EXPERIMENT_DIR/scans -o <target>`. The helper writes its NIfTI/JSON outputs into `<target>/tmp_dcm2bids/helper/`.
6. Drafts a first-pass dcm2bids config at `<target>/dcm2bids_config.json` from every `tmp_dcm2bids/helper/*.json` sidecar:
   - For each sidecar with a `BidsGuess` field (set by recent `dcm2niix` releases), parses it into `datatype`, `custom_entities`, and `suffix` (e.g., `["func", "_task-rest_bold"]` → `datatype: func`, `custom_entities: ["task-rest"]`, `suffix: bold`). `run-*`, `echo-*`, and `acq-*` entities are stripped — dcm2bids assigns run/echo numbering automatically, and `acq-*` from BidsGuess (typically a protocol-name shorthand like `acq-epfid2p3`) is replaced by a SeriesDescription-derived `acq-<label>` only when needed for disambiguation.
   - One description is emitted per unique identity, where identity is the first non-empty of `SeriesDescription`, `ProtocolName`, or `SidecarFilename` (basename). The chosen field becomes the description's `criteria`. Multiple sidecars sharing one identity (e.g., multi-run / multi-echo series with the same `SeriesDescription`) collapse into a single description.
   - When two or more identities map to the same `(datatype, custom_entities, suffix)` slot, the disambiguator first looks for phase-encoding direction codes (`AP`, `PA`, `RL`, `LR`, `SI`, `IS`) inside each identity. A code only matches when bordered by non-letters (so `MAPS`, `ISIS`, `RAPID` are not false positives); the *last* such code in the identity wins.
     - Identities sharing a detected code (or none) get bucketed; each bucket gets a `dir-<code>` entity and emits its own description, with the matched code masked out before computing any further `acq-` label. If a bucket still has multiple identities after this, an `acq-<label>` is added — the minimal substring that distinguishes them (longest common prefix and suffix removed, sanitized to `[A-Za-z0-9]`); when minimal-diff fails to yield unique non-empty labels, the full sanitized identity is used instead.
     - If `BidsGuess` already contained a `dir-XX` for the slot, no promotion happens (the entity is already there); but if any identity contains a *different* direction code, a loud warning is printed flagging the inconsistency.
     - When a slot has only one identity, no `dir-` promotion happens — the rule is conflict-only.
   - Sidecars with missing or empty `BidsGuess` are skipped with a warning.
   - If `<target>/dcm2bids_config.json` already exists, the command refuses to overwrite and exits with an error.

```bash
xnatcli bidsprep PATH/TO/PROJECT/SUBJECT/EXPERIMENT -o OUTPUT_DIR
```

Multiple experiments from the same project intentionally share one project bidsprep directory; running `bidsprep` for a second experiment from the same project will overwrite the first.

| Argument | Description |
| --- | --- |
| `EXPERIMENT_DIR` | Path to a downloaded XNAT experiment directory. Its parent directory name is treated as `SUBJECT` and its grandparent as `PROJECT`. |
| `-o`, `--output` | **Required.** Directory under which `PROJECT-<PROJECT>_bidsprep/` is created (the parent directory is created if missing). |

## `xnatcli bidsconvert`

Converts XNAT-downloaded sessions to BIDS via [`Dcm2Bids`](https://unfmontreal.github.io/Dcm2Bids/), one or many at a time. The input directory follows the layout produced by `xnatcli download` (`<input>/PROJECT/SUBJECT/EXPERIMENT/scans/...`); the output is a per-project BIDS dataset at `<output>/PROJECT/sub-<PARTICIPANT>/ses-<SESSION>/`.

1. Validates `--input`, `--config`, and that `dcm2bids` and `dcm2niix` are on `PATH`. Imports `pydicom` (used to confirm a session has at least one readable DICOM before running the conversion).
2. Resolves the set of sessions to convert from one of the mutually exclusive selectors:
   - `-1 PROJECT SUBJECT EXPERIMENT` — exactly one session.
   - `-s/--subject PROJECT SUBJECT` — every `EXPERIMENT` directory under that subject.
   - `-p/--project PROJECT` — every `EXPERIMENT` directory under every subject in the project.
3. For each session:
   - Walks `<input>/PROJECT/SUBJECT/EXPERIMENT/scans/` recursively for files with extension `.dcm` or `.IMA` (case-insensitive) and tries to read the first match with `pydicom.dcmread(stop_before_pixels=True)`. If no readable DICOM is found, the session is marked `EMPTY`.
   - Derives the BIDS labels from the directory names: `PARTICIPANT` is the `SUBJECT` directory name with non-`[A-Za-z0-9]` characters stripped, `SESSION` is the `EXPERIMENT` directory name similarly stripped (case preserved). Because `xnatcli download` writes labels (not XNAT IDs) for these directories, the resulting BIDS labels are derived from human-readable identifiers.
   - If `<output>/PROJECT/sub-<PARTICIPANT>/ses-<SESSION>/` already has contents, prints a `WARNING:` line; the conversion proceeds with `--clobber`.
   - Invokes `dcm2bids -d <scans_dir> -p <PARTICIPANT> -s <SESSION> -c <CONFIG_FILE> -o <output>/PROJECT --clobber`.
4. Sessions are processed serially or in parallel (`-n/--nconvert`); a one-line per-session status is printed, and a summary is printed at the end. With `-l/--log`, a CSV identical in shape to `download`'s log (`DATESTAMP,PROJECT,SUBJECT,EXPERIMENT,STATUS`) is written to `<output>/log/bidsconvert_<YYYYMMDD_HHMMSS>_log.csv`.
5. With `-d/--delete`, after a session finishes with `STATUS=COMPLETE` or `STATUS=EMPTY`, its input directory `<input>/PROJECT/SUBJECT/EXPERIMENT` is removed (via `shutil.rmtree`). The `SUBJECT` and then `PROJECT` parent directories are also removed if they become empty as a result. `FAILURE` and `NONEXISTENT` sessions are left untouched. Deletion happens after the per-session log row is written, so the log still records what was converted before removal.

```bash
# One session
xnatcli bidsconvert -i DOWNLOAD_DIR -1 PROJECT SUBJECT EXPERIMENT -o OUTPUT_DIR -c PATH/TO/dcm2bids_config.json

# All sessions of one subject, 4 in parallel, with a log
xnatcli bidsconvert -i DOWNLOAD_DIR -s PROJECT SUBJECT -o OUTPUT_DIR -c PATH/TO/dcm2bids_config.json -n 4 -l

# All sessions of all subjects in a project
xnatcli bidsconvert -i DOWNLOAD_DIR -p PROJECT -o OUTPUT_DIR -c PATH/TO/dcm2bids_config.json
```

### Per-session STATUS (and exit code)

| STATUS | Meaning |
| --- | --- |
| `COMPLETE` | `dcm2bids` exited 0. |
| `FAILURE` | `dcm2bids` exited non-zero, or sanitized PARTICIPANT/SESSION came out empty. |
| `NONEXISTENT` | The session directory `<input>/PROJECT/SUBJECT/EXPERIMENT` does not exist on disk. |
| `EMPTY` | The session directory exists but no readable `.dcm`/`.IMA` DICOMs were found under `scans/`. |

Exit code is `0` if every processed session is `COMPLETE` or `EMPTY`, and `1` otherwise.

| Argument | Description |
| --- | --- |
| `-i`, `--input` | **Required.** Directory holding `PROJECT/SUBJECT/EXPERIMENT` subdirectories. |
| `-1 PROJECT SUBJECT EXPERIMENT` | Convert a single session. Mutually exclusive with `-s` and `-p`. Values must match the directory names under `INPUT_DIR`. |
| `-s`, `--subject PROJECT SUBJECT` | Convert all sessions of one subject. |
| `-p`, `--project PROJECT` | Convert all sessions of all subjects in a project. |
| `-o`, `--output` | **Required.** Directory under which `PROJECT/sub-X/ses-Y/` is written. |
| `-c`, `--config` | **Required.** Path to the `dcm2bids` config JSON (typically the one drafted by `xnatcli bidsprep`). |
| `-n`, `--nconvert` | *Optional.* Number of parallel session conversions (default `1`). |
| `-l`, `--log` | *Optional.* Write a per-session log CSV to `<output>/log/bidsconvert_<YYYYMMDD_HHMMSS>_log.csv`. |
| `-d`, `--delete` | *Optional.* After a session finishes with `STATUS=COMPLETE` or `STATUS=EMPTY`, delete its input directory `<input>/PROJECT/SUBJECT/EXPERIMENT`. Empty `SUBJECT` and `PROJECT` parent directories are also pruned. |

## `xnatcli cubids`

Runs [`CuBIDS`](https://cubids.readthedocs.io/) on a BIDS dataset produced by `xnatcli bidsconvert` — first `cubids add-nifti-info` (which annotates JSON sidecars with NIfTI header fields) and then `cubids group` (which groups acquisitions by their parameters and writes `_summary.tsv`, `_files.tsv`, `_AcqGrouping.tsv`, and `_AcqGroupInfo.txt`).

1. Validates that `--input` is an existing directory and that `INPUT_DIR/<PROJECT>/` (the BIDS dataset) exists. The expected layout is the one produced by `xnatcli bidsconvert` — `<input>/PROJECT/sub-X/ses-Y/...`.
2. Verifies that `cubids` is on `PATH`; exits with an error if it is missing.
3. Creates the output directory `INPUT_DIR/PROJECT-<PROJECT>_cubids/` if it does not already exist. If it does, the directory is reused (CuBIDS writes its own outputs into it).
4. If `INPUT_DIR/<PROJECT>/tmp_dcm2bids/` exists (leftover dcm2bids scratch), it is moved out to `INPUT_DIR/.<PROJECT>_cubids_stash_tmp_dcm2bids/` for the duration of the run so CuBIDS does not scan it, and moved back when the run finishes (success or failure). CuBIDS has no built-in ignore mechanism; it walks the whole BIDS tree.
5. Invokes `cubids add-nifti-info <bids_dir>` without `--use-datalad` (datalad is disabled by default in CuBIDS), so the BIDS dataset itself is mutated in place to add NIfTI header info to sidecars.
6. Invokes `cubids group <bids_dir> v0`, which writes `v0_summary.tsv`, `v0_files.tsv`, `v0_AcqGrouping.tsv`, and `v0_AcqGroupInfo.txt` into `INPUT_DIR/<PROJECT>/code/CuBIDS/`.
7. On a successful `group`, the `INPUT_DIR/<PROJECT>/code/CuBIDS/` directory is merged into `INPUT_DIR/PROJECT-<PROJECT>_cubids/CuBIDS/` (existing files with the same name are overwritten; unrelated files in the destination are left alone) and the source is removed. If `INPUT_DIR/<PROJECT>/code/` is empty afterwards, it is also removed.
8. If `add-nifti-info` exits non-zero, `group` is skipped and the command exits `1`. Otherwise the exit code is `0` if both steps succeeded and `1` if `group` failed.

```bash
xnatcli cubids -i BIDSCONVERT_OUTPUT_DIR -p PROJECT
```

For example, if you ran `xnatcli bidsconvert -i DOWNLOAD_DIR -p MYPROJ -o /data/bids -c config.json`, the BIDS dataset lives at `/data/bids/MYPROJ/`, and `xnatcli cubids -i /data/bids -p MYPROJ` writes CuBIDS outputs to `/data/bids/PROJECT-MYPROJ_cubids/CuBIDS/v0_*.tsv`.

| Argument | Description |
| --- | --- |
| `-i`, `--input` | **Required.** Parent directory holding the BIDS dataset at `INPUT_DIR/PROJECT/` (i.e., the output of `xnatcli bidsconvert`). The CuBIDS output subdirectory is created here. |
| `-p`, `--project` | **Required.** Project directory name under `INPUT_DIR` identifying the BIDS dataset to process. |
| `-l`, `--log` | *Optional.* Write a per-step log CSV to `INPUT_DIR/PROJECT-<PROJECT>_cubids/log/cubids_<YYYYMMDD_HHMMSS>_log.csv` with header `DATESTAMP,PROJECT,STEP,STATUS`. One row per CuBIDS step (`add-nifti-info`, `group`), each with `STATUS` of `COMPLETE` or `FAILURE`. |
