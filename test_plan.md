# Test Plan Checklist

This is a manual test plan for exercising every `xnatcli` subcommand and its flag combinations by hand against a real (or test) XNAT server. Work through the sections in order — `login` → `query` → `download` → `mriconfig` → `mriconvert` → `physioconvert` → `bidsmap` → `cubids` — since later subcommands consume the output of earlier ones; within a section, check off each item as you verify it. Use this before a release or after any change to a subcommand's behavior to catch regressions across the full workflow.

## xnatcli login

- [x] `xnatcli login` with valid server/username/password succeeds, prints the "Credentials verified and saved" message, and writes `~/.xnatcli/credentials.cfg`
- [x] `credentials.cfg` contains a `[xnatcli]` section with `server`, `username`, `password`
- [ ] `credentials.cfg` permissions are restricted (mode `0600` on macOS/Linux; read-only bit toggled on Windows)
- [x] Password input is not echoed to the terminal
- [ ] Leaving the server URL prompt blank exits with "server url is required" and nothing is written
- [ ] Leaving the username prompt blank exits with "username is required" and nothing is written
- [ ] Leaving the password prompt blank exits with "password is required" and nothing is written
- [ ] A trailing `/` on the server URL is stripped before verification/storage
- [ ] An incorrect password exits with an authentication-failure message and does **not** overwrite existing valid credentials
- [ ] An unreachable/incorrect server URL exits with a connection-failure message and does **not** overwrite existing valid credentials
- [ ] Re-running `xnatcli login` with new valid credentials overwrites the previously stored credentials
- [ ] After login, a downstream command (e.g. `xnatcli query`) succeeds using the stored credentials without re-prompting

## xnatcli query

- [ ] Deleting/renaming `credentials.cfg` and running `xnatcli query` exits with a message to run `xnatcli login`
- [ ] A `credentials.cfg` missing the `[xnatcli]` section (or a required key) exits with a message to re-run `xnatcli login`
- [ ] `xnatcli query PROJECT -o OUTPUT_DIR` writes `OUTPUT_DIR/PROJECT-<PROJECT>.csv` with header `PROJECT,SUBJECT_LABEL,SUBJECT_ID,EXPERIMENT_LABEL,EXPERIMENT_ID,EXPERIMENT_DATE` and one row per experiment in the project
- [ ] `xnatcli query PROJECT SUBJECT -o OUTPUT_DIR` writes `OUTPUT_DIR/PROJECT-<PROJECT>_SUBJECT-<SUBJECT>.csv` scoped to that subject's experiments only
- [ ] Supplying a project by its XNAT ID and, separately, by its label both resolve to the same project
- [ ] Supplying a subject by its XNAT ID and, separately, by its label both resolve to the same subject
- [ ] A nonexistent `PROJECT` exits with an error and no CSV is written
- [ ] A valid `PROJECT` with a nonexistent `SUBJECT` exits with an error and no CSV is written
- [ ] A project (or subject) that exists but has zero experiments writes a header-only CSV
- [ ] `EXPERIMENT_DATE` is populated as `YYYYMMDD` when set on the server, and blank when unset/unparseable
- [ ] Running the same query twice silently overwrites the existing output CSV
- [ ] Omitting `-o/--output` fails with an argparse "required" error
- [ ] `OUTPUT_DIR` that does not yet exist is created

## xnatcli download

- [ ] Deleting/renaming `credentials.cfg` and running `xnatcli download` exits with a message to run `xnatcli login`
- [ ] Omitting both `-1` and `-c/--csv` fails with a "one of the arguments is required" error
- [ ] Supplying both `-1` and `-c/--csv` fails with a mutually-exclusive-arguments error
- [ ] `-1 PROJECT SUBJECT EXPERIMENT -o OUTPUT_DIR` downloads every scan file to `OUTPUT_DIR/PROJECT/SUBJECT/EXPERIMENT/scans/<scan_id>/<resource_label>/<filename>`
- [ ] `-1` also downloads session-level resource files to `OUTPUT_DIR/PROJECT/SUBJECT/EXPERIMENT/resources/<resource_label>/<filename>`
- [ ] `-1` accepts XNAT IDs interchangeably with user-facing labels for `PROJECT`, `SUBJECT`, `EXPERIMENT`
- [ ] `-1` on a nonexistent triplet reports `STATUS=NONEXISTENT` and exits `1`
- [ ] `-1` on an experiment with zero files reports `STATUS=EMPTY` and exits `0`
- [ ] `--csv PATH/TO/QUERY.csv -o OUTPUT_DIR` (aliases `-c`, `-i`, `--input`) downloads every row from a `query`-produced CSV
- [ ] Batch mode continues processing remaining rows after one row fails (`STATUS=FAILURE`/`PARTIAL`) and exits `1` with a summary
- [ ] Batch mode exits `0` when every row is `COMPLETE` or `EMPTY`
- [ ] `-n/--ndownload 4` on `-1` mode parallelizes per-file downloads and produces the same file set as `-n 1`
- [ ] `-n/--ndownload 4` on `--csv` mode parallelizes per-experiment downloads and produces the same file set as `-n 1`
- [ ] `-l/--log` writes `OUTPUT_DIR/log/download_<YYYYMMDD_HHMMSS>_log.csv` with header `DATESTAMP,PROJECT,SUBJECT,EXPERIMENT,STATUS` and one row per processed experiment
- [ ] Without `-l/--log`, no `log/` directory is created
- [ ] `-a/--archive` produces `OUTPUT_DIR/archive/PROJECT-<P>_SUBJECT-<S>_EXPERIMENT-<E>.tar.gz` after each experiment downloads
- [ ] Re-running with `-a/--archive` against an experiment whose archive already exists skips archiving with a warning (does not overwrite)
- [ ] `-d/--delete` without `-a/--archive` fails immediately with "requires -a/--archive"
- [ ] `-a -d` together delete `OUTPUT_DIR/PROJECT/SUBJECT/EXPERIMENT` only after a successful archive
- [ ] `-a -d` also removes the `SUBJECT` and then `PROJECT` parent directories if they become empty
- [ ] Combine `-1`, `-n`, `-l`, `-a`, `-d` together in one invocation and verify all behaviors hold simultaneously
- [ ] Combine `--csv`, `-n`, `-l`, `-a`, `-d` together in one invocation and verify all behaviors hold simultaneously
- [ ] Omitting `-o/--output` fails with an argparse "required" error

## xnatcli mriconfig

- [ ] Running with `dcm2bids_helper` or `dcm2niix` missing from `PATH` exits with a clear error before touching `OUTPUT_DIR`
- [ ] Omitting all of `-1`, `-s/--subject`, `-p/--project` fails with a "one of the arguments is required" error
- [ ] Supplying two of `-1`, `-s/--subject`, `-p/--project` together fails with a mutually-exclusive-arguments error
- [ ] `-i INPUT_DIR -1 PROJECT SUBJECT EXPERIMENT -o OUTPUT_DIR` runs the helper on exactly one experiment, nesting output under `OUTPUT_DIR/PROJECT-<PROJECT>_mriconfig/tmp_dcm2bids/helper/<EXPERIMENT>/`
- [ ] `-s/--subject PROJECT SUBJECT` runs the helper on every experiment directory under that subject
- [ ] `-p/--project PROJECT` runs the helper on every experiment of every subject in the project
- [ ] An experiment directory missing `scans/` reports `STATUS=FAILURE` for that experiment without aborting the rest
- [ ] `-n/--nprep 4` parallelizes helper invocations and produces the same per-experiment outputs as `-n 1`
- [ ] After all experiments process, `OUTPUT_DIR/PROJECT-<PROJECT>_mriconfig/dcm2bids_config.json` is drafted from the aggregated helper sidecars
- [ ] Re-running `mriconfig` against the same project/output when `dcm2bids_config.json` already exists writes a timestamped `dcm2bids_config_<YYYYMMDD_HHMMSS>.json` instead of overwriting
- [ ] Running `mriconfig` a second time with a different `-1`/`-s`/`-p` scope against the same `OUTPUT_DIR` accumulates helper subdirectories rather than clobbering prior ones, and the new config draft aggregates across all of them
- [ ] Multi-run/multi-echo series sharing one `SeriesDescription`/`ProtocolName` collapse into a single description in the draft config
- [ ] Two identities that collide on `(datatype, custom_entities, suffix)` get disambiguated via `dir-<code>` when a phase-encoding code (AP/PA/RL/LR/SI/IS) is present and bordered by non-letters
- [ ] Two identities that collide with no detectable direction code get disambiguated via a minimal-diff `acq-<label>`
- [ ] A `BidsGuess` that already contains `dir-XX` is left alone, and a conflicting direction code elsewhere in the same slot prints a loud warning
- [ ] A sidecar with missing/empty `BidsGuess` is skipped with a warning, not fatal to the run
- [ ] `-l/--log` writes `OUTPUT_DIR/log/mriconfig_<YYYYMMDD_HHMMSS>_log.csv` with header `DATESTAMP,PROJECT,SUBJECT,EXPERIMENT,STATUS`
- [ ] `-d/--delete` removes `*.nii.gz` from each experiment's helper subdir right after that experiment's helper run, regardless of `STATUS`, while keeping the JSON sidecars
- [ ] `-d/--delete` status line shows the trailing `(removed N .nii.gz)` annotation
- [ ] `-m/--maps` alone (no `dcm2bids_helper`/`dcm2niix` on `PATH`) still succeeds by only re-drafting the config from existing sidecars under `OUTPUT_DIR/PROJECT-<PROJECT>_mriconfig/`
- [ ] `-m/--maps` against an `OUTPUT_DIR` with no prior helper runs produces an empty/near-empty config draft without crashing
- [ ] Combine `-p`, `-n`, `-l`, `-d` together in one invocation and verify all behaviors hold simultaneously
- [ ] Omitting `-i/--input` or `-o/--output` fails with an argparse "required" error

## xnatcli mriconvert

- [ ] Running with `dcm2bids`/`dcm2niix` missing from `PATH`, or `pydicom` not importable, exits with a clear error (unless `-m/--maps` is given)
- [ ] Omitting all of `-1`, `-s/--subject`, `-p/--project` fails with a "one of the arguments is required" error
- [ ] Supplying two of `-1`, `-s/--subject`, `-p/--project` together fails with a mutually-exclusive-arguments error
- [ ] Omitting `-c/--config` without `-m/--maps` fails with a clear error
- [ ] `-1 PROJECT SUBJECT EXPERIMENT -o OUTPUT_DIR -c CONFIG` converts one session to `OUTPUT_DIR/PROJECT/sub-<PARTICIPANT>/ses-<SESSION>/`
- [ ] `PARTICIPANT`/`SESSION` labels strip non-`[A-Za-z0-9]` characters from the `SUBJECT`/`EXPERIMENT` directory names while preserving case
- [ ] A session directory with no readable `.dcm`/`.IMA` under `scans/` reports `STATUS=EMPTY`
- [ ] A session directory that does not exist on disk reports `STATUS=NONEXISTENT`
- [ ] Re-running against an already-populated `sub-X/ses-Y/` prints a `WARNING:` and proceeds with `--clobber`
- [ ] `-s/--subject PROJECT SUBJECT` converts every experiment directory under that subject
- [ ] `-p/--project PROJECT` converts every experiment of every subject in the project
- [ ] `-n/--nconvert 4` parallelizes session conversions and produces the same per-session outputs as `-n 1`
- [ ] `-l/--log` writes `OUTPUT_DIR/log/mriconvert_<YYYYMMDD_HHMMSS>_log.csv` with header `DATESTAMP,PROJECT,SUBJECT,EXPERIMENT,STATUS`
- [ ] `-a/--archive` tars+gzips `INPUT_DIR/PROJECT/SUBJECT/EXPERIMENT` to `INPUT_DIR/archive/PROJECT-<P>_SUBJECT-<S>_EXPERIMENT-<E>.tar.gz` regardless of conversion outcome
- [ ] Re-running with `-a/--archive` against a session whose archive already exists skips archiving with a warning
- [ ] `-d/--delete` without `-a/--archive` deletes the session's input directory only when `STATUS=COMPLETE` or `STATUS=EMPTY`, leaving `FAILURE`/`NONEXISTENT` sessions untouched
- [ ] `-d/--delete` with `-a/--archive` deletes the session's input directory after a successful archive, regardless of conversion status
- [ ] `-d/--delete` also removes empty `SUBJECT` and `PROJECT` parent directories
- [ ] `-d/--delete` deletion happens after the log row for that session is written (verify the log still shows the pre-deletion result)
- [ ] After a run, `OUTPUT_DIR/PROJECT/mriconvert_qc.tsv` and `OUTPUT_DIR/PROJECT/mriconvert_qc.json` are (re)written
- [ ] `mriconvert_qc.tsv` columns match the documented order and content (`filename`, `acq_time`, `series_number`, `dimensions`, `size_bytes`, `participant_id`, `session_id`, `datatype`, `suffix`, `bids_name`, plus blank reviewer columns `rename`, `physio`, `recommend_for_use`, `complete`, `usable`, `qc_rating`, `rating_reason`, `qc_notes`)
- [ ] Re-running `mriconvert` (adding a new session) merges into the existing `mriconvert_qc.tsv` by `filename`: prior rows (including any hand-filled reviewer columns) are kept as-is, and the new session's rows are appended with blank reviewer columns
- [ ] Hand-editing a reviewer column (e.g. `rename`) on an existing row survives a subsequent `mriconvert` re-run
- [ ] Manually changing a generator-owned field on disk (e.g. renaming a `.nii.gz`) and re-running triggers a `WARNING` per deviation, printed to stdout and logged to `OUTPUT_DIR/log/scans_deviations_<PROJECT>_<YYYYMMDD_HHMMSS>.log`
- [ ] A `mriconvert_qc.tsv` row whose file no longer exists on disk is preserved (not dropped), and the end-of-run summary reports the total count of such preserved rows across all projects in scope
- [ ] `-m/--maps` (without `dcm2bids`/`dcm2niix`/`pydicom`/`-c` available) still succeeds by only regenerating `mriconvert_qc.tsv`/`mriconvert_qc.json` from already-converted BIDS data
- [ ] `-y/--physio PHYSIO_PARENT_DIR` records `PhysioParent` in `mriconvert_qc.json`
- [ ] Omitting `-y/--physio` on a subsequent run preserves a `PhysioParent` recorded by a prior run
- [ ] Combine `-p`, `-n`, `-l`, `-a`, `-d`, `-y` together in one invocation and verify all behaviors hold simultaneously
- [ ] Omitting `-i/--input` or `-o/--output` fails with an argparse "required" error

## xnatcli physioconvert

- [ ] Running before `xnatcli mriconvert` has produced `OUTPUT_DIR/PROJECT/mriconvert_qc.tsv` exits with a clear error
- [ ] Running with `phys2bids` unavailable exits with a clear error
- [ ] With every `mriconvert_qc.tsv` `physio` column blank, the run completes with zero associations processed
- [ ] Filling in one row's `physio` column with a valid recording basename under `PhysioParent` converts it, writing `_physio.tsv.gz`/`.json` next to the paired `.nii.gz`, and reports `STATUS=CONVERTED`
- [ ] The output physio basename is derived from `rename` (if set) else `bids_name`, with the trailing suffix token replaced by `physio`
- [ ] Two `mriconvert_qc.tsv` rows referencing the same `physio` basename both report `STATUS=COLLISION`, print one `WARNING` listing both rows, and neither converts; clearing all but one and re-running resolves it
- [ ] A `physio` value with no `PhysioParent` set in `mriconvert_qc.json` reports `STATUS=SOURCE_MISSING`
- [ ] A `physio` value not found under `PhysioParent` reports `STATUS=SOURCE_MISSING`
- [ ] A `physio` value pointing at a non-physiological file (e.g. a stray `.txt`) reports `STATUS=NOT_PHYSIO`
- [ ] A `physio` value pointing at a format whose optional reader package is not installed reports `STATUS=READER_MISSING`
- [ ] A recording whose channels have mixed sampling frequencies splits into multiple outputs, each with a `recording-<label>` entity inserted before `physio`
- [ ] After a successful conversion, `physioconvert_qc.tsv` (and `.json`) is written at `OUTPUT_DIR/PROJECT/` with per-row metrics (`n_channels`, `sampling_frequencies`, `output_files`, `sample_count`, `duration_seconds`, `bids_name`)
- [ ] Re-running unchanged converts nothing further; the previously converted row is left alone (not re-run) because `physioconvert_qc.tsv` shows `CONVERTED` and its `output_files` still exist
- [ ] Editing a converted row's `rename`/`participant_id`/`session_id`/`datatype` and re-running relocates/renames the existing output to match, without re-invoking `phys2bids`
- [ ] Editing a converted row's `physio` to a different raw file converts the new file fresh and leaves the old output in place untouched
- [ ] A computed destination already occupied by a file from a different association reports `STATUS=CONVERT_ERROR` and does not overwrite it
- [ ] Deleting/blanking a row's association in `mriconvert_qc.tsv` and re-running marks the corresponding `physioconvert_qc.tsv` row `STATUS=ROW_GONE` while preserving any prior QC review fields
- [ ] Hand-filled QC review columns (`recommend_for_use`, `complete`, `usable`, `qc_rating`, `rating_reason`, `qc_notes`) in `physioconvert_qc.tsv` survive subsequent runs
- [ ] `-n/--nphysio 4` converts multiple associations in parallel processes, and `physioconvert_qc.tsv`/log rows still land in deterministic sorted-`filename` order matching a `-n 1` run
- [ ] `-l/--log` writes `OUTPUT_DIR/PROJECT/log/physioconvert_<YYYYMMDD_HHMMSS>_log.csv` with header `DATESTAMP,STATUS,MRI_FILENAME,PHYSIO_SOURCE,DESTINATION_PATH`, one row per output (or one blank-`DESTINATION_PATH` row for an association with no output)
- [ ] `-m/--maps` relocates already-converted outputs to match edited columns and refreshes `physioconvert_qc.tsv` without invoking `phys2bids`
- [ ] `-m/--maps` on an association with no existing output to relocate leaves it untouched with a note (does not convert it)
- [ ] Exit code is `1` when any association is `CONVERT_ERROR` or `READER_MISSING`, and `0` otherwise
- [ ] Omitting `-o/--output` or `-p/--project` fails with an argparse "required" error

## xnatcli bidsmap

- [ ] `-i INPUT_DIR -p PROJECT` (no `-o`) writes `INPUT_DIR/PROJECT-<PROJECT>_bidsmap.tsv` with columns `participant_id`, `participant_rename`, `session_id`, `session_rename`, sorted by participant then session
- [ ] For a dataset with no `ses-*` directories at all, the map TSV only has `participant_id`/`participant_rename` columns
- [ ] For a dataset that uses sessions but one participant lacks any `ses-*` directory, that participant is skipped with a warning
- [ ] Re-running map generation after adding a new participant/session appends the new `(participant_id, session_id)` rows while preserving previously filled-in `*_rename` values
- [ ] Filling in `participant_rename`/`session_rename` values, then running with `-o OUTPUT_DIR`, renames the corresponding `sub-*`/`ses-*` directories and embedded filename labels in the copy; blank values keep the original label
- [ ] Filling in a `mriconvert_qc.tsv` row's `rename` column, then `-o`, renames that file's `bids_name` (and matching `.json`/`.bval`/`.bvec` sidecars) in the copy; blank `rename` keeps the original `bids_name`
- [ ] A row with `recommend_for_use`, `complete`, or `usable` exactly `"FALSE"` is excluded from the copy (file, sidecars, and manifest row all omitted)
- [ ] A row with `qc_rating` exactly `"FAIL"` or `"UNCERTAIN"` is excluded from the copy
- [ ] A QC column with a near-miss value (e.g. `"false"` instead of `"FALSE"`) is **not** filtered and prints a warning about the invalid Level
- [ ] `-o OUTPUT_DIR` on a first run requires `OUTPUT_DIR/PROJECT/` not to already exist, and produces `participants.tsv` and `scans.tsv` (promoted from `mriconvert_qc.tsv`) reflecting all renames
- [ ] `scans.tsv` in the output drops the `rename` and `physio` columns while preserving other reviewer columns
- [ ] `mriconvert_qc.tsv`/`mriconvert_qc.json` and `physioconvert_qc.tsv`/`.json` are **not** copied into `OUTPUT_DIR/PROJECT/`
- [ ] `tmp_dcm2bids` and `log` scratch directories are skipped in the copy
- [ ] Re-running `-o` against an existing `OUTPUT_DIR/PROJECT/` only copies files not already present at their destination (incremental); root-level manifests are always re-written
- [ ] Adding new files into an already-mapped `sub-*/[ses-*/]` directory and re-running `-o` prints a loud warning that the session was already mapped
- [ ] Two source files that would map to the same destination path both print a warning and neither is copied
- [ ] Run `physioconvert` before `bidsmap -o` and confirm the physio `_physio.tsv.gz`/`.json` pair rides along the copy with correct participant/session label substitution
- [ ] Omitting `-i/--input` or `-p/--project` fails with an argparse "required" error

## xnatcli cubids

- [ ] Running with `cubids` missing from `PATH` exits with a clear error
- [ ] `-i INPUT_DIR -p PROJECT` where `INPUT_DIR/PROJECT/` does not exist exits with a clear error
- [ ] A clean run creates `INPUT_DIR/PROJECT-<PROJECT>_cubids/`, runs `cubids add-nifti-info` then `cubids group ... v0`, and merges `INPUT_DIR/PROJECT/code/CuBIDS/` into `INPUT_DIR/PROJECT-<PROJECT>_cubids/CuBIDS/`
- [ ] Verify `v0_summary.tsv`, `v0_files.tsv`, `v0_AcqGrouping.tsv`, `v0_AcqGroupInfo.txt` all land in `INPUT_DIR/PROJECT-<PROJECT>_cubids/CuBIDS/`
- [ ] `INPUT_DIR/PROJECT/code/` is removed after the merge if it is left empty
- [ ] `add-nifti-info` mutates the BIDS dataset's JSON sidecars in place (spot-check a sidecar before/after)
- [ ] With a leftover `INPUT_DIR/PROJECT/tmp_dcm2bids/` present, it is stashed to `INPUT_DIR/.<PROJECT>_cubids_stash_tmp_dcm2bids/` during the run and restored afterward (success case)
- [ ] Re-running `cubids` against an `INPUT_DIR/PROJECT-<PROJECT>_cubids/` that already exists reuses it and overwrites same-named files without disturbing unrelated files there
- [ ] If `add-nifti-info` exits non-zero, `group` is skipped entirely and the command exits `1`
- [ ] If `group` exits non-zero after a successful `add-nifti-info`, the command exits `1`
- [ ] A fully successful run exits `0`
- [ ] `-l/--log` writes `INPUT_DIR/PROJECT-<PROJECT>_cubids/log/cubids_<YYYYMMDD_HHMMSS>_log.csv` with header `DATESTAMP,PROJECT,STEP,STATUS`, one row per step (`add-nifti-info`, `group`)
- [ ] Omitting `-i/--input` or `-p/--project` fails with an argparse "required" error
