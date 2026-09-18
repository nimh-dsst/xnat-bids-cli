# Manual Interventions

Some steps in the `xnatcli` workflow require a human to fill in values by hand between two commands. This page covers those steps.

## Renaming SUBJECT/EXPERIMENT during download

`xnatcli download` (see [`xnatcli download`](cli/download.md)) can write a subject's or experiment's files under a different on-disk directory name than its XNAT label. How you supply the rename depends on which download mode you're using:

- **`--csv` (batch) mode:** After `xnatcli query` writes its CSV (see [`xnatcli query`](cli/query.md)), the `SUBJECT_BIDS_RENAME` and `EXPERIMENT_BIDS_RENAME` columns are empty. Fill in the corresponding cell(s) for a row before running `download`.
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
