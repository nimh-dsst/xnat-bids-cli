# XNAT CLI for BIDS

A command-line interface for logging into an Extensible Neuroimaging Archive Toolkit (XNAT) server, querying experiments and downloading files, then converting to the Brain Imaging Data Structure (BIDS) standard format.

## Contents

- [`src/xnatcli/`](src/xnatcli/) — installable package that provides the `xnatcli` CLI (`xnatcli login`, `xnatcli download`, `xnatcli query`, `xnatcli mriconfig`, `xnatcli mriconvert`, `xnatcli cubids`, `xnatcli bidsmap`, `xnatcli physioconvert`), built on [PyXNAT](https://pyxnat.github.io/pyxnat/index.html).

## Setup

The project uses [uv](https://docs.astral.sh/uv/) and Python ≥ 3.11. Runtime dependencies: `pyxnat`, `dcm2bids`, `dcm2niix` (the [`dcm2niix`](https://pypi.org/project/dcm2niix/) PyPI package vendors the binary onto your `PATH`), `pydicom`, `cubids`, `nibabel` (used to read NIfTI shapes for the `mriconvert` `scans.tsv`), `pandas` (used by `bidsmap`), and `phys2bids` (used by `physioconvert` to read physiological recordings and write BIDS physio files; see the note under [`xnatcli physioconvert`](#xnatcli-physioconvert) about its `numpy` pin). `bioread` is also pulled in for `phys2bids` to read BIOPAC `.acq` files (phys2bids imports it lazily but does not depend on it directly).

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
- [`src/xnatcli/mriconfig.py`](src/xnatcli/mriconfig.py) — runs `dcm2bids_helper` against a downloaded experiment directory.
- [`src/xnatcli/mriconvert.py`](src/xnatcli/mriconvert.py) — converts downloaded XNAT sessions to BIDS via `dcm2bids`.
- [`src/xnatcli/cubids.py`](src/xnatcli/cubids.py) — runs [`CuBIDS`](https://cubids.readthedocs.io/) `add-nifti-info` and `group` on a BIDS dataset.
- [`src/xnatcli/bidsmap.py`](src/xnatcli/bidsmap.py) — generates/updates a participant/session mapping TSV for a `mriconvert`-produced BIDS dataset and, with `-o`, applies renames by copying the dataset to a new tree (uses [`pandas`](https://pandas.pydata.org/)).
- [`src/xnatcli/physioconvert.py`](src/xnatcli/physioconvert.py) — converts physio recordings associated (via `mriconvert`'s `mriconvert_qc.tsv` `physio` column) with a BIDS dataset, placing them directly alongside their paired scan (uses [`phys2bids`](https://phys2bids.readthedocs.io/)).

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
4. Iterates subjects and experiments and writes one row per experiment with header `PROJECT,SUBJECT_LABEL,SUBJECT_ID,EXPERIMENT_LABEL,EXPERIMENT_ID,EXPERIMENT_DATE`, sorted by `SUBJECT_LABEL` then `EXPERIMENT_LABEL`. If the project (or subject) exists but has no experiments, a header-only CSV is written. An existing output file is overwritten silently.

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

## `xnatcli mriconfig`

Runs `dcm2bids_helper` (from [`Dcm2Bids`](https://unfmontreal.github.io/Dcm2Bids/)) on one or many downloaded XNAT experiment directories and drafts a project-level dcm2bids config. The input directory follows the layout produced by `xnatcli download` (`<input>/PROJECT/SUBJECT/EXPERIMENT/scans/...`).

1. Validates `--input` and verifies that both `dcm2bids_helper` and `dcm2niix` are on `PATH`.
2. Resolves the set of experiments to process from one of the mutually exclusive selectors:
   - `-1 PROJECT SUBJECT EXPERIMENT` — exactly one experiment.
   - `-s/--subject PROJECT SUBJECT` — every `EXPERIMENT` directory under that subject.
   - `-p/--project PROJECT` — every `EXPERIMENT` directory under every subject in the project.
3. Computes the target directory `OUTPUT_DIR/PROJECT-<PROJECT>_mriconfig/` and creates it if missing. Existing contents are kept; per-experiment helper runs overwrite their own nested subdirectory.
4. For each experiment, invokes `dcm2bids_helper -d <input>/PROJECT/SUBJECT/EXPERIMENT/scans -o <target> -n EXPERIMENT --force`. The `-n` flag nests each experiment's NIfTI/JSON outputs under `<target>/tmp_dcm2bids/helper/<EXPERIMENT>/` so runs do not collide; `--force` lets re-runs overwrite stale files within that subdirectory. Experiments are processed serially or in parallel (`-n/--nprep`, one helper invocation per experiment per worker).
5. After every experiment has been processed (regardless of individual success), drafts a project-level dcm2bids config at `<target>/dcm2bids_config_<YYYYMMDD_HHMMSS>.json` by aggregating sidecars from **every** `<target>/tmp_dcm2bids/helper/<EXPERIMENT>/*.json` present on disk (including prior runs). Each run gets its own timestamped file so older drafts are preserved:
   - For each sidecar with a `BidsGuess` field (set by recent `dcm2niix` releases), parses it into `datatype`, `custom_entities`, and `suffix` (e.g., `["func", "_task-rest_bold"]` → `datatype: func`, `custom_entities: ["task-rest"]`, `suffix: bold`). `run-*`, `echo-*`, and `acq-*` entities are stripped — dcm2bids assigns run/echo numbering automatically, and `acq-*` from BidsGuess (typically a protocol-name shorthand like `acq-epfid2p3`) is replaced by a SeriesDescription-derived `acq-<label>` only when needed for disambiguation.
   - One description is emitted per unique identity, where identity is the first non-empty of `SeriesDescription`, `ProtocolName`, or `SidecarFilename` (basename). The chosen field becomes the description's `criteria`. Multiple sidecars sharing one identity (e.g., multi-run / multi-echo series with the same `SeriesDescription`, including across experiments) collapse into a single description.
   - When two or more identities map to the same `(datatype, custom_entities, suffix)` slot, the disambiguator first looks for phase-encoding direction codes (`AP`, `PA`, `RL`, `LR`, `SI`, `IS`) inside each identity. A code only matches when bordered by non-letters (so `MAPS`, `ISIS`, `RAPID` are not false positives); the *last* such code in the identity wins.
     - Identities sharing a detected code (or none) get bucketed; each bucket gets a `dir-<code>` entity and emits its own description, with the matched code masked out before computing any further `acq-` label. If a bucket still has multiple identities after this, an `acq-<label>` is added — the minimal substring that distinguishes them (longest common prefix and suffix removed, sanitized to `[A-Za-z0-9]`); when minimal-diff fails to yield unique non-empty labels, the full sanitized identity is used instead.
     - If `BidsGuess` already contained a `dir-XX` for the slot, no promotion happens (the entity is already there); but if any identity contains a *different* direction code, a loud warning is printed flagging the inconsistency.
     - When a slot has only one identity, no `dir-` promotion happens — the rule is conflict-only.
   - Sidecars with missing or empty `BidsGuess` are skipped with a warning.
6. Alongside it, a second, separate config is always drafted at `<target>/dcm2bids_config_blank_<YYYYMMDD_HHMMSS>.json`: one description per unique identity (`SeriesDescription`, falling back to `ProtocolName` or `SidecarFilename`, same priority as above) with blank `datatype`, `suffix`, and `custom_entities`, and `criteria` set to the matched field. This is a minimal starting point meant for manual editing rather than the `BidsGuess`-derived draft above.

```bash
# One experiment
xnatcli mriconfig -i DOWNLOAD_DIR -1 PROJECT SUBJECT EXPERIMENT -o OUTPUT_DIR

# All experiments of one subject, 4 in parallel
xnatcli mriconfig -i DOWNLOAD_DIR -s PROJECT SUBJECT -o OUTPUT_DIR -n 4

# All experiments of all subjects in a project
xnatcli mriconfig -i DOWNLOAD_DIR -p PROJECT -o OUTPUT_DIR
```

Per-experiment helper output is uniformly nested under `<target>/tmp_dcm2bids/helper/<EXPERIMENT>/` regardless of which selector was used. Multiple `mriconfig` invocations against the same project accumulate: each run refreshes the helper subdir(s) it touches (via `--force`) and re-drafts the project-level config by aggregating across **all** nested helper subdirectories present on disk.

### Per-experiment helper STATUS (and exit code)

| STATUS | Meaning |
| --- | --- |
| `COMPLETE` | `dcm2bids_helper` exited 0. |
| `FAILURE` | `dcm2bids_helper` exited non-zero, or `<input>/PROJECT/SUBJECT/EXPERIMENT/scans/` does not exist. |

Exit code is `0` if every processed experiment is `COMPLETE`, and `1` otherwise. Both config drafts are attempted regardless.

With `-l/--log`, a CSV identical in shape to `download`'s and `mriconvert`'s logs (`DATESTAMP,PROJECT,SUBJECT,EXPERIMENT,STATUS`) is written to `OUTPUT_DIR/PROJECT/log/mriconfig_<YYYYMMDD_HHMMSS>_log.csv` (local time, captured at run start) — the same `PROJECT/log/` directory used by `mriconvert`. One row is appended per processed experiment; rows are written under a lock so concurrent workers do not interleave.

With `-d/--delete`, every `*.nii.gz` file in each experiment's helper subdir (`OUTPUT_DIR/PROJECT-<PROJECT>_mriconfig/tmp_dcm2bids/helper/<EXPERIMENT>/`) is removed right after `dcm2bids_helper` returns for that experiment, regardless of STATUS. JSON sidecars are kept — the project-level config draft only needs the JSONs, and the NIfTI images are typically far larger. The per-experiment status line gets a trailing `(removed N .nii.gz)` so the deletion is visible. Use this when you only need the drafted config and not the helper-stage NIfTIs.

| Argument | Description |
| --- | --- |
| `-i`, `--input` | **Required.** Root directory holding `PROJECT/SUBJECT/EXPERIMENT` subdirectories. |
| `-1 PROJECT SUBJECT EXPERIMENT` | Run helper on a single experiment. Mutually exclusive with `-s` and `-p`. Values must match the directory names under `INPUT_DIR`. |
| `-s`, `--subject PROJECT SUBJECT` | Run helper on every experiment of one subject. |
| `-p`, `--project PROJECT` | Run helper on every experiment of every subject in a project. |
| `-o`, `--output` | **Required.** Directory under which `PROJECT-<PROJECT>_mriconfig/` is created (the parent directory is created if missing). |
| `-n`, `--nprep` | *Optional.* Number of parallel dcm2bids_helper invocations, one per experiment per worker (default `1`). |
| `-l`, `--log` | *Optional.* Write a per-experiment log CSV to `OUTPUT_DIR/PROJECT/log/mriconfig_<YYYYMMDD_HHMMSS>_log.csv`. |
| `-d`, `--delete` | *Optional.* After each experiment's helper run, delete `*.nii.gz` from its `tmp_dcm2bids/helper/<EXPERIMENT>/` subdir. JSON sidecars are kept. |
| `-m`, `--maps` | *Optional.* Skip running `dcm2bids_helper` and only (re)draft the config from the helper JSON sidecars already under `OUTPUT_DIR/PROJECT-<PROJECT>_mriconfig/`. `dcm2bids_helper`/`dcm2niix` are not required. |

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
4. Sessions are processed serially or in parallel (`-n/--nconvert`); a one-line per-session status is printed, and a summary is printed at the end. With `-l/--log`, a CSV identical in shape to `download`'s log (`DATESTAMP,PROJECT,SUBJECT,EXPERIMENT,STATUS`) is written to `<output>/PROJECT/log/mriconvert_<YYYYMMDD_HHMMSS>_log.csv` — the same `PROJECT/log/` directory used by `mriconfig`.
5. With `-d/--delete`, after a session finishes with `STATUS=COMPLETE` or `STATUS=EMPTY`, its input directory `<input>/PROJECT/SUBJECT/EXPERIMENT` is removed (via `shutil.rmtree`). The `SUBJECT` and then `PROJECT` parent directories are also removed if they become empty as a result. `FAILURE` and `NONEXISTENT` sessions are left untouched. Deletion happens after the per-session log row is written, so the log still records what was converted before removal.
6. After all sessions are processed, a dataset-wide `mriconvert_qc.tsv` is (re)written at `<output>/PROJECT-<PROJECT>_mriconvert_qc.tsv`, and the static data dictionary [`src/assets/mriconvert_qc.json`](src/assets/mriconvert_qc.json) is copied alongside it as `<output>/PROJECT-<PROJECT>_mriconvert_qc.json`. See [`mriconvert_qc.tsv`](#mriconvert_qctsv) below.

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
| `-c`, `--config` | **Required** unless `-m/--maps` is given. Path to the `dcm2bids` config JSON (typically the one drafted by `xnatcli mriconfig`). Recorded as the top-level `Dcm2BidsConfigPath` key in `OUTPUT_DIR/PROJECT-<PROJECT>_mriconvert_qc.json`. If omitted, a `Dcm2BidsConfigPath` recorded on a prior run is preserved. |
| `-n`, `--nconvert` | *Optional.* Number of parallel session conversions (default `1`). |
| `-l`, `--log` | *Optional.* Write a per-session log CSV to `<output>/PROJECT/log/mriconvert_<YYYYMMDD_HHMMSS>_log.csv`. |
| `-d`, `--delete` | *Optional.* After a session finishes with `STATUS=COMPLETE` or `STATUS=EMPTY`, delete its input directory `<input>/PROJECT/SUBJECT/EXPERIMENT`. Empty `SUBJECT` and `PROJECT` parent directories are also pruned. |
| `-m`, `--maps` | *Optional.* Skip the `dcm2bids` conversion and only (re)generate `OUTPUT_DIR/PROJECT-<PROJECT>_mriconvert_qc.tsv` (and copy `OUTPUT_DIR/PROJECT-<PROJECT>_mriconvert_qc.json`) for every project in scope from the already-converted BIDS data under `OUTPUT_DIR`. `-c/--config`, `pydicom`, `dcm2bids`, and `dcm2niix` are not required. |
| `-y`, `--physio` | *Optional.* Absolute path to the flat directory holding all raw physio recordings for this project. Recorded as the top-level `PhysioParent` key in `OUTPUT_DIR/PROJECT-<PROJECT>_mriconvert_qc.json` for [`xnatcli physioconvert`](#xnatcli-physioconvert) to resolve `OUTPUT_DIR/PROJECT-<PROJECT>_mriconvert_qc.tsv`'s `physio` column against. If omitted, a `PhysioParent` recorded on a prior run is preserved. |

### `mriconvert_qc.tsv`

At the end of every `mriconvert` run, a single dataset-wide `mriconvert_qc.tsv` is (re)generated at `<output>/PROJECT-<PROJECT>_mriconvert_qc.tsv`, by walking the dataset with `os.walk` for every `.nii.gz` file (the dcm2bids `tmp_dcm2bids/` scratch directory is skipped). The data dictionary `<output>/PROJECT-<PROJECT>_mriconvert_qc.json` is (re)written next to it from the static [`src/assets/mriconvert_qc.json`](src/assets/mriconvert_qc.json) asset, with its `PhysioParent` value set from `-y/--physio` (see below). Reading NIfTI shapes uses `nibabel`; if it is not installed, the `dimensions` column is left empty and a warning is printed.

`mriconvert_qc.tsv` is named distinctly from BIDS's canonical `scans.tsv`; [`xnatcli bidsmap`](#xnatcli-bidsmap) promotes `mriconvert_qc.tsv`/`mriconvert_qc.json` to the canonical `scans.tsv`/`scans.json` in its mapped output.

When a `mriconvert_qc.tsv` already exists, rows are merged by `filename` rather than being rebuilt from scratch — this is what lets separate sessions be converted at different times (not all at once) without losing manual review work already recorded for earlier sessions:

- A row whose `filename` is already in `mriconvert_qc.tsv` is always **kept exactly as-is**, including any text in its reviewer columns (`rename`, `physio`, `recommend_for_use`, `complete`, `usable`, `qc_rating`, `rating_reason`, `qc_notes`). This applies even if that file's generator-owned fields have since drifted from what's on disk — such drift is only reported (see below), never applied.
- A row whose `filename` is **not** yet in `mriconvert_qc.tsv` — i.e. newly converted since the last time `mriconvert_qc.tsv` was written — is appended with its reviewer columns blank.
- A row already in `mriconvert_qc.tsv` whose file is no longer found on disk is preserved rather than dropped.

Whenever a `mriconvert_qc.tsv` already exists, the freshly generated rows are compared against it and every difference in a **non-user (generator-owned)** field — `acq_time`, `series_number`, `dimensions`, `size_bytes`, `participant_id`, `session_id`, `datatype`, `suffix`, `bids_name`, plus files added or removed on disk — is reported as one `WARNING` per deviation to stdout and to a log file at `<output>/PROJECT/log/scans_deviations_<YYYYMMDD_HHMMSS>.log` (the log itself stays nested under the `PROJECT` directory). The log is written only when there is at least one deviation. (When `nibabel` is unavailable, `dimensions` is excluded from the comparison so its empty values are not flagged.) Reviewer-column edits are never reported as deviations.

Rows preserved because their file is no longer found on disk are additionally tallied across every project in scope and, if the total is non-zero, reported once more at the very end of the run: `WARNING: N mriconvert_qc.tsv row(s) across all project(s) in scope reference file(s) no longer found on disk (rows preserved; see WARNING(s) above).` This is informational only — it does not affect `mriconvert`'s exit code.

The columns, in order:

| Column | Source |
| --- | --- |
| `filename` | Path relative to the `PROJECT` root, POSIX separators, no leading `./` or `/`. |
| `acq_time` | `AcquisitionTime` from the file's JSON sidecar (empty if absent). |
| `series_number` | `SeriesNumber` from the file's JSON sidecar (empty if absent). |
| `dimensions` | `nibabel` image shape, `x`-joined, always padded to include the 4th dimension even when it is `1` (e.g. `256x256x170x1`). |
| `size_bytes` | File size on disk, in bytes. |
| `participant_id` | `sub-<label>` parsed from the filename. |
| `session_id` | `ses-<label>` parsed from the filename. |
| `datatype` | Name of the file's parent directory (e.g. `anat`, `func`). |
| `suffix` | Basename portion after the last underscore (never empty). |
| `bids_name` | The basename between the `sub-<label>_ses-<label>_` prefix and the `.nii.gz` extension. |
| `rename` | Empty — for the end-user to record a corrected `bids_name`. |
| `physio` | Empty — for the end-user to record the basename (with extension) of a raw physio recording under `PhysioParent` acquired alongside this scan; consumed by [`xnatcli physioconvert`](#xnatcli-physioconvert). |
| `recommend_for_use` | Empty — end-user `TRUE`/`FALSE` review field. |
| `complete` | Empty — end-user `TRUE`/`FALSE` review field (acquired at full intended length). |
| `usable` | Empty — end-user `TRUE`/`FALSE` review field. |
| `qc_rating` | Empty — end-user `PASS`/`FAIL`/`UNCERTAIN` review field. |
| `rating_reason` | Empty — free-text reason for `qc_rating`. |
| `qc_notes` | Empty — free-text QC notes. |

`mriconvert_qc.json` additionally carries two top-level keys, each `{"Description": ..., "Value": "<path or empty>"}` and always the first two keys in the file, in this order: `PhysioParent` (the absolute path passed via `-y/--physio`, the flat directory holding every raw physio recording for the project) and `Dcm2BidsConfigPath` (the absolute path passed via `-c/--config`, the `dcm2bids` config JSON used for this conversion).

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

## `xnatcli bidsmap`

Generates a participant/session mapping TSV for a BIDS dataset — the output of `xnatcli mriconvert` (with any physio already placed by [`xnatcli physioconvert`](#xnatcli-physioconvert)) — at `INPUT_DIR/PROJECT/`. The map is later filled in by hand to relate XNAT IDs and real dates to anonymized BIDS IDs and session codenames. When `-o OUTPUT_DIR` is provided, it additionally applies all filled-in renames by copying the BIDS dataset to a new directory tree.

This is the "map" half of the xnatcli workflow: `mriconvert`/`physioconvert` first **convert** raw source data to BIDS, preserving the source data untouched; `bidsmap` then **maps** that raw/unmapped BIDS data to a separate, renamed BIDS output, so the intermediary unmapped BIDS data is preserved too. `bidsmap` operates on `.nii.gz` main files with `.json`/`.bval`/`.bvec` sidecars, reading its `rename` column and QC-exclusion columns (`recommend_for_use`, `complete`, `usable`, `qc_rating`) from `mriconvert_qc.tsv` (one row per `.nii.gz` file, so `rename` unambiguously targets one file). A physio `_physio.tsv.gz`/`.json` pair co-located under the same `sub-*/ses-*/<datatype>/` directory rides along the copy for free (participant/session label substitution only — [`physioconvert`](#xnatcli-physioconvert) already writes it under its final name, so no separate rename step is needed for it).

`mriconvert_qc.tsv` is named distinctly from BIDS's canonical `scans.tsv` (see [`xnatcli mriconvert`](#xnatcli-mriconvert)); `bidsmap -o` promotes it to the canonical `scans.tsv`/`scans.json` in the mapped output.

### Map TSV generation (always runs)

1. Validates that `--input` exists and that the BIDS dataset `INPUT_DIR/PROJECT/` exists.
2. Scans `INPUT_DIR/PROJECT/` for `sub-*` directories and, within each, `ses-*` subdirectories.
3. Writes `INPUT_DIR/PROJECT-<PROJECT>_bidsmap.tsv` with the columns `participant_id`, `participant_rename`, `session_id`, `session_rename`. One row is emitted per `(participant, session)` pair, with the two `*_rename` columns left blank for later editing. Rows are sorted alphanumerically by `participant_id` then `session_id`.
   - If the dataset has **no** sessions (no participant has any `ses-*` subdirectory), only the `participant_id` and `participant_rename` columns are written, one row per participant.
   - If the dataset uses sessions but a particular participant has no `ses-*` subdirectory, that participant is skipped with a warning.
4. If `PROJECT-<PROJECT>_bidsmap.tsv` already exists, a fresh blank map is generated and compared to it (with `pandas`): any `(participant_id, session_id)` pairs not already present are appended, and all existing rows — including any `*_rename` values already filled in — are preserved. The merged table is re-sorted and rewritten.

```bash
xnatcli bidsmap -i MRICONVERT_OUTPUT_DIR -p PROJECT
```

### Copy-with-rename (`-o OUTPUT_DIR`)

When `-o` is provided, after updating the map TSV the command reads back the renames and writes a fully renamed copy of the BIDS dataset to `OUTPUT_DIR/PROJECT/`:

- **`PROJECT-<PROJECT>_bidsmap.tsv`** — `participant_rename` and `session_rename` columns rename `sub-*` and `ses-*` directory names and the matching labels embedded in all filenames. Blank values mean "keep the original label."
- **`mriconvert_qc.tsv`** — its `rename` column supplies a corrected `bids_name` (the part after `sub-X[_ses-Y]_`) for the file(s) that row describes. Sidecar files (`.json`, `.bval`, `.bvec`) sharing the same stem are renamed to match. Blank values mean "keep the original bids_name."

The copy also:

- **QC filtering**: Rows whose `recommend_for_use`, `complete`, or `usable` is exactly `"FALSE"`, or whose `qc_rating` is exactly `"FAIL"` or `"UNCERTAIN"`, have their file(s) excluded from the copy (along with sidecars) and their row omitted from the output manifest. Values in any of these columns that are non-empty but do not match a valid Level from `mriconvert_qc.json` (e.g. `"false"` instead of `"FALSE"`) generate an additional warning, since they are silently ignored by the filter.
- Updates `participants.tsv` in the output with the renamed participant IDs, and writes `scans.tsv` (`filename`, `bids_name`, `participant_id`, `session_id` columns, among others) to reflect all renames, omits rows for QC-excluded files, and drops the columns `rename` and `physio` (both have already been applied by the time `bidsmap -o` runs — `rename` to `bids_name`, `physio` by `xnatcli physioconvert`, which must run before `bidsmap -o`). All other reviewer columns are preserved. `mriconvert_qc.tsv`/`mriconvert_qc.json` and `physioconvert_qc.tsv`/`.json` themselves are **not** copied — `mriconvert_qc.tsv`/`.json` are promoted to `scans.tsv`/`.json` instead, and `physioconvert_qc.tsv`/`.json` stay raw-tree-only bookkeeping with no promoted counterpart.
- Skips `tmp_dcm2bids` and `log` scratch directories.
- **Incremental by default**: if `OUTPUT_DIR/PROJECT/` already exists, files under `sub-*/` whose destination path already exists are treated as already mapped and left untouched — only files not yet present at the destination are copied. Root-level manifests (`scans.tsv`, `participants.tsv`, `dataset_description.json`, ...) are always re-copied/re-written and re-patched, since they reflect the fully merged source state. This lets `bidsmap -o` be re-run safely as new data lands in `INPUT_DIR/PROJECT/` (e.g. from further `mriconvert`/`physioconvert` runs).
- **Warns loudly** when new files are being mapped into a `sub-*/[ses-*/]` directory that already existed at the destination before this run, since that session was already mapped and is only gaining files.
- Warns loudly for any two source files that would map to the same destination path (neither is copied); all warnings are re-displayed together at the end.

```bash
xnatcli bidsmap -i MRICONVERT_OUTPUT_DIR -p PROJECT -o RENAMED_OUTPUT_DIR
```

For example, if the BIDS dataset lives at `/data/bids/MYPROJ/`, then:

- `xnatcli bidsmap -i /data/bids -p MYPROJ` writes `/data/bids/PROJECT-MYPROJ_bidsmap.tsv`.
- After filling in the rename columns, `xnatcli bidsmap -i /data/bids -p MYPROJ -o /data/renamed` copies the dataset to `/data/renamed/MYPROJ/` with all renames applied.

| Argument | Description |
| --- | --- |
| `-i`, `--input` | **Required.** Root directory holding the BIDS dataset at `INPUT_DIR/PROJECT/`. The map TSV is written here as `PROJECT-<PROJECT>_bidsmap.tsv`. |
| `-p`, `--project` | **Required.** Project directory name under `INPUT_DIR` identifying the BIDS dataset to scan. |
| `-o`, `--output` | *Optional.* When provided, copy the BIDS dataset to `OUTPUT_DIR/PROJECT/` with all renames from the map TSV and `mriconvert_qc.tsv`'s own `rename` column applied. If `OUTPUT_DIR/PROJECT/` already exists, only files not already mapped there are copied (see above). |

## `xnatcli physioconvert`

Converts physio recordings **associated with an `xnatcli mriconvert` BIDS dataset** to [BIDS physiological recordings](https://bids-specification.readthedocs.io/en/stable/modality-specific-files/physiological-recordings.html) (`_physio.tsv.gz` + `_physio.json`), using [`phys2bids`](https://phys2bids.readthedocs.io/) (imported as a Python library) to read the files and write the BIDS output. It must run **after** `xnatcli mriconvert` and **before** `xnatcli bidsmap -o` (the association it consumes lives in `mriconvert`'s raw `mriconvert_qc.tsv`, and the `physio` column is dropped once `bidsmap` promotes it to `scans.tsv`).

Each physio recording is tied to one MRI scan by hand: fill in `mriconvert_qc.tsv`'s `physio` column with the raw recording's basename (with extension), found by browsing the flat directory recorded as `PhysioParent` in `mriconvert_qc.json` (set via `xnatcli mriconvert -y/--physio`). `physioconvert` then converts and places that recording **directly alongside its paired scan** — no filename parsing or guessed entities.

1. Validates that `OUTPUT_DIR/PROJECT-<PROJECT>_mriconvert_qc.tsv` exists (i.e. `xnatcli mriconvert` has already run); exits with a message if `phys2bids` is unavailable.
2. Reads `mriconvert_qc.tsv` (read-only — `physioconvert` never writes it back) and scopes to every row with a non-blank `physio` column.
3. **Collision check**: if the same `physio` basename is referenced by more than one row, **none** of those rows are converted — each is marked `COLLISION` and a `WARNING` lists every row referencing it. Clear all but one row's `physio` column and re-run to resolve.
4. Reads `PhysioParent` from `OUTPUT_DIR/PROJECT-<PROJECT>_mriconvert_qc.json`. For each remaining row, resolves the raw file as `PhysioParent/<physio>`; a missing `PhysioParent` or basename not found under it is reported as `SOURCE_MISSING` for that row (and does not block other rows).
5. **Validates that each match is really physiological data** by loading it with the matching `phys2bids` loader — not just trusting the extension. A file that fails to load (e.g. a stray `.txt` that is not a recording) is reported as `NOT_PHYSIO`.
6. Runs the `phys2bids` workflow into a temporary directory for **every** remaining row — `physioconvert` always reconverts from the raw source, every run, regardless of whether that association was converted before — then places its `.tsv.gz`/`.json` output(s) directly at `OUTPUT_DIR/PROJECT/<participant_id>/[<session_id>/]<datatype>/`, next to the associated `.nii.gz`, named from the row's `rename` (if set) or `bids_name` with its trailing suffix token replaced by `physio` — e.g. `bids_name` `task-rest_bold` → `task-rest_physio`, so the pair becomes `..._task-rest_physio.tsv.gz`/`.json`. Any `echo-<N>` entity is dropped from that name first, since a physio recording captures the whole run and applies to every echo of a multi-echo scan, not just the one row it was associated with — e.g. `bids_name` `task-rest_echo-1_bold` → `task-rest_physio`. A leftover output from an earlier run of the *same* association at that same computed path is replaced; a destination already written by a *different* association **this run** is never overwritten — that row is instead marked `CONVERT_ERROR` with a detail message. Editing a row's `physio` value to a *different* raw file converts the new one fresh; if the row's naming changed since a prior run (e.g. `rename`, `participant_id`, `session_id`, `datatype`), the old, differently-named output is left in place as an orphan (not auto-relocated or deleted — review and remove it by hand).
7. `phys2bids` automatically splits a recording whose channels have different sampling frequencies into one output file per frequency; each such file is given a `recording-<label>` entity (the `<freq>Hz` suffix `phys2bids` assigns) inserted just before `physio`.
8. Regenerates the per-row metrics every run: `n_channels` (channel count, including the time channel), `sampling_frequencies` (unique channel frequencies in Hz, ascending), `sample_count` (samples per frequency), and `duration_seconds` (acquisition length in seconds at 0.001 s precision, `sample_count / sampling_frequency`). These are comma-separated and aligned position-by-position, so a recording split across frequencies reports one entry per frequency in each. A blocked/failed row keeps blank metrics.
9. Writes `OUTPUT_DIR/PROJECT-<PROJECT>_physioconvert_qc.tsv`, fully regenerated from scratch every run — it is a visual reference for an expert reviewer, not a store consulted by a later run. One row per in-scope association, columns `physio`, `status`, `n_channels`, `sampling_frequencies`, `sample_count`, `duration_seconds`, sorted by `physio` (the sole reference/key column — no `filename`, `output_files`, or `bids_name`). A `COLLISION` group (same `physio` referenced by multiple `mriconvert_qc.tsv` rows) contributes a single row for that `physio` value.
10. Writes the static data dictionary [`src/assets/physioconvert_qc.json`](src/assets/physioconvert_qc.json) as `OUTPUT_DIR/PROJECT-<PROJECT>_physioconvert_qc.json`, describing every column, injecting a top-level `PhysioParent` key (`{"Description": ..., "Value": "<path or empty>"}`) — the same `PhysioParent` value read from `mriconvert_qc.json` for this run — always the first key in the file.

```bash
# Serial
xnatcli physioconvert -o BIDS_DIR -p MYPROJ

# 4 conversions in parallel
xnatcli physioconvert -o BIDS_DIR -p MYPROJ -n 4
```

With `-n/--nphysio` > 1, the `phys2bids` conversions run in parallel across separate **processes** (real parallelism, since `phys2bids` is an in-process Python library rather than an external command). The conversions run in workers, but all placement, `physioconvert_qc.tsv`, and the log are written **serially in the main process**, drained in **sorted-filename order** (out-of-order completions are buffered until their turn) — so results are fully deterministic regardless of `-n`.

### Per-association STATUS (and exit code)

| STATUS | Meaning |
| --- | --- |
| `CONVERTED` | `phys2bids` read the raw file and its `_physio.tsv.gz`/`.json` were written next to the paired `.nii.gz`. |
| `NOT_PHYSIO` | The referenced file matched a supported extension but could not be loaded as physiological data. |
| `READER_MISSING` | The optional reader package `phys2bids` needs for this format is not installed (e.g. `bioread` for `.acq`, `scipy` for `.mat`, `sonpy` for `.smr`). This is an environment problem, not a data problem — install the package and re-run. |
| `CONVERT_ERROR` | `phys2bids` raised while converting, produced no `.tsv.gz` output, or its destination was already occupied by a different association. |
| `SOURCE_MISSING` | `PhysioParent` was unset/not a directory, the named basename was not found under it, or the `mriconvert_qc.tsv` row is missing `participant_id`/`datatype`. |
| `COLLISION` | This `physio` basename is referenced by more than one `mriconvert_qc.tsv` row; none were converted until resolved. |

Exit code is `1` if any association is `CONVERT_ERROR` or `READER_MISSING`, and `0` otherwise.

| Argument | Description |
| --- | --- |
| `-o`, `--output` | **Required.** Same BIDS root `xnatcli mriconvert` wrote to (`OUTPUT_DIR` must hold `PROJECT-<PROJECT>_mriconvert_qc.tsv`/`PROJECT-<PROJECT>_mriconvert_qc.json`). Physio outputs are written directly into `OUTPUT_DIR/PROJECT/<participant_id>/[<session_id>/]<datatype>/`, alongside the associated `.nii.gz`. |
| `-p`, `--project` | **Required.** Project directory name under `OUTPUT_DIR` identifying the BIDS dataset produced by `xnatcli mriconvert`. |
| `-n`, `--nphysio` | *Optional.* Number of physio conversions to run in parallel, one `phys2bids` conversion per process (default `1`). |
| `-l`, `--log` | *Optional.* Write a per-association log CSV to `OUTPUT_DIR/log/physioconvert_<YYYYMMDD_HHMMSS>_log.csv` (header `DATESTAMP,STATUS,MRI_FILENAME,PHYSIO_SOURCE,DESTINATION_PATH`). One row per processed association, except a conversion that produced several outputs emits one row per output; associations with no output get a single blank-`DESTINATION_PATH` row. Also mirrors everything printed to stdout/stderr into a companion text log at `OUTPUT_DIR/log/physioconvert_<YYYYMMDD_HHMMSS>_log.txt`, the Python equivalent of piping through `tee`. Off by default. |

> **Note:** `phys2bids` 2.10.0 caps `numpy` at `<1.24`, which conflicts with `nibabel`'s `numpy>=1.25` requirement. Because `phys2bids` does not actually use any `numpy` APIs removed in 1.24+, `pyproject.toml` carries a `[tool.uv] override-dependencies = ["numpy>=1.25,<2"]` so the whole stack shares one `numpy` (held on the 1.x series, which predates `numpy` 2.0).
