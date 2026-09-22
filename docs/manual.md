# Manual Steps

Some steps in the `xnatbidscli` workflow require a human to fill in values by hand between two commands. This page covers those steps.

1. [Between query and download](#1-between-query-and-download)
2. [Between download and mriconvert](#2-between-download-and-mriconvert)
3. [Between mriconvert and physioconvert](#3-between-mriconvert-and-physioconvert)
4. [Between physioconvert and bidsmap](#4-between-physioconvert-and-bidsmap)

## 1. Between query and download

Before handing the [`xnatbidscli query`](cli/query.md) CSV to [`xnatbidscli download`](cli/download.md), review it by hand: `download --csv` processes every row in the file, so any row that should not be pulled down (a subject withdrawn from the study, an experiment outside the date range you care about, a duplicate session, etc.) must be deleted from the CSV first — there is no in-tool filtering flag. This is also where the on-disk renaming below is filled in. Both edits are ordinary spreadsheet work on the CSV `query` wrote, and neither step is reversible by `download` itself, so keep the original CSV if you want an audit trail of what was excluded.

### Renaming SUBJECT/EXPERIMENT during download

`xnatbidscli download` (see [`xnatbidscli download`](cli/download.md)) can write a subject's or experiment's files under a different on-disk directory name than its XNAT label. How you supply the rename depends on which download mode you're using:

- **`--csv` (batch) mode:** After `xnatbidscli query` writes its CSV (see [`xnatbidscli query`](cli/query.md)), the `SUBJECT_BIDS_RENAME` and `EXPERIMENT_BIDS_RENAME` columns are empty. Fill in the corresponding cell(s) for a row before running `download`.
- **`-1` (single-experiment) or `--accession` mode:** Pass `--rename-subject` and/or `--rename-experiment` on the command line instead — there's no CSV to edit. With a subject `--accession` (which may download more than one experiment), only `--rename-subject` is allowed; `--rename-experiment` is rejected.

Either way, the same formatting rule applies:

- Subject rename — if the value does not already start with `sub-`, `sub-` is prepended.
- Experiment rename — if the value does not already start with `ses-`, `ses-` is prepended.

XNAT itself is always queried using the original `SUBJECT_LABEL`/`EXPERIMENT_LABEL` (or, in `-1`/`--accession` mode, the original/resolved `SUBJECT`/`EXPERIMENT` values); only the on-disk directory name (and, with `-a/--archive`, the archive filename and, with `-l/--log`, the log's `SUBJECT`/`EXPERIMENT` columns) reflects the rename.

### Blank vs. filled behavior

The subject and experiment rename are independent — each is evaluated on its own:

- Blank/omitted: that value passes through as-is (no renaming) — the original label is used.
- Filled: the rename is applied (with the `sub-`/`ses-` prefix prepended if missing).

A row (or `-1` invocation) may have both blank, both filled, or only one of the two filled — each one's outcome does not depend on the other's.

### Validation

After an optional `sub-`/`ses-` prefix is stripped, the remaining value must be alphanumeric only (letters and digits — no `-`, `_`, spaces, or other punctuation). This matches the BIDS label format.

- In `--csv` mode, `download` validates every row of the input CSV before downloading anything; if any `SUBJECT_BIDS_RENAME` or `EXPERIMENT_BIDS_RENAME` value fails this check, the entire run exits with an error and nothing is downloaded. Fix the offending cell(s) in the CSV and re-run `download`.
- In `-1`/`--accession` mode, an invalid `--rename-subject`/`--rename-experiment` value exits with an error before downloading.

`--rename-subject`/`--rename-experiment` only apply to `-1`/`--accession` mode; supplying either alongside `--csv` is an error. `--rename-experiment` is additionally rejected when `--accession` resolves to a subject rather than an experiment.

## 2. Between download and mriconvert

Before running [`xnatbidscli mriconvert`](cli/mriconvert.md) at scale, run [`xnatbidscli mriconfig`](cli/mriconfig.md) against a representative sample of the downloaded data and use its drafted `dcm2bids` config as a starting point. Expect several rounds of manual editing — adjusting `datatype`, `suffix`, `custom_entities`, and disambiguating `criteria` — interleaved with re-running `mriconfig -m` or a small `mriconvert` test batch, until the team settles on one config JSON that correctly identifies every scan type present in the project. Because `mriconfig` and `mriconvert` are both idempotent and safe to re-run against the same input, this back-and-forth can be repeated as many times as needed without corrupting prior output. Only once the config is agreed upon internally should it be pointed to via `mriconvert -c` for the full project.

## 3. Between mriconvert and physioconvert

Before running [`xnatbidscli physioconvert`](cli/physioconvert.md), confirm that `-y/--physio` was passed to `mriconvert` so the `PhysioParent` key in `mriconvert_qc.json` points at the flat directory holding every raw physio recording for the project — `physioconvert` resolves each recording relative to that path and cannot find files without it. That directory must stay flat (no subfolders), since recordings are matched by basename alone. Then, for each scan that has an associated recording, fill in the `physio` column of the corresponding row in `mriconvert_qc.tsv` with that recording's basename (including extension); for multi-echo fMRI data, the recording is one continuous acquisition covering every echo, so it belongs only in the `echo-1` row — `physioconvert` applies it to the whole run automatically and drops the `echo-*` entity from the output name.

## 4. Between physioconvert and bidsmap

Before running [`xnatbidscli bidsmap -o`](cli/bidsmap.md), review `mriconvert_qc.tsv` row by row and record QC judgments in the `recommend_for_use`, `complete`, `usable`, and `qc_rating` columns — any row marked `"FALSE"` in the first three or `"FAIL"`/`"UNCERTAIN"` in `qc_rating` is excluded from the mapped BIDS output, along with its sidecars. This is also the point to fill in the `rename` column with a corrected `bids_name` for any file whose auto-derived name needs fixing, since `bidsmap -o` applies both the QC exclusions and the renames in the same copy step. Do this review after `physioconvert` has run (its `physio` values are consumed at that point) and before `bidsmap -o`, since `bidsmap` reads `mriconvert_qc.tsv` as a snapshot at copy time.
