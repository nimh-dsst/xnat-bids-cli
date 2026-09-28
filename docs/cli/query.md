# `xnatbidscli query`

Writes a CSV of one row per experiment — every experiment in a project, or every experiment under a single subject in a project — optionally narrowed by [filters](#filters). The columns are:

| Column | Source |
| --- | --- |
| `PROJECT` | Canonical XNAT project ID. |
| `SUBJECT_LABEL` | User-facing subject label (e.g., `sub-001`). Used by downstream commands and on-disk paths. |
| `SUBJECT_ID` | XNAT accession ID for the subject (e.g., `XNAT_S00001`). |
| `SUBJECT_BIDS_RENAME` | Empty by default. Optional manual entry consumed by `xnatbidscli download` — see [Manual Interventions](../manual.md). |
| `EXPERIMENT_LABEL` | User-facing experiment label (e.g., `ses-baseline`). Used by downstream commands and on-disk paths. |
| `EXPERIMENT_ID` | XNAT accession ID for the experiment (e.g., `XNAT_E00001`). |
| `EXPERIMENT_DATE` | Experiment date in `YYYYMMDD` format. Empty if unset on the server or unparseable. |
| `EXPERIMENT_BIDS_RENAME` | Empty by default. Optional manual entry consumed by `xnatbidscli download` — see [Manual Interventions](../manual.md). |
| `ESTIMATED_SIZE_BYTES` | Sum of every file's size (bytes) under the experiment — scans and session-level resources combined — via XNAT's session-wide `/files` listing. `UNKNOWN` means no size could be determined: the size lookup failed (a warning is printed to stderr in that case, and the query still completes), the server listed no files, or the sizes summed to zero. `0` is never written, because XNAT's file listing is not a reliable sign that an experiment is empty. `FILES_WITH_UNLABELED_SIZE` means the experiment has files but the server reported no `Size` for any of them. `UNPARSEABLE_SIZE_VALUES` means at least one file's `Size` was present but non-numeric. |
| `EXPERIMENT_TIME` | Experiment start time in `HHMMSS` format. DICOM StudyTime, else XNAT's value. |
| `STUDY_DESCRIPTION` | DICOM StudyDescription. |
| `STUDY_UID` | DICOM StudyInstanceUID, else XNAT's session `UID`. Unique to one study. |
| `SCANNER_NAME` | DICOM StationName, else XNAT's scanner name. |
| `SCANNER_MANUFACTURER` | DICOM Manufacturer, else XNAT's scanner manufacturer (e.g., `SIEMENS`). |
| `SCANNER_MODEL` | DICOM ManufacturerModelName, else XNAT's scanner model (e.g., `Prisma`). |
| `SCANNER_SERIAL` | DICOM DeviceSerialNumber. |
| `SOFTWARE_VERSION` | DICOM SoftwareVersions. |
| `FIELD_STRENGTH` | DICOM MagneticFieldStrength, in tesla. |
| `SITE` | DICOM InstitutionName, else XNAT's acquisition site. |
| `OPERATOR` | DICOM OperatorsName, else XNAT's operator. |
| `XNAT_GENDER` | XNAT's subject demographics `gender` field, with `male`/`female`/`other` written as `M`/`F`/`O` (other values as-is). One value per subject. |
| `DICOM_SEX` | DICOM PatientSex (usually `M`, `F` or `O`). Recorded per session, so it can differ between a subject's experiments. |
| `HANDEDNESS` | Subject handedness from XNAT demographics (e.g., `left`, `right`, `ambidextrous`). DICOM has no handedness tag. |
| `AGE` | Age in whole years at the experiment. Uses the first available of: DICOM PatientAge, the experiment's `age`, the subject's date of birth, the subject's year of birth (may be one year high), then the subject's demographics `age`. |

DICOM values come from one DICOM file per experiment, read via XNAT's `dicomdump` service, so only study-level fields are used. Any of the columns from `EXPERIMENT_TIME` on is empty when neither source has the value. Anonymization often strips operator, site and demographics. DICOM-only columns are also empty when the experiment has no DICOM files; a warning is printed to stderr in that case.

> **Note:** XNAT enforces label uniqueness within a project for both subjects and experiments. If a server somehow contains duplicate labels, the resulting CSV may contain rows that downstream commands cannot disambiguate.

1. Loads credentials from `~/.xnatbidscli/credentials.cfg`; if the file is missing or incomplete, exits with a message telling you to run `xnatbidscli login`.
2. Connects to the stored server via PyXNAT.
3. Verifies the project exists (and the subject, if provided); exits with an error if not.
4. Iterates subjects and experiments, making one REST call per subject (demographics) and three per experiment (experiment record, DICOM header, file sizes). Filters are checked as soon as their value is known, so a dropped subject or experiment skips the remaining calls. Writes one row per kept experiment with the columns above, in that order, sorted by `SUBJECT_LABEL` then `EXPERIMENT_LABEL`. `SUBJECT_BIDS_RENAME` and `EXPERIMENT_BIDS_RENAME` are always written empty. If no experiments exist or none pass the filters, a header-only CSV is written.

> **Note:** With three REST calls per experiment, `query` on a large project can take a while. `--accession`, `--handedness` and `--date` drop experiments before the DICOM and size calls, which saves time. Every other filter needs the DICOM header, so it saves only the size call.

While it runs, `query` shows a line such as `Querying PROJECT: 37 experiment(s) checked, 12 matched, elapsed 42s`, updated in place every 5 seconds. "Checked" counts experiments looked at so far, and "matched" counts those that passed every filter. The line only appears when stdout is a terminal. When it finishes, `query` prints `Took N second(s) to write N row(s) to OUTPUT_PATH`.

```bash
xnatbidscli query PROJECT [SUBJECT] -o OUTPUT_DIR [FILTERS...]
```

The output filename, where `<YYYYMMDD_HHMMSS>` is the local-time timestamp of the write (so each run gets its own file rather than overwriting a previous one), is:

- `OUTPUT_DIR/PROJECT-<PROJECT>_<YYYYMMDD_HHMMSS>.csv` when only `PROJECT` is supplied.
- `OUTPUT_DIR/PROJECT-<PROJECT>_SUBJECT-<SUBJECT>_<YYYYMMDD_HHMMSS>.csv` when both positional arguments are supplied.

On Linux and macOS the CSV is created with mode `rw-------` (`0o600`), so only its owner can read or write it. On Windows, access comes from the folder's ACLs instead. After writing, `query` prints an `INFO:` line reminding you that sharing the CSV means changing its group and permissions, and that it may contain PII.

> **Warning:** The CSV may contain personally identifiable information (PII) and protected health information (PHI). Some XNAT servers keep Medical Record Numbers (MRNs) in subject labels or accession numbers. The CSV can also hold scan dates and times, age at scan, sex, site and operator names. To share a query CSV, set its group with `chgrp` to one holding only approved study staff, then grant access with `chmod g+r` (or `g+rw`); check the result with `ls -l`. Copies, or files re-saved by a spreadsheet editor, may not keep these permissions. If labels contain MRNs, they also end up in `download` folder names and log CSVs (and everything built from them) unless you fill in `SUBJECT_BIDS_RENAME` and `EXPERIMENT_BIDS_RENAME` before downloading — see [Manual Steps](../manual.md#renaming-subjectexperiment-during-download).

| Argument | Description |
| --- | --- |
| `PROJECT` | XNAT project (ID or label). |
| `SUBJECT` | *Optional.* XNAT subject (ID or label). If omitted, all subjects in the project are listed. |
| `-o`, `--output` | **Required.** Directory to write the CSV file into (created if missing). |

## Filters

Each filter keeps only the experiments that match. When you use several filter flags, an experiment must match all of them. When you give one flag several space-separated values, an experiment only needs to match one of them. An experiment whose value is blank for a filtered field is dropped.

| Filter | Matches column(s) | How it matches |
| --- | --- | --- |
| `--accession ID ...` | `SUBJECT_ID`, `SUBJECT_LABEL`, `EXPERIMENT_ID`, `EXPERIMENT_LABEL`, `STUDY_UID` | Exact, case-insensitive. Each value can be a subject ID or label (keeps all of that subject's experiments), an experiment ID or label, or a StudyInstanceUID. When XNAT stores a study UID, it is used to drop non-matching experiments before the DICOM read. |
| `--date DATE ...` | `EXPERIMENT_DATE` | `MM` (that month in any year), `YYYY`, `YYYYMM` or `YYYYMMDD`. |
| `--time TIME ...` | `EXPERIMENT_TIME` | `HH`, `HHMM` or `HHMMSS` (prefix of the start time). |
| `--scanner TEXT ...` | `SCANNER_NAME`, `SCANNER_MANUFACTURER`, `SCANNER_MODEL` | Substring of any of them, case-insensitive. |
| `--study TEXT ...` | `STUDY_DESCRIPTION` | Substring, case-insensitive. |
| `--site TEXT ...` | `SITE` | Substring, case-insensitive. |
| `--operator TEXT ...` | `OPERATOR` | Substring, case-insensitive. |
| `--sex SEX ...` | `XNAT_GENDER`, `DICOM_SEX` | `M`/`F`/`O` or `male`/`female`/`other`, case-insensitive. Kept if either column matches. |
| `--handedness {L,R,A,U} ...` | `HANDEDNESS` | First letter of the XNAT value: left, right, ambidextrous, unknown. |
| `--age YEARS ...` | `AGE` | Exact whole years. |

```bash
xnatbidscli query PROJECT -o OUTPUT_DIR --scanner siemens --date 2024 --handedness R L
```

> **Note:** Filter flags take one or more values, so put `SUBJECT` before any filter flag (otherwise it is read as a filter value).
