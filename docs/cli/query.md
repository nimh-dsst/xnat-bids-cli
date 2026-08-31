# `xnatcli query`

Writes a CSV of one row per experiment — every experiment in a project, or every experiment under a single subject in a project. The columns are:

| Column | Source |
| --- | --- |
| `PROJECT` | Canonical XNAT project ID. |
| `SUBJECT_LABEL` | User-facing subject label (e.g., `sub-001`). Used by downstream commands and on-disk paths. |
| `SUBJECT_ID` | XNAT accession ID for the subject (e.g., `XNAT_S00001`). |
| `EXPERIMENT_LABEL` | User-facing experiment label (e.g., `ses-baseline`). Used by downstream commands and on-disk paths. |
| `EXPERIMENT_ID` | XNAT accession ID for the experiment (e.g., `XNAT_E00001`). |
| `EXPERIMENT_DATE` | Experiment date in `YYYYMMDD` format. Empty if unset on the server or unparseable. |
| `ESTIMATED_SIZE_BYTES` | Sum of every file's size (bytes) under the experiment — scans and session-level resources combined — via XNAT's session-wide `/files` listing. `0` means the experiment genuinely has no files. Empty means the size lookup itself failed (a warning is printed to stderr in that case); this does not fail the query. `FILES_WITH_UNLABELED_SIZE` means the experiment has files but the server reported no `Size` for any of them. `UNPARSEABLE_SIZE_VALUES` means at least one file's `Size` was present but non-numeric. |

> **Note:** XNAT enforces label uniqueness within a project for both subjects and experiments. If a server somehow contains duplicate labels, the resulting CSV may contain rows that downstream commands cannot disambiguate.

1. Loads credentials from `~/.xnatcli/credentials.cfg`; if the file is missing or incomplete, exits with a message telling you to run `xnatcli login`.
2. Connects to the stored server via PyXNAT.
3. Verifies the project exists (and the subject, if provided); exits with an error if not.
4. Iterates subjects and experiments; for each experiment, issues one additional REST call to sum its file sizes. Writes one row per experiment with header `PROJECT,SUBJECT_LABEL,SUBJECT_ID,EXPERIMENT_LABEL,EXPERIMENT_ID,EXPERIMENT_DATE,ESTIMATED_SIZE_BYTES`, sorted by `SUBJECT_LABEL` then `EXPERIMENT_LABEL`. If the project (or subject) exists but has no experiments, a header-only CSV is written. An existing output file is overwritten silently.

> **Note:** The size lookup adds one REST call per experiment, so `query` on a large project takes proportionally longer than a size-less listing would.

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
