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
