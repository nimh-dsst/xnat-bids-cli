# `xnatcli bidsmap`

Generates a participant/session mapping TSV for a BIDS dataset — the output of `xnatcli mriconvert` (with any physio already placed by [`xnatcli physioconvert`](physioconvert.md)) — at `INPUT_DIR/PROJECT/`. The map is later filled in by hand to relate XNAT IDs and real dates to anonymized BIDS IDs and session codenames. When `-o OUTPUT_DIR` is provided, it additionally applies all filled-in renames by copying the BIDS dataset to a new directory tree.

This is the "map" half of the xnatcli workflow: `mriconvert`/`physioconvert` first **convert** raw source data to BIDS, preserving the source data untouched; `bidsmap` then **maps** that raw/unmapped BIDS data to a separate, renamed BIDS output, so the intermediary unmapped BIDS data is preserved too. `bidsmap` operates on `.nii.gz` main files with `.json`/`.bval`/`.bvec` sidecars, reading its `rename` column and QC-exclusion columns (`recommend_for_use`, `complete`, `usable`, `qc_rating`) from `mriconvert_qc.tsv` (one row per `.nii.gz` file, so `rename` unambiguously targets one file). A physio `_physio.tsv.gz`/`.json` pair co-located under the same `sub-*/ses-*/<datatype>/` directory rides along the copy for free (participant/session label substitution only — [`physioconvert`](physioconvert.md) already writes it under its final name, so no separate rename step is needed for it).

`mriconvert_qc.tsv` is named distinctly from BIDS's canonical `scans.tsv` (see [`xnatcli mriconvert`](mriconvert.md)); `bidsmap -o` promotes it to the canonical `scans.tsv`/`scans.json` in the mapped output.

## Map TSV generation (always runs)

1. Validates that `--input` exists and that the BIDS dataset `INPUT_DIR/PROJECT/` exists.
2. Scans `INPUT_DIR/PROJECT/` for `sub-*` directories and, within each, `ses-*` subdirectories.
3. Writes `INPUT_DIR/PROJECT-<PROJECT>_bidsmap.tsv` with the columns `participant_id`, `participant_rename`, `session_id`, `session_rename`. One row is emitted per `(participant, session)` pair, with the two `*_rename` columns left blank for later editing. Rows are sorted alphanumerically by `participant_id` then `session_id`.
   - If the dataset has **no** sessions (no participant has any `ses-*` subdirectory), only the `participant_id` and `participant_rename` columns are written, one row per participant.
   - If the dataset uses sessions but a particular participant has no `ses-*` subdirectory, that participant is skipped with a warning.
4. If `PROJECT-<PROJECT>_bidsmap.tsv` already exists, a fresh blank map is generated and compared to it (with `pandas`): any `(participant_id, session_id)` pairs not already present are appended, and all existing rows — including any `*_rename` values already filled in — are preserved. The merged table is re-sorted and rewritten.

```bash
xnatcli bidsmap -i MRICONVERT_OUTPUT_DIR -p PROJECT
```

## Copy-with-rename (`-o OUTPUT_DIR`)

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
