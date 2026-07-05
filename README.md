# XNAT CLI for BIDS

A command-line interface for logging into an Extensible Neuroimaging Archive Toolkit (XNAT) server, querying experiments and downloading files, then converting to the Brain Imaging Data Structure (BIDS) standard format.

## Contents

- [`src/xnatcli/`](src/xnatcli/) — installable package that provides the `xnatcli` CLI (`xnatcli login`, `xnatcli download`, `xnatcli query`, `xnatcli mrihelp`, `xnatcli mriconvert`, `xnatcli cubids`, `xnatcli mrimap`, `xnatcli physioconvert`), built on [PyXNAT](https://pyxnat.github.io/pyxnat/index.html).

## Setup

The project uses [uv](https://docs.astral.sh/uv/) and Python ≥ 3.11. Runtime dependencies: `pyxnat`, `dcm2bids`, `dcm2niix` (the [`dcm2niix`](https://pypi.org/project/dcm2niix/) PyPI package vendors the binary onto your `PATH`), `pydicom`, `cubids`, `nibabel` (used to read NIfTI shapes for the `mriconvert` `scans.tsv`), `pandas` (used by `mrimap`), and `phys2bids` (used by `physioconvert` to read physiological recordings and write BIDS physio files; see the note under [`xnatcli physioconvert`](#xnatcli-physioconvert) about its `numpy` pin). `bioread` is also pulled in for `phys2bids` to read BIOPAC `.acq` files (phys2bids imports it lazily but does not depend on it directly).

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
- [`src/xnatcli/mrihelp.py`](src/xnatcli/mrihelp.py) — runs `dcm2bids_helper` against a downloaded experiment directory.
- [`src/xnatcli/mriconvert.py`](src/xnatcli/mriconvert.py) — converts downloaded XNAT sessions to BIDS via `dcm2bids`.
- [`src/xnatcli/cubids.py`](src/xnatcli/cubids.py) — runs [`CuBIDS`](https://cubids.readthedocs.io/) `add-nifti-info` and `group` on a BIDS dataset.
- [`src/xnatcli/mrimap.py`](src/xnatcli/mrimap.py) — generates/updates a participant/session mapping TSV for a BIDS dataset (uses [`pandas`](https://pandas.pydata.org/)).
- [`src/xnatcli/physioconvert.py`](src/xnatcli/physioconvert.py) — converts physiological recordings found under a directory tree to BIDS physio files (uses [`phys2bids`](https://phys2bids.readthedocs.io/)).

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

## `xnatcli mrihelp`

Runs `dcm2bids_helper` (from [`Dcm2Bids`](https://unfmontreal.github.io/Dcm2Bids/)) on one or many downloaded XNAT experiment directories and drafts a project-level dcm2bids config. The input directory follows the layout produced by `xnatcli download` (`<input>/PROJECT/SUBJECT/EXPERIMENT/scans/...`).

1. Validates `--input` and verifies that both `dcm2bids_helper` and `dcm2niix` are on `PATH`.
2. Resolves the set of experiments to process from one of the mutually exclusive selectors:
   - `-1 PROJECT SUBJECT EXPERIMENT` — exactly one experiment.
   - `-s/--subject PROJECT SUBJECT` — every `EXPERIMENT` directory under that subject.
   - `-p/--project PROJECT` — every `EXPERIMENT` directory under every subject in the project.
3. Computes the target directory `OUTPUT_DIR/PROJECT-<PROJECT>_mrihelp/` and creates it if missing. Existing contents are kept; per-experiment helper runs overwrite their own nested subdirectory.
4. For each experiment, invokes `dcm2bids_helper -d <input>/PROJECT/SUBJECT/EXPERIMENT/scans -o <target> -n EXPERIMENT --force`. The `-n` flag nests each experiment's NIfTI/JSON outputs under `<target>/tmp_dcm2bids/helper/<EXPERIMENT>/` so runs do not collide; `--force` lets re-runs overwrite stale files within that subdirectory. Experiments are processed serially or in parallel (`-n/--nprep`, one helper invocation per experiment per worker).
5. After every experiment has been processed (regardless of individual success), drafts a project-level dcm2bids config at `<target>/dcm2bids_config.json` by aggregating sidecars from **every** `<target>/tmp_dcm2bids/helper/<EXPERIMENT>/*.json` present on disk (including prior runs):
   - For each sidecar with a `BidsGuess` field (set by recent `dcm2niix` releases), parses it into `datatype`, `custom_entities`, and `suffix` (e.g., `["func", "_task-rest_bold"]` → `datatype: func`, `custom_entities: ["task-rest"]`, `suffix: bold`). `run-*`, `echo-*`, and `acq-*` entities are stripped — dcm2bids assigns run/echo numbering automatically, and `acq-*` from BidsGuess (typically a protocol-name shorthand like `acq-epfid2p3`) is replaced by a SeriesDescription-derived `acq-<label>` only when needed for disambiguation.
   - One description is emitted per unique identity, where identity is the first non-empty of `SeriesDescription`, `ProtocolName`, or `SidecarFilename` (basename). The chosen field becomes the description's `criteria`. Multiple sidecars sharing one identity (e.g., multi-run / multi-echo series with the same `SeriesDescription`, including across experiments) collapse into a single description.
   - When two or more identities map to the same `(datatype, custom_entities, suffix)` slot, the disambiguator first looks for phase-encoding direction codes (`AP`, `PA`, `RL`, `LR`, `SI`, `IS`) inside each identity. A code only matches when bordered by non-letters (so `MAPS`, `ISIS`, `RAPID` are not false positives); the *last* such code in the identity wins.
     - Identities sharing a detected code (or none) get bucketed; each bucket gets a `dir-<code>` entity and emits its own description, with the matched code masked out before computing any further `acq-` label. If a bucket still has multiple identities after this, an `acq-<label>` is added — the minimal substring that distinguishes them (longest common prefix and suffix removed, sanitized to `[A-Za-z0-9]`); when minimal-diff fails to yield unique non-empty labels, the full sanitized identity is used instead.
     - If `BidsGuess` already contained a `dir-XX` for the slot, no promotion happens (the entity is already there); but if any identity contains a *different* direction code, a loud warning is printed flagging the inconsistency.
     - When a slot has only one identity, no `dir-` promotion happens — the rule is conflict-only.
   - Sidecars with missing or empty `BidsGuess` are skipped with a warning.
   - If `<target>/dcm2bids_config.json` already exists, the new draft is written to `<target>/dcm2bids_config_<YYYYMMDD_HHMMSS>.json` instead (rename or delete the old file if you want to claim the canonical filename).

```bash
# One experiment
xnatcli mrihelp -i DOWNLOAD_DIR -1 PROJECT SUBJECT EXPERIMENT -o OUTPUT_DIR

# All experiments of one subject, 4 in parallel
xnatcli mrihelp -i DOWNLOAD_DIR -s PROJECT SUBJECT -o OUTPUT_DIR -n 4

# All experiments of all subjects in a project
xnatcli mrihelp -i DOWNLOAD_DIR -p PROJECT -o OUTPUT_DIR
```

Per-experiment helper output is uniformly nested under `<target>/tmp_dcm2bids/helper/<EXPERIMENT>/` regardless of which selector was used. Multiple `mrihelp` invocations against the same project accumulate: each run refreshes the helper subdir(s) it touches (via `--force`) and re-drafts the project-level config by aggregating across **all** nested helper subdirectories present on disk.

### Per-experiment helper STATUS (and exit code)

| STATUS | Meaning |
| --- | --- |
| `COMPLETE` | `dcm2bids_helper` exited 0. |
| `FAILURE` | `dcm2bids_helper` exited non-zero, or `<input>/PROJECT/SUBJECT/EXPERIMENT/scans/` does not exist. |

Exit code is `0` if every processed experiment is `COMPLETE`, and `1` otherwise. The config draft is attempted regardless.

With `-l/--log`, a CSV identical in shape to `download`'s and `mriconvert`'s logs (`DATESTAMP,PROJECT,SUBJECT,EXPERIMENT,STATUS`) is written to `OUTPUT_DIR/log/mrihelp_<YYYYMMDD_HHMMSS>_log.csv` (local time, captured at run start). One row is appended per processed experiment; rows are written under a lock so concurrent workers do not interleave.

With `-d/--delete`, every `*.nii.gz` file in each experiment's helper subdir (`OUTPUT_DIR/PROJECT-<PROJECT>_mrihelp/tmp_dcm2bids/helper/<EXPERIMENT>/`) is removed right after `dcm2bids_helper` returns for that experiment, regardless of STATUS. JSON sidecars are kept — the project-level config draft only needs the JSONs, and the NIfTI images are typically far larger. The per-experiment status line gets a trailing `(removed N .nii.gz)` so the deletion is visible. Use this when you only need the drafted config and not the helper-stage NIfTIs.

| Argument | Description |
| --- | --- |
| `-i`, `--input` | **Required.** Root directory holding `PROJECT/SUBJECT/EXPERIMENT` subdirectories. |
| `-1 PROJECT SUBJECT EXPERIMENT` | Run helper on a single experiment. Mutually exclusive with `-s` and `-p`. Values must match the directory names under `INPUT_DIR`. |
| `-s`, `--subject PROJECT SUBJECT` | Run helper on every experiment of one subject. |
| `-p`, `--project PROJECT` | Run helper on every experiment of every subject in a project. |
| `-o`, `--output` | **Required.** Directory under which `PROJECT-<PROJECT>_mrihelp/` is created (the parent directory is created if missing). |
| `-n`, `--nprep` | *Optional.* Number of parallel dcm2bids_helper invocations, one per experiment per worker (default `1`). |
| `-l`, `--log` | *Optional.* Write a per-experiment log CSV to `OUTPUT_DIR/log/mrihelp_<YYYYMMDD_HHMMSS>_log.csv`. |
| `-d`, `--delete` | *Optional.* After each experiment's helper run, delete `*.nii.gz` from its `tmp_dcm2bids/helper/<EXPERIMENT>/` subdir. JSON sidecars are kept. |
| `-m`, `--maps` | *Optional.* Skip running `dcm2bids_helper` and only (re)draft the config from the helper JSON sidecars already under `OUTPUT_DIR/PROJECT-<PROJECT>_mrihelp/`. `dcm2bids_helper`/`dcm2niix` are not required. |

## `xnatcli mriconvert`

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
4. Sessions are processed serially or in parallel (`-n/--nconvert`); a one-line per-session status is printed, and a summary is printed at the end. With `-l/--log`, a CSV identical in shape to `download`'s log (`DATESTAMP,PROJECT,SUBJECT,EXPERIMENT,STATUS`) is written to `<output>/log/mriconvert_<YYYYMMDD_HHMMSS>_log.csv`.
5. With `-d/--delete`, after a session finishes with `STATUS=COMPLETE` or `STATUS=EMPTY`, its input directory `<input>/PROJECT/SUBJECT/EXPERIMENT` is removed (via `shutil.rmtree`). The `SUBJECT` and then `PROJECT` parent directories are also removed if they become empty as a result. `FAILURE` and `NONEXISTENT` sessions are left untouched. Deletion happens after the per-session log row is written, so the log still records what was converted before removal.
6. After all sessions are processed, a root-level `scans.tsv` is (re)written at `<output>/PROJECT/scans.tsv`, and the static data dictionary [`src/assets/scans.json`](src/assets/scans.json) is copied alongside it as `<output>/PROJECT/scans.json`. See [Root-level `scans.tsv`](#root-level-scanstsv) below.

```bash
# One session
xnatcli mriconvert -i DOWNLOAD_DIR -1 PROJECT SUBJECT EXPERIMENT -o OUTPUT_DIR -c PATH/TO/dcm2bids_config.json

# All sessions of one subject, 4 in parallel, with a log
xnatcli mriconvert -i DOWNLOAD_DIR -s PROJECT SUBJECT -o OUTPUT_DIR -c PATH/TO/dcm2bids_config.json -n 4 -l

# All sessions of all subjects in a project
xnatcli mriconvert -i DOWNLOAD_DIR -p PROJECT -o OUTPUT_DIR -c PATH/TO/dcm2bids_config.json
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
| `-c`, `--config` | **Required** unless `-m/--maps` is given. Path to the `dcm2bids` config JSON (typically the one drafted by `xnatcli mrihelp`). |
| `-n`, `--nconvert` | *Optional.* Number of parallel session conversions (default `1`). |
| `-l`, `--log` | *Optional.* Write a per-session log CSV to `<output>/log/mriconvert_<YYYYMMDD_HHMMSS>_log.csv`. |
| `-d`, `--delete` | *Optional.* After a session finishes with `STATUS=COMPLETE` or `STATUS=EMPTY`, delete its input directory `<input>/PROJECT/SUBJECT/EXPERIMENT`. Empty `SUBJECT` and `PROJECT` parent directories are also pruned. |
| `-m`, `--maps` | *Optional.* Skip the `dcm2bids` conversion and only (re)generate `scans.tsv` (and copy `scans.json`) for every project in scope from the already-converted BIDS data under `OUTPUT_DIR`. `-c/--config`, `pydicom`, `dcm2bids`, and `dcm2niix` are not required. |

### Root-level `scans.tsv`

At the end of every `mriconvert` run, a single dataset-wide `scans.tsv` is (re)generated at the project root, `<output>/PROJECT/scans.tsv`. It is rebuilt from scratch each run by walking the dataset with `os.walk` for every `.nii.gz` file (the dcm2bids `tmp_dcm2bids/` scratch directory is skipped). The static data dictionary [`src/assets/scans.json`](src/assets/scans.json) is copied next to it as `<output>/PROJECT/scans.json`. Reading NIfTI shapes uses `nibabel`; if it is not installed, the `dimensions` column is left empty and a warning is printed.

To protect manual review work, an existing `scans.tsv` is **not** overwritten if any of its reviewer columns (`rename`, `recommend_for_use`, `complete`, `usable`, `qc_rating`, `rating_reason`, `qc_notes`) hold any text — the generator prints a warning and leaves the file untouched. A `scans.tsv` whose reviewer columns are all still empty is regenerated normally.

Whenever a `scans.tsv` already exists, the freshly generated rows are compared against it and every difference in a **non-user (generator-owned)** field — `acq_time`, `series_number`, `dimensions`, `size_bytes`, `bids_name`, `participant_id`, `session_id`, `datatype`, `task`, `acquisition`, `echo`, `run`, `suffix`, plus files added or removed on disk — is reported as one `WARNING` per deviation to stdout and to a log file at `<output>/log/scans_deviations_<PROJECT>_<YYYYMMDD_HHMMSS>.log`. The log is written only when there is at least one deviation. (When `nibabel` is unavailable, `dimensions` is excluded from the comparison so its empty values are not flagged.) Reviewer-column edits are never reported as deviations.

The columns, in order:

| Column | Source |
| --- | --- |
| `filename` | Path relative to the `PROJECT` root, POSIX separators, no leading `./` or `/`. |
| `acq_time` | `AcquisitionTime` from the file's JSON sidecar (empty if absent). |
| `series_number` | `SeriesNumber` from the file's JSON sidecar (empty if absent). |
| `dimensions` | `nibabel` image shape, `x`-joined, always padded to include the 4th dimension even when it is `1` (e.g. `256x256x170x1`). |
| `size_bytes` | File size on disk, in bytes. |
| `bids_name` | The basename between the `sub-<label>_ses-<label>_` prefix and the `.nii.gz` extension. |
| `rename` | Empty — for the end-user to record a corrected `bids_name`. |
| `recommend_for_use` | Empty — end-user `TRUE`/`FALSE` review field. |
| `complete` | Empty — end-user `TRUE`/`FALSE` review field (acquired at full intended length). |
| `usable` | Empty — end-user `TRUE`/`FALSE` review field. |
| `qc_rating` | Empty — end-user `PASS`/`FAIL`/`UNCERTAIN` review field. |
| `rating_reason` | Empty — free-text reason for `qc_rating`. |
| `qc_notes` | Empty — free-text QC notes. |
| `participant_id` | `sub-<label>` parsed from the filename. |
| `session_id` | `ses-<label>` parsed from the filename. |
| `datatype` | Name of the file's parent directory (e.g. `anat`, `func`). |
| `task` | The full `task-<label>` entity (prefix included), or empty. |
| `acquisition` | The full `acq-<label>` entity (prefix included), or empty. |
| `echo` | The full `echo-<index>` entity (prefix included), or empty. |
| `run` | The full `run-<index>` entity (prefix included), or empty. |
| `suffix` | Basename portion after the last underscore (never empty). |

## `xnatcli cubids`

Runs [`CuBIDS`](https://cubids.readthedocs.io/) on a BIDS dataset produced by `xnatcli mriconvert` — first `cubids add-nifti-info` (which annotates JSON sidecars with NIfTI header fields) and then `cubids group` (which groups acquisitions by their parameters and writes `_summary.tsv`, `_files.tsv`, `_AcqGrouping.tsv`, and `_AcqGroupInfo.txt`).

1. Validates that `--input` is an existing directory and that `INPUT_DIR/<PROJECT>/` (the BIDS dataset) exists. The expected layout is the one produced by `xnatcli mriconvert` — `<input>/PROJECT/sub-X/ses-Y/...`.
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

For example, if you ran `xnatcli mriconvert -i DOWNLOAD_DIR -p MYPROJ -o /data/bids -c config.json`, the BIDS dataset lives at `/data/bids/MYPROJ/`, and `xnatcli cubids -i /data/bids -p MYPROJ` writes CuBIDS outputs to `/data/bids/PROJECT-MYPROJ_cubids/CuBIDS/v0_*.tsv`.

| Argument | Description |
| --- | --- |
| `-i`, `--input` | **Required.** Parent directory holding the BIDS dataset at `INPUT_DIR/PROJECT/` (i.e., the output of `xnatcli mriconvert`). The CuBIDS output subdirectory is created here. |
| `-p`, `--project` | **Required.** Project directory name under `INPUT_DIR` identifying the BIDS dataset to process. |
| `-l`, `--log` | *Optional.* Write a per-step log CSV to `INPUT_DIR/PROJECT-<PROJECT>_cubids/log/cubids_<YYYYMMDD_HHMMSS>_log.csv` with header `DATESTAMP,PROJECT,STEP,STATUS`. One row per CuBIDS step (`add-nifti-info`, `group`), each with `STATUS` of `COMPLETE` or `FAILURE`. |

## `xnatcli mrimap`

Generates a participant/session mapping TSV for a BIDS dataset (the output of `xnatcli mriconvert`, at `INPUT_DIR/PROJECT/`). The map is later filled in by hand to relate XNAT IDs and real dates to anonymized BIDS IDs and session codenames. When `-o OUTPUT_DIR` is provided, it additionally applies all filled-in renames by copying the BIDS dataset to a new directory tree.

### Map TSV generation (always runs)

1. Validates that `--input` exists and that the BIDS dataset `INPUT_DIR/PROJECT/` exists.
2. Scans `INPUT_DIR/PROJECT/` for `sub-*` directories and, within each, `ses-*` subdirectories.
3. Writes `INPUT_DIR/PROJECT-<PROJECT>_mrimap.tsv` with the columns `participant_id`, `participant_rename`, `session_id`, `session_rename`. One row is emitted per `(participant, session)` pair, with the two `*_rename` columns left blank for later editing. Rows are sorted alphanumerically by `participant_id` then `session_id`.
   - If the dataset has **no** sessions (no participant has any `ses-*` subdirectory), only the `participant_id` and `participant_rename` columns are written, one row per participant.
   - If the dataset uses sessions but a particular participant has no `ses-*` subdirectory, that participant is skipped with a warning.
4. If `PROJECT-<PROJECT>_mrimap.tsv` already exists, a fresh blank map is generated and compared to it (with `pandas`): any `(participant_id, session_id)` pairs not already present are appended, and all existing rows — including any `*_rename` values already filled in — are preserved. The merged table is re-sorted and rewritten.

```bash
xnatcli mrimap -i MRICONVERT_OUTPUT_DIR -p PROJECT
```

### Copy-with-rename (`-o OUTPUT_DIR`)

When `-o` is provided, after updating the map TSV the command reads back the renames from two sources and writes a fully renamed copy of the BIDS dataset to `OUTPUT_DIR/PROJECT/`:

- **`PROJECT-<PROJECT>_mrimap.tsv`** — `participant_rename` and `session_rename` columns rename `sub-*` and `ses-*` directory names and the matching labels embedded in all filenames. Blank values mean "keep the original label."
- **`INPUT_DIR/PROJECT/scans.tsv`** — the `rename` column supplies a corrected `bids_name` (the part after `sub-X_ses-Y_`) for individual `.nii.gz` files. Sidecar files (`.json`, `.bval`, `.bvec`) sharing the same stem are renamed to match. Blank values mean "keep the original bids_name."

The copy also:

- **QC filtering**: Files whose `scans.tsv` row has `recommend_for_use`, `complete`, or `usable` set to exactly `"FALSE"`, or `qc_rating` set to exactly `"FAIL"` or `"UNCERTAIN"`, are excluded from the copy (along with their `.json`/`.bval`/`.bvec` sidecars) and their rows are omitted from the output `scans.tsv`. Values in any of these columns that are non-empty but do not match a valid Level from `scans.json` (e.g. `"false"` instead of `"FALSE"`) generate an additional warning, since they are silently ignored by the filter.
- Updates `participants.tsv` in the output with the renamed participant IDs.
- Updates `scans.tsv` in the output (`filename`, `bids_name`, `participant_id`, `session_id` columns) to reflect all renames, omits rows for QC-excluded files, and drops the columns `rename`, `task`, `acquisition`, `echo`, `run`, and `suffix` (the rename has been applied; the rest are redundant with the filename). All other reviewer columns are preserved.
- Skips `tmp_dcm2bids` and `tmp_phys2bids` scratch directories.
- Errors and copies nothing if `OUTPUT_DIR/PROJECT/` already exists.
- Warns loudly for any two source files that would map to the same destination path (neither is copied); all warnings are re-displayed together at the end.

```bash
xnatcli mrimap -i MRICONVERT_OUTPUT_DIR -p PROJECT -o RENAMED_OUTPUT_DIR
```

For example, if the BIDS dataset lives at `/data/bids/MYPROJ/`, then:

- `xnatcli mrimap -i /data/bids -p MYPROJ` writes `/data/bids/PROJECT-MYPROJ_mrimap.tsv`.
- After filling in the rename columns, `xnatcli mrimap -i /data/bids -p MYPROJ -o /data/renamed` copies the dataset to `/data/renamed/MYPROJ/` with all renames applied.

| Argument | Description |
| --- | --- |
| `-i`, `--input` | **Required.** BIDS root directory holding the dataset at `INPUT_DIR/PROJECT/`. The map TSV is written here as `PROJECT-<PROJECT>_mrimap.tsv`. |
| `-p`, `--project` | **Required.** Project directory name under `INPUT_DIR` identifying the BIDS dataset to scan. |
| `-o`, `--output` | *Optional.* When provided, copy the BIDS dataset to `OUTPUT_DIR/PROJECT/` with all renames from the map TSV and `scans.tsv` `rename` column applied. `OUTPUT_DIR/PROJECT/` must not already exist. |

## `xnatcli physioconvert`

Walks a directory tree for physiological recordings and converts each one to [BIDS physiological recordings](https://bids-specification.readthedocs.io/en/stable/modality-specific-files/physiological-recordings.html) (`_physio.tsv.gz` + `_physio.json`) inside a BIDS project directory, using [`phys2bids`](https://phys2bids.readthedocs.io/) (imported as a Python library) to read the files and write the BIDS output.

1. Validates `--input` and creates `--output` if missing; exits with a message if `phys2bids` is unavailable.
2. Recursively finds every file whose extension `phys2bids` supports (`.acq`, `.txt`, `.mat`, `.gep`, `.smr`; case-insensitive) under `--input`, skipping anything already inside `--output`.
3. **Validates that each match is really physiological data** by loading it with the matching `phys2bids` loader — not just trusting the extension. Files that fail to load (e.g. a stray `.txt` that is not a recording) are reported as `NOT_PHYSIO` and skipped.
4. Writes/updates a `physioconvert_map.tsv` at the `--output` root, one row per validated physio file. The BIDS entity columns (`participant_id`, `session_id`, `task_id`, `acquisition_id`, `run_id`, `datatype`) are best-guesses parsed from the input file's basename, split on underscores:
   - **`participant_id`** — the first token (filenames are expected to start with the participant label), written as `sub-<token>` (sanitized to alphanumerics).
   - **`session_id`** — the acquisition date (found among the tokens after the participant — it need not sit immediately after it), emitted as `ses-YYYYMMDD`. Four formats are recognized, in order: Format 1 `DDMMMYY` (e.g. `23May18` → `20180523`, with `20` assumed as the century); Format 2 `YYYYMMDD` (used as-is); Format 3 `MMDDYYYY` and Format 4 `DDMMYYYY` (year last), disambiguated only when exactly one of the two leading pairs exceeds 12. The less common `YYYY-MM-DDTHH_MM_SS` form is checked last. **The parsed date is verified against the file's last-modified date** (see `DATES_DISAGREE` below); when there is no date token, or the `MMDD`/`DDMM` order is ambiguous, the last-modified date is used.
   - **`run_id`** — a trailing 4-digit run number becomes a zero-padded 2-digit `run-` entity (e.g. `0000` → `run-00`, `0012` → `run-12`).
   - **Missing underscores** are handled for the date and run: a token that glues a date to a trailing 4-digit run is split first, so e.g. `RPD123_050620260000` parses as `sub-RPD123` + date `05062026` + `run-00` (likewise `23May180000` → `23May18` + `run-00`). This keeps the date and run out of `acquisition_id`. (The participant ID is otherwise expected to be the first underscore-delimited token.)
   - **Fully concatenated names** (no underscores at all) are split when they contain an embedded `DDMMMYY` date — its 3-letter month is an unambiguous anchor: text before the date is the ID and text after is the run. For example `RPD12309Oct190003` → `sub-RPD123` + date `09Oct19` (`20191009`) + `run-03`. Fully-numeric concatenations are **not** split this way (there is no reliable ID/date boundary when the ID also contains digits).
   - **`acquisition_id`** — any remaining tokens (those that are neither the participant, the date, nor the run) are concatenated, sanitized, and written as `acq-<label>`.
   - **`datatype`** is not encoded in filenames, so it defaults to `func`; change it in the map for non-functional recordings.

   Existing user edits in `physioconvert_map.tsv` take precedence over these guesses. The map's only regenerated column is `status` (refreshed every run); the per-file metrics, including the converted output path(s), live in `physioconvert_qc.tsv` (below). The map's rows are sorted by BIDS entity — `acquisition_id`, then `run_id`, `task_id`, `session_id`, and `participant_id` — so related recordings group together (the `physioconvert_qc.tsv` stays sorted by `source_path`).
5. Runs the `phys2bids` workflow into a temporary directory for each validated physio file **that has not already been converted**, then relocates its `.tsv.gz`/`.json` output(s). The JSON sidecar (with `SamplingFrequency`, `StartTime`, `Columns`) is produced by `phys2bids` and carried over as-is. A file is treated as **already converted** when a prior `physioconvert_map.tsv` shows `status=CONVERTED`, `DATES_DISAGREE`, or `UNKNOWN_NAME` for it **and** all of its `physioconvert_qc.tsv` `output_files` still exist on disk. Such a file is **never re-converted**; instead its existing outputs are **relocated/renamed to match its (possibly edited) map row** — exactly as `-m/--maps` does (see below) — so editing its entities and re-running always moves/renames the outputs without re-running `phys2bids`, and its preserved metrics are carried over with only the output paths refreshed. A file whose outputs are missing, or whose prior status was anything else (`CONVERT_ERROR`, `NOT_PHYSIO`, …), and every new file, is converted as usual. (Under `-m/--maps`, *every* file takes this relocate path and nothing is converted.)
   - **Resolved files** (`participant_id` filled) are moved into `<output>/sub-<P>/[ses-<S>/]<datatype>/`, renamed to the BIDS basename built from the map's entities, with `status=CONVERTED`. `task_id`, `session_id`, `acquisition_id`, and `run_id` are optional — when blank, that entity is simply omitted from the name.
   - **Unresolved files** (`participant_id` blank — i.e. the filename did not start with a participant label) are instead moved into `<output>/tmp_phys2bids/` keeping the input file's basename (the default name `phys2bids` assigns), with `status=UNKNOWN_NAME`. The layout there is flat; if two inputs share a basename, a `_1`, `_2`, … suffix is appended so nothing is overwritten. The original path (`source_path`) is recorded in both TSVs and the converted path (`output_files`) in `physioconvert_qc.tsv` (and per-output as `DESTINATION_PATH` in the log).
   - **Nothing is ever overwritten.** If a resolved file's BIDS name already exists (e.g. two recordings map to the same entities), a zero-padded `run-` entity is added (or an existing one incremented) until the name is free. A file's own outputs from a previous run are cleared first, so re-running is idempotent.
6. `phys2bids` automatically splits a recording whose channels have different sampling frequencies into one output file per frequency; for resolved files each such file is given a `recording-<label>` entity (the `<freq>Hz` suffix `phys2bids` assigns).
7. Writes/updates a **`physioconvert_qc.tsv`** alongside the map (same `source_path` index key and `status` column), holding the per-file metrics regenerated every run: `n_channels` (channel count, including the time channel), `sampling_frequencies` (unique channel frequencies in Hz, ascending), `output_files` (converted `_physio.tsv.gz` path(s)), `sample_count` (samples per frequency), and `duration_seconds` (acquisition length in seconds at 0.001 s precision, `sample_count / sampling_frequency`). `sampling_frequencies`, `sample_count`, and `duration_seconds` are comma-separated and aligned position-by-position, so a recording split across frequencies reports one entry per frequency in each. It contains the same rows as the map (one per `source_path`, including non-physio/missing files, with blank metrics).
8. Copies the static data dictionaries [`src/assets/physioconvert_map.json`](src/assets/physioconvert_map.json) and [`src/assets/physioconvert_qc.json`](src/assets/physioconvert_qc.json) into the `--output` root as `physioconvert_map.json` and `physioconvert_qc.json`, describing each TSV's columns.

**Fill in the blank entity columns in `physioconvert_map.tsv` and re-run** to place the `UNKNOWN_NAME` files under their proper BIDS path — user edits are preserved across runs, and once a file is placed correctly its earlier `tmp_phys2bids/` copy is deleted. **Editing any already-converted file's entities and re-running renames/moves its outputs to match, without re-running the (slow) `phys2bids` conversion** — a plain run relocates already-converted files (reusing their stored metrics) and only converts files that have no outputs yet. The dedicated `-m/--maps` mode does the same relocation for *every* file but never converts anything, and additionally re-reads each file to refresh its `physioconvert_qc.tsv` metrics (see below).

```bash
# Serial
xnatcli physioconvert -i PHYSIO_DIR -o BIDS_PROJECT_DIR

# 4 files converted in parallel
xnatcli physioconvert -i PHYSIO_DIR -o BIDS_PROJECT_DIR -n 4
```

With `-n/--nphysio` > 1, the `phys2bids` conversions run in parallel across separate **processes** (real parallelism, since `phys2bids` is an in-process Python library rather than an external command). The conversions run in workers, but all BIDS naming, collision/`run-` numbering, the `physioconvert_map.tsv`, and the log are written **serially in the main process**, and placement is drained in **sorted source-path order** (out-of-order completions are buffered until their turn). So results are fully deterministic and identical to a serial run, including which of two name-colliding files keeps the unnumbered name and which gets `run-NN` (the sorted-earlier source path wins) — regardless of `-n`.

### Per-file STATUS (and exit code)

| STATUS | Meaning |
| --- | --- |
| `CONVERTED` | `phys2bids` read the file and its `_physio.tsv.gz`/`.json` file(s) were written to the proper BIDS path. |
| `UNKNOWN_NAME` | The file is valid physio but `participant_id` is blank (the filename did not start with a participant label), so it was converted into `<output>/tmp_phys2bids/` under its input basename. Fill in `participant_id` in `physioconvert_map.tsv` and re-run to place it properly (the `tmp_phys2bids/` copy is removed then). |
| `DATES_DISAGREE` | The file converted to its proper BIDS path, but the date parsed from its filename disagrees with its last-modified date. The **last-modified date** was used for `session_id`; review and set `session_id` in `physioconvert_map.tsv` if the filename date is the correct one. |
| `NOT_PHYSIO` | The file matched a supported extension but could not be loaded as physiological data (so it is not added to the map, unless a prior row exists to preserve). |
| `READER_MISSING` | The optional reader package `phys2bids` needs for this format is not installed (e.g. `bioread` for `.acq`, `scipy` for `.mat`, `sonpy` for `.smr`). This is an environment problem, not a data problem — install the package and re-run. |
| `CONVERT_ERROR` | `phys2bids` raised while converting, or produced no `.tsv.gz` output. |
| `MISSING` | A file present in a prior `physioconvert_map.tsv` is no longer found under `--input`; its row (and any edits) is preserved. |

Exit code is `1` if any file is `CONVERT_ERROR` or `READER_MISSING`, and `0` otherwise. (`UNKNOWN_NAME` and `DATES_DISAGREE` are not errors — those files are converted, just awaiting proper naming or a date review.)

| Argument | Description |
| --- | --- |
| `-i`, `--input` | **Required.** Root directory walked recursively for `phys2bids`-supported physio files (`.acq`/`.txt`/`.mat`/`.gep`/`.smr`). |
| `-o`, `--output` | **Required.** BIDS project directory to write physio files into; `physioconvert_map.tsv`, `physioconvert_qc.tsv`, and their `.json` data dictionaries are written/updated at its root. |
| `-n`, `--nphysio` | *Optional.* Number of physio files to convert in parallel, one `phys2bids` conversion per process (default `1`). |
| `-l`, `--log` | *Optional.* Write a per-file log CSV to `OUTPUT_DIR/log/physioconvert_<YYYYMMDD_HHMMSS>_log.csv` (header `DATESTAMP,SOURCE_PATH,STATUS,DESTINATION_PATH`). One row per processed file, except a converted file that produced several outputs emits one row per output (each with its own `DESTINATION_PATH`); files with no output get a single blank-`DESTINATION_PATH` row. Off by default. |
| `-m`, `--maps` | *Optional.* Skip the `phys2bids` conversion and instead **apply your `physioconvert_map.tsv` edits** to the already-converted files: each file's existing `_physio.tsv.gz`/`.json` output(s) (the paths recorded in `physioconvert_qc.tsv`'s `output_files`) are **moved/renamed** to the BIDS path implied by its (possibly edited) entities, preserving each `recording-<freq>Hz` label and handling `tmp_phys2bids/` ↔ proper-path transitions — but without re-running `phys2bids`. **Nothing is overwritten:** a file already correctly placed is left as-is, and if the target path is occupied by a *different* file the move is **skipped with a `WARNING`** (resolve the conflict and re-run). `physioconvert_map.tsv`/`physioconvert_qc.tsv` and their `.json` data dictionaries are regenerated; emptied source directories (including `tmp_phys2bids/`) are pruned. Each file is still re-read to recompute its metrics, so `phys2bids` is needed to read the files. Files with no prior output (e.g. an earlier `CONVERT_ERROR`) cannot be placed in this mode — re-run without `-m/--maps` to convert them. |

> **Note:** `phys2bids` 2.10.0 caps `numpy` at `<1.24`, which conflicts with `nibabel`'s `numpy>=1.25` requirement. Because `phys2bids` does not actually use any `numpy` APIs removed in 1.24+, `pyproject.toml` carries a `[tool.uv] override-dependencies = ["numpy>=1.25,<2"]` so the whole stack shares one `numpy` (held on the 1.x series, which predates `numpy` 2.0).
