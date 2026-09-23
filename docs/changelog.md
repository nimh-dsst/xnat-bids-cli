# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/2.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Planned as 2.0.0. The project is renamed from `xnatcli` to `xnatbidscli` and will be published on PyPI. Downloads are faster and more reliable, and you can now rename subjects and experiments between `query` and `download`.

### Added

- The package is set up for PyPI (`pip install xnatbidscli`). The version now comes from git tags, and a GitHub Action publishes each new GitHub release.
- `download --accession ACCESSION` downloads by one XNAT ID alone, with no project or subject needed. A subject ID downloads every experiment for that subject. An experiment ID downloads only that experiment.
- `download --rename-subject` and `--rename-experiment` rename the on-disk `SUBJECT`/`EXPERIMENT` directories in `-1` and `--accession` modes.
- `query` CSVs have new columns: `SUBJECT_BIDS_RENAME`, `EXPERIMENT_BIDS_RENAME` and `ESTIMATED_SIZE_BYTES`. `download --csv` renames directories using the two rename columns.
- `download` shows progress for each experiment in `--csv` mode and for a subject `--accession`. It shows a percentage when `ESTIMATED_SIZE_BYTES` is available.
- `mriconvert` prints an elapsed-time line every 5 seconds while `dcm2bids` runs.
- `mriconvert_qc.json` records a `LastModified` timestamp for `Dcm2BidsConfigPath`.
- `physioconvert` has a new `SKIPPED` status. It skips an association when its output `_physio.tsv.gz` already exists and is valid.
- Saved credentials at `~/.xnatcli/credentials.cfg` move automatically to `~/.xnatbidscli/credentials.cfg` the first time a command needs them.
- New docs pages: [Manual Steps](manual.md), [Frequently Asked Questions](faq.md) and [Using Excel](excel.md). There is also a "Project feedback" section on the [Overview](index.md) and a GitHub repository link on every page.

### Changed

- **Breaking:** The CLI command, Python package and config directory are renamed from `xnatcli` to `xnatbidscli`. Re-run `uv sync` to install the new command. See [Installation](installation.md#upgrading-from-xnatcli).
- **Breaking:** `download` now fetches each experiment as whole-experiment zip archives, not one file at a time. Files are saved using XNAT's own scan and resource folder names. The `PARTIAL` status is removed.
- **Breaking:** `download -n/--ndownload` now sets how many experiments download in parallel. It is not used with `-1`.
- **Breaking:** `query` output filenames now end in a `_<YYYYMMDD_HHMMSS>` timestamp, so a new run no longer overwrites an old file.
- **Breaking:** The `query` CSV has a new column order: `PROJECT,SUBJECT_LABEL,SUBJECT_ID,SUBJECT_BIDS_RENAME,EXPERIMENT_LABEL,EXPERIMENT_ID,EXPERIMENT_DATE,EXPERIMENT_BIDS_RENAME,ESTIMATED_SIZE_BYTES`.
- **Breaking:** The log CSVs from `download`, `mriconfig`, `mriconvert`, `physioconvert` and `cubids` have a new `USER` column after `DATESTAMP`.
- `query` makes one extra REST call per experiment to estimate its size, so it is slower on large projects.
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
