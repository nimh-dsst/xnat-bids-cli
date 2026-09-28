# Quickstart Examples

This file describes examples of common xnatbidscli usage, and in which order, to get you started quickly.

## 0. Installation

```shell
pip install xnatbidscli
```

See [Installation](installation.md) to install unreleased features from source with uv.

## 1. Logging into XNAT

```shell
> xnatbidscli login
XNAT server URL: https://xnat-server.domain.edu
Username: myusername
Password: 
Credentials verified and saved to ~/.xnatbidscli/credentials.cfg
```

## 2. Querying a project

```shell
> xnatbidscli query PROJECT_ID -o ~/data/xnat
Took 95 second(s) to write 100 row(s) to ~/data/xnat/PROJECT-PROJECT_ID_20260916_120000.csv
```

> **Warning:** The query CSV can contain personally identifiable information (PII) from XNAT, such as Medical Record Numbers (MRNs), scan dates and age at scan. On Linux and macOS it is created readable only by its owner (`rw-------`); to share it, change its group and permissions deliberately. If subject or experiment labels contain MRNs, fill in the `SUBJECT_BIDS_RENAME` and `EXPERIMENT_BIDS_RENAME` columns before step 3. Otherwise those labels become folder names and appear in download logs. See [`xnatbidscli query`](cli/query.md) for details.

## 3. Downloading a project

### Whole query output CSV

```shell
xnatbidscli download -c ~/data/xnat/PROJECT-PROJECT_ID_20260916_120000.csv -o ~/data/xnat -n 8 -l
```

This downloads all the experiments listed in the CSV file `PROJECT-PROJECT_ID_20260916_120000.csv` to the directory `~/data/xnat`. The `-n 8` option specifies that 8 parallel downloads should be used, and the `-l` option indicates that the process should save logs.

### Single experiment

```shell
xnatbidscli download -1 PROJECT SUBJECT EXPERIMENT -o ~/data/xnat -n 8 -l --rename-subject sub-01 --rename-experiment ses-01
```

Instead of a whole-project CSV, `-1 PROJECT SUBJECT EXPERIMENT` downloads a single experiment (optionally renaming it on disk with `--rename-subject`/`--rename-experiment`)

### By accession number

```shell
xnatbidscli download --accession XNAT_ID -o ~/data/xnat -n 8 -l --rename-subject sub-01 --rename-experiment ses-01
```

`--accession` downloads by a subject or experiment ID or label, or a StudyInstanceUID, with no project or subject needed.

## 4. Creating a "starter" Dcm2Bids configuration JSON file

```shell
xnatbidscli mriconfig -i ~/data/xnat -p PROJECT_ID -o ~/data/unmapped -n 4 -l -d
```

This command generates starter Dcm2Bids configuration JSON files based on the MRI data downloaded from the `~/data/xnat` directory. The `-i` option specifies the input directory containing the downloaded data, `-p` specifies the project ID (it should be within that input directory), `-o` specifies the output directory for the configuration files, `-n 4` indicates that 4 parallel processes should be used to run dcm2bids_helper, and `-l` option indicates that the process should save logs. The `-d` option (recommended) indicates you would like to delete all the NIfTI files and only keep the JSON files after running the helper.

This writes two timestamped drafts to `~/data/unmapped/PROJECT-PROJECT_ID_mriconfig/`: `dcm2bids_config_<YYYYMMDD_HHMMSS>.json` (best-effort `datatype`/`suffix`/entities guessed from each series) and `dcm2bids_config_blank_<YYYYMMDD_HHMMSS>.json` (one blank description per series, for building a config by hand). Pick whichever is the better starting point, rename it, and edit it to match the project's actual sequences before the next step.

## 5. Running Dcm2Bids with `mriconvert`

This sort of step should be performed after the Dcm2Bids configuration JSON file has been generated and modified to match the specific needs of the project.

```shell
xnatbidscli mriconvert -i ~/data/xnat -p PROJECT_ID -o ~/data/unmapped -y ~/data/physio -c ~/data/unmapped/PROJECT-PROJECT_ID_mriconfig/dcm2bids_config_20260916_120000.json -n 4 -l
```

This command runs Dcm2Bids on the MRI data downloaded from the `~/data/xnat` directory. The `-i` option specifies the input directory containing the downloaded data, `-p` specifies the project ID (it should be within that input directory), `-o` specifies the output directory for the BIDS-formatted (f)MRI data, `-c` specifies the path to the (edited) Dcm2Bids configuration JSON file from the previous step, `-y` specifies the path to a directory containing physiological data (if any), `-n 4` indicates that 4 parallel processes should be used to run dcm2bids on XNAT downloaded experiments, and `-l` option indicates that the process should save logs. Each in-progress session prints a `converting... elapsed ###s` line every 5 seconds.

This also (re)writes `~/data/unmapped/PROJECT-PROJECT_ID_mriconvert_qc.tsv` — the per-scan QC sheet you fill in next: mark `rename`, `physio`, and review columns (`recommend_for_use`, `complete`, `usable`, `qc_rating`, ...) here before continuing.

## 6. Converting physiological data using `phys2bids`

This step should be performed after the `mriconvert` step has been completed and the BIDS-formatted (f)MRI data has been generated and, most importantly, the physio file basenames have been added to `PROJECT-PROJECT_ID_mriconvert_qc.tsv` file's `physio` column rows. Only the physio files that have been added to the `mriconvert_qc.tsv` file (and are present in the `PhysioParent` directory passed via `mriconvert -y`) will be converted to BIDS format.

```shell
xnatbidscli physioconvert -o ~/data/unmapped -p PROJECT_ID -n 10 -l
```

Also, re-running this command is cheap: an association whose output already exists on disk (and is a valid, non-corrupt file) is reported `SKIPPED` rather than reconverted, so it's safe to re-run after adding more `physio` rows without redoing earlier work.

## 7. Preparing to map converted BIDS data to renamed/ready-to-share data

```shell
xnatbidscli bidsmap -i ~/data/unmapped -p PROJECT_ID
```

Writes a file for mapping all participants and sessions to new renamed/anonymized/sanitized file and folder names to `~/data/unmapped/PROJECT-PROJECT_ID_bidsmap.tsv`. Fill in the blanks and you're ready to actually map the files.

## 8. Mapping BIDS data from converted to renamed or ready-to-share data

```shell
xnatbidscli bidsmap -i ~/data/unmapped -p PROJECT_ID -o ~/data/mapped
```

Uses the `~/data/unmapped/PROJECT-PROJECT_ID_bidsmap.tsv` file to map all participants and sessions to new renamed/anonymized/sanitized file and folder names into the `~/data/mapped` directory.

## 9. (Optional) Grouping acquisitions with `cubids`

```shell
xnatbidscli cubids -i ~/data/unmapped -p PROJECT_ID -l
```

Runs [`CuBIDS`](https://cubids.readthedocs.io/) `add-nifti-info` and `group` against the converted BIDS dataset at `~/data/unmapped/PROJECT_ID/`, writing `v0_summary.tsv`/`v0_files.tsv`/`v0_AcqGrouping.tsv`/`v0_AcqGroupInfo.txt` to `~/data/unmapped/PROJECT-PROJECT_ID_cubids/CuBIDS/` — useful for spotting acquisition-parameter inconsistencies before or after mapping.
