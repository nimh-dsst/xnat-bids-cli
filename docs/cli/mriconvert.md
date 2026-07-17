# `xnatcli mriconvert`

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
4. Sessions are processed serially or in parallel (`-n/--nconvert`); a one-line per-session status is printed, and a summary is printed at the end. With `-l/--log`, a CSV identical in shape to `download`'s log (`DATESTAMP,PROJECT,SUBJECT,EXPERIMENT,STATUS`) is written to `<output>/log/mriconvert_<YYYYMMDD_HHMMSS>_log.csv` — the same `log/` directory used by `mriconfig`.
5. With `-d/--delete`, after a session finishes with `STATUS=COMPLETE` or `STATUS=EMPTY`, its input directory `<input>/PROJECT/SUBJECT/EXPERIMENT` is removed (via `shutil.rmtree`). The `SUBJECT` and then `PROJECT` parent directories are also removed if they become empty as a result. `FAILURE` and `NONEXISTENT` sessions are left untouched. Deletion happens after the per-session log row is written, so the log still records what was converted before removal.
6. After all sessions are processed, a dataset-wide `mriconvert_qc.tsv` is (re)written at `<output>/PROJECT-<PROJECT>_mriconvert_qc.tsv`, and the static data dictionary [`src/assets/mriconvert_qc.json`](https://github.com/nimh-dsst/xnat-bids-cli/blob/main/src/assets/mriconvert_qc.json) is copied alongside it as `<output>/PROJECT-<PROJECT>_mriconvert_qc.json`. See [`mriconvert_qc.tsv`](#mriconvert_qctsv) below.

```bash
# One session
xnatcli mriconvert -i DOWNLOAD_DIR -1 PROJECT SUBJECT EXPERIMENT -o OUTPUT_DIR -c PATH/TO/dcm2bids_config.json

# All sessions of one subject, 4 in parallel, with a log
xnatcli mriconvert -i DOWNLOAD_DIR -s PROJECT SUBJECT -o OUTPUT_DIR -c PATH/TO/dcm2bids_config.json -n 4 -l

# All sessions of all subjects in a project
xnatcli mriconvert -i DOWNLOAD_DIR -p PROJECT -o OUTPUT_DIR -c PATH/TO/dcm2bids_config.json
```

## Per-session STATUS (and exit code)

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
| `-l`, `--log` | *Optional.* Write a per-session log CSV to `<output>/log/mriconvert_<YYYYMMDD_HHMMSS>_log.csv`. |
| `-d`, `--delete` | *Optional.* After a session finishes with `STATUS=COMPLETE` or `STATUS=EMPTY`, delete its input directory `<input>/PROJECT/SUBJECT/EXPERIMENT`. Empty `SUBJECT` and `PROJECT` parent directories are also pruned. |
| `-m`, `--maps` | *Optional.* Skip the `dcm2bids` conversion and only (re)generate `OUTPUT_DIR/PROJECT-<PROJECT>_mriconvert_qc.tsv` (and copy `OUTPUT_DIR/PROJECT-<PROJECT>_mriconvert_qc.json`) for every project in scope from the already-converted BIDS data under `OUTPUT_DIR`. `-c/--config`, `pydicom`, `dcm2bids`, and `dcm2niix` are not required. |
| `-y`, `--physio` | *Optional.* Absolute path to the flat directory holding all raw physio recordings for this project. Recorded as the top-level `PhysioParent` key in `OUTPUT_DIR/PROJECT-<PROJECT>_mriconvert_qc.json` for [`xnatcli physioconvert`](physioconvert.md) to resolve `OUTPUT_DIR/PROJECT-<PROJECT>_mriconvert_qc.tsv`'s `physio` column against. If omitted, a `PhysioParent` recorded on a prior run is preserved. |

## `mriconvert_qc.tsv`

At the end of every `mriconvert` run, a single dataset-wide `mriconvert_qc.tsv` is (re)generated at `<output>/PROJECT-<PROJECT>_mriconvert_qc.tsv`, by walking the dataset with `os.walk` for every `.nii.gz` file (the dcm2bids `tmp_dcm2bids/` scratch directory is skipped). The data dictionary `<output>/PROJECT-<PROJECT>_mriconvert_qc.json` is (re)written next to it from the static [`src/assets/mriconvert_qc.json`](https://github.com/nimh-dsst/xnat-bids-cli/blob/main/src/assets/mriconvert_qc.json) asset, with its `PhysioParent` value set from `-y/--physio` (see below). Reading NIfTI shapes uses `nibabel`; if it is not installed, the `dimensions` column is left empty and a warning is printed.

`mriconvert_qc.tsv` is named distinctly from BIDS's canonical `scans.tsv`; [`xnatcli bidsmap`](bidsmap.md) promotes `mriconvert_qc.tsv`/`mriconvert_qc.json` to the canonical `scans.tsv`/`scans.json` in its mapped output.

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
| `physio` | Empty — for the end-user to record the basename (with extension) of a raw physio recording under `PhysioParent` acquired alongside this scan; consumed by [`xnatcli physioconvert`](physioconvert.md). |
| `recommend_for_use` | Empty — end-user `TRUE`/`FALSE` review field. |
| `complete` | Empty — end-user `TRUE`/`FALSE` review field (acquired at full intended length). |
| `usable` | Empty — end-user `TRUE`/`FALSE` review field. |
| `qc_rating` | Empty — end-user `PASS`/`FAIL`/`UNCERTAIN` review field. |
| `rating_reason` | Empty — free-text reason for `qc_rating`. |
| `qc_notes` | Empty — free-text QC notes. |

`mriconvert_qc.json` additionally carries two top-level keys, each with `{"Description": ..., "Value": "<path or empty>"}` and always the first two keys in the file, in this order: `Dcm2BidsConfigPath` (the absolute path passed via `-c/--config`, the `dcm2bids` config JSON used for this conversion) and `PhysioParent` (the absolute path passed via `-y/--physio`, the flat directory holding every raw physio recording for the project). `Dcm2BidsConfigPath` also carries a `LastModified` key, stamped (in Python logging's default `asctime` format, e.g. `2026-07-16 14:23:05,123`) each time `-c/--config` is passed and `Value` is updated; preserved from a prior run whenever `Value` is preserved.
