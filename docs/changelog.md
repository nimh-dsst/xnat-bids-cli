# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/2.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Planned as 2.0.0. The project is renamed from `xnatcli` to `xnatbidscli` and will be published on PyPI. Downloads are faster and more reliable, and you can now rename subjects and experiments between `query` and `download`.

### Added

- The package is set up for PyPI (`pip install xnatbidscli`). The version now comes from git tags, and a GitHub Action publishes each new GitHub release.
- `download --accession ACCESSION` downloads by one subject ID or label, experiment ID or label, or StudyInstanceUID alone, with no project or subject needed. A subject downloads every experiment for that subject. An experiment downloads only that experiment. A label that matches more than one subject or experiment on the server is an error that lists the matches.
- `query` filter flags: `--accession` (subject ID or label, experiment ID or label, or StudyInstanceUID), `--date`, `--time`, `--scanner`, `--study`, `--site`, `--operator`, `--sex`, `--handedness` and `--age`. Different flags must all match, and any one value of a flag can match. Text filters match a case-insensitive substring. Experiments with a blank value for a filtered field are dropped.
- `query` CSVs have new columns at the end: `EXPERIMENT_TIME`, `STUDY_DESCRIPTION`, `STUDY_UID`, `SCANNER_NAME`, `SCANNER_MANUFACTURER`, `SCANNER_MODEL`, `SCANNER_SERIAL`, `SOFTWARE_VERSION`, `FIELD_STRENGTH`, `SITE`, `OPERATOR`, `XNAT_GENDER`, `DICOM_SEX`, `HANDEDNESS` and `AGE`. `XNAT_GENDER` and `DICOM_SEX` both use `M`/`F`/`O` codes, and `--sex` keeps an experiment if either one matches. `AGE`, `EXPERIMENT_TIME`, `STUDY_UID`, `SCANNER_NAME`, `SCANNER_MANUFACTURER`, `SCANNER_MODEL`, `SITE` and `OPERATOR` come from the DICOM header first, with XNAT's value as the fallback.
- `download --rename-subject` and `--rename-experiment` rename the on-disk `SUBJECT`/`EXPERIMENT` directories in `-1` and `--accession` modes.
- `query` CSVs have new columns: `SUBJECT_BIDS_RENAME`, `EXPERIMENT_BIDS_RENAME` and `ESTIMATED_SIZE_BYTES`. `download --csv` renames directories using the two rename columns.
- `download` shows progress for each experiment in `--csv` mode and for a subject `--accession`. It shows a percentage when `ESTIMATED_SIZE_BYTES` is available.
- `mriconvert` prints an elapsed-time line every 5 seconds while `dcm2bids` runs.
- `query` shows a status line while it runs, updated every 5 seconds, with the elapsed time and the number of experiments checked and matched. It only appears when stdout is a terminal.
- `mriconvert_qc.json` records a `LastModified` timestamp for `Dcm2BidsConfigPath`.
- `physioconvert` has a new `SKIPPED` status. It skips an association when its output `_physio.tsv.gz` already exists and is valid.
- Saved credentials at `~/.xnatcli/credentials.cfg` move automatically to `~/.xnatbidscli/credentials.cfg` the first time a command needs them.
- New docs pages: [Manual Steps](manual.md), [Frequently Asked Questions](faq.md) and [Using Excel](excel.md). The [`query`](cli/query.md), [Manual Steps](manual.md) and [Quickstart](quickstart.md) pages now warn that the query CSV can contain PII, and that unrenamed labels carry it into `download` folder names and logs. There is also a "Project feedback" section on the [Overview](index.md) and a GitHub repository link on every page.

### Changed

- **Breaking:** The CLI command, Python package and config directory are renamed from `xnatcli` to `xnatbidscli`. Re-run `uv sync` to install the new command. See [Installation](installation.md#upgrading-from-xnatcli).
- **Breaking:** `download` now fetches each experiment as whole-experiment zip archives, not one file at a time. Files are saved using XNAT's own scan and resource folder names. The `PARTIAL` status is removed.
- **Breaking:** `download -n/--ndownload` now sets how many experiments download in parallel. It is not used with `-1`.
- **Breaking:** `query` output filenames now end in a `_<YYYYMMDD_HHMMSS>` timestamp, so a new run no longer overwrites an old file.
- **Breaking:** The `query` CSV has a new column order: `PROJECT,SUBJECT_LABEL,SUBJECT_ID,SUBJECT_BIDS_RENAME,EXPERIMENT_LABEL,EXPERIMENT_ID,EXPERIMENT_DATE,EXPERIMENT_BIDS_RENAME,ESTIMATED_SIZE_BYTES`, followed by the new metadata columns listed above.
- **Breaking:** The log CSVs from `download`, `mriconfig`, `mriconvert`, `physioconvert` and `cubids` have a new `USER` column after `DATESTAMP`.
- `query` creates its CSV with mode `rw-------` (owner only) on Linux and macOS, since it can hold PII such as MRNs, scan dates and ages. It then prints an `INFO:` line explaining that sharing the CSV requires changing its group and permissions.
- `query` now finishes with `Took N second(s) to write N row(s) to ...` instead of `Wrote N row(s) to ...`.
- `query` makes one REST call per subject and three per experiment (record, DICOM header, size), so it is slower on large projects. Filters skip the later calls for experiments they drop.
- `mriconvert` uses `sub-<label>`/`ses-<label>` directory names exactly as they are when `download` has already renamed them.
- `download` Ctrl+C with `-n` > 1: experiments that have not started are cancelled, and the command exits right away.

### Fixed

- `download` is more stable, especially for session-level resources. It saves a single-file resource correctly when XNAT sends the file without a zip.
- `download` now reports a dropped connection clearly, instead of showing a generic error.
- When `physioconvert` runs in parallel, lines from different workers no longer get mixed together in the log.

## [1.0.0] - 2026-07-15

First release.

### Added

- `xnatcli` subcommands: `login`, `query`, `download`, `mriconfig`, `mriconvert`, `physioconvert`, `bidsmap` and `cubids`.
- A documentation site built with Zensical and hosted on Read the Docs.

[Unreleased]: https://github.com/nimh-dsst/xnat-bids-cli/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/nimh-dsst/xnat-bids-cli/releases/tag/v1.0.0
