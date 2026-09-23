# Frequently Asked Questions (FAQ)

Click a question to expand its answer, or use the buttons below to open or close all of them at once.

<button id="faq-expand-all" class="md-button">Expand all</button>
<button id="faq-collapse-all" class="md-button">Collapse all</button>

??? question "When and where can I rename the subjects and dates?"

    There are two rename points: at download time, using the `SUBJECT_BIDS_RENAME`/`EXPERIMENT_BIDS_RENAME` columns in the `query` CSV (or `--rename-subject`/`--rename-experiment` in `-1`/`--accession` mode), which sets the on-disk directory name; and later, using the `participant_rename`/`session_rename` columns in the `bidsmap` TSV, which produces a final renamed copy of the converted BIDS dataset. The `EXPERIMENT_DATE` column from `query` is informational only (e.g., to help you choose a rename) and is not itself renamed.

    **References:**

    - [Manual Steps — renaming during download](manual.md#renaming-subjectexperiment-during-download)
    - [`xnatbidscli bidsmap`](cli/bidsmap.md)

??? question "Where do I store the quality control information for each scan?"

    In `mriconvert_qc.tsv`, written by `mriconvert` at `<output>/PROJECT-<PROJECT>_mriconvert_qc.tsv` — one row per converted `.nii.gz` file. Review the data and fill in its `recommend_for_use`, `complete`, `usable`, `qc_rating`, `rating_reason`, and `qc_notes` columns by hand.

    **References:**

    - [`xnatbidscli mriconvert` — `mriconvert_qc.tsv`](cli/mriconvert.md#mriconvert_qctsv)
    - [Manual Steps — between physioconvert and bidsmap](manual.md#4-between-physioconvert-and-bidsmap)

??? question "How do I filter out scans I don't want with the query command?"

    `query` has no filtering flag of its own — it always writes one row per experiment. To exclude experiments, open the CSV it writes in a spreadsheet editor and delete the rows you don't want before running `download`, since `download --csv` processes every remaining row.

    **References:**

    - [Manual Steps — between query and download](manual.md#1-between-query-and-download)
    - [Using Excel](excel.md)

??? question "Can I import data as ZIP files that are not on XNAT?"

    No — `download` only pulls data from an XNAT server over its REST API, and there is no general-purpose "import a local ZIP" feature. If you have non-XNAT data, you must manually extract it into the same `OUTPUT_DIR/PROJECT/SUBJECT/EXPERIMENT/scans/` layout that `download` produces, so `mriconfig`/`mriconvert` can find it.

    **References:**

    - [`xnatbidscli download`](cli/download.md)

??? question "How do I archive downloaded data?"

    Pass `-a`/`--archive` to `download` (or `mriconvert`) to tar+gzip each experiment's directory into `OUTPUT_DIR/archive/PROJECT-<P>_SUBJECT-<S>_EXPERIMENT-<E>.tar.gz` once it's processed; add `-d`/`--delete` to remove the original directory after the archive is confirmed written.

    **References:**

    - [`xnatbidscli download`](cli/download.md#archiving-and-deletion-a-archive-d-delete)
    - [`xnatbidscli mriconvert`](cli/mriconvert.md)

??? question "How do I make a Dcm2Bids configuration JSON file?"

    Run `xnatbidscli mriconfig` against a representative sample of downloaded experiments; it drafts a `dcm2bids_config_<timestamp>.json` (best-effort guesses) and a `dcm2bids_config_blank_<timestamp>.json` (blank template) under `OUTPUT_DIR/PROJECT-<PROJECT>_mriconfig/`. Edit whichever is the better starting point — and re-run `mriconfig -m` or a small `mriconvert` test batch as needed — until it correctly identifies every scan type, then point `mriconvert -c` at the finished file.

    **References:**

    - [`xnatbidscli mriconfig`](cli/mriconfig.md)
    - [Manual Steps — between download and mriconvert](manual.md#2-between-download-and-mriconvert)

??? question "What should I do if I need to change my Dcm2Bids configuration JSON file? How does this change mriconvert and other downstream commands?"

    Edit the config JSON directly, then re-run `mriconvert -c PATH/TO/config.json` against the affected sessions — `mriconvert` is idempotent and safe to re-run, and `dcm2bids --clobber` lets it overwrite existing output; passing `-c` also updates the `Dcm2BidsConfigPath`/`LastModified` recorded in `mriconvert_qc.json`. `--clobber` only overwrites files regenerated under the *same* name, so if the edit changes how a scan is identified (a different `datatype`/`suffix`/`custom_entities`), the previously generated, differently-named file is left behind rather than removed — delete the affected `sub-<PARTICIPANT>/ses-<SESSION>/` directory before reconverting to avoid orphaned files. In `mriconvert_qc.tsv`, rows are merged by `filename`: an unchanged filename keeps its existing row (and any QC already entered) even if a generator-owned field like `series_number` drifted — drift is only reported as a `WARNING`, never applied — while a renamed file gets a new row with blank review columns, and its old row is preserved even though the file it references is gone. Review and clean up both kinds of leftover rows before running `bidsmap`.

    **References:**

    - [`xnatbidscli mriconfig`](cli/mriconfig.md)
    - [`xnatbidscli mriconvert`](cli/mriconvert.md)
    - [`xnatbidscli mriconvert` — `mriconvert_qc.tsv`](cli/mriconvert.md#mriconvert_qctsv)
    - [Manual Steps — between download and mriconvert](manual.md#2-between-download-and-mriconvert)

??? question "How can I tell what scans aren't converted by Dcm2Bids?"

    Check the `tmp_dcm2bids/` scratch directory that `dcm2bids` writes under the BIDS output — any DICOM series that didn't match a description in your config JSON lands there instead of under `sub-*/ses-*/`. Both `mriconvert_qc.tsv` generation and `bidsmap` explicitly skip this directory, so anything left there stays out of your QC sheet and mapped output until the config is fixed and the session is reconverted.

    **References:**

    - [`xnatbidscli mriconvert` — `mriconvert_qc.tsv`](cli/mriconvert.md#mriconvert_qctsv)
    - [`xnatbidscli bidsmap`](cli/bidsmap.md)

??? question "Why are there two places to rename subjects/participants and experiments/sessions? Which should I use?"

    The download-time rename (`SUBJECT_BIDS_RENAME`/`EXPERIMENT_BIDS_RENAME` or `--rename-subject`/`--rename-experiment`) sets the raw/unmapped `sub-`/`ses-` labels used throughout `mriconvert`/`physioconvert`; the `bidsmap` rename (`participant_rename`/`session_rename`) produces a separate, final renamed copy while preserving that unmapped dataset. Use the download-time rename for an identifier you already know before conversion (e.g. a normalized subject code); reserve `bidsmap` for the final anonymized/shareable naming, since it's meant to be revisited and writes a distinct output tree rather than overwriting anything.

    **References:**

    - [Manual Steps — renaming during download](manual.md#renaming-subjectexperiment-during-download)
    - [`xnatbidscli bidsmap`](cli/bidsmap.md)

??? question "How do I exclude bad scans from my BIDS-mapped dataset at the end?"

    In `mriconvert_qc.tsv`, mark a row's `recommend_for_use`, `complete`, or `usable` column as `FALSE`, or its `qc_rating` as `FAIL` or `UNCERTAIN`, before running `bidsmap -o`. Those rows (and their sidecars) are excluded from the copied dataset and omitted from the resulting `scans.tsv`.

    **References:**

    - [`xnatbidscli bidsmap` — copy-with-rename](cli/bidsmap.md#copy-with-rename-o-output_dir)
    - [Manual Steps — between physioconvert and bidsmap](manual.md#4-between-physioconvert-and-bidsmap)
