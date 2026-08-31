# `xnatcli download`

Downloads every file belonging to one XNAT experiment (single-experiment mode, `-1`) or every experiment listed in an `xnatcli query` CSV (batch mode, `--csv`). Each experiment is fetched as whole-experiment zip archives rather than one HTTP request per file.

1. Loads credentials from `~/.xnatcli/credentials.cfg`; if the file is missing or incomplete, exits with a message telling you to run `xnatcli login`.
2. Connects to the stored server via PyXNAT.
3. For each experiment, walks `project → subject → experiment`, then issues two bulk zip requests against XNAT's REST API: one for all scans, one for all session-level resources.
4. Each zip is extracted directly into `OUTPUT_DIR/PROJECT/SUBJECT/EXPERIMENT/`, following XNAT's own scan/resource folder naming (not a custom path scheme), then discarded.

    `PROJECT` is the canonical XNAT project ID; `SUBJECT` and `EXPERIMENT` are the user-facing labels emitted by `xnatcli query`.

5. With `-a/--archive`, after each experiment is downloaded, its `OUTPUT_DIR/PROJECT/SUBJECT/EXPERIMENT` directory is tar+gzipped to `OUTPUT_DIR/archive/PROJECT-<P>_SUBJECT-<S>_EXPERIMENT-<E>.tar.gz`. An existing archive at that path is left untouched and reported as `SKIPPED`. With `-d/--delete` (requires `-a/--archive`), the `EXPERIMENT` directory is removed once its archive is `COMPLETE` or `SKIPPED`; the `SUBJECT` and then `PROJECT` parent directories are also removed if they become empty as a result.

```bash
# Single experiment
xnatcli download -1 PROJECT SUBJECT EXPERIMENT -o OUTPUT_DIR

# Batch from a query CSV
xnatcli download --csv PATH/TO/QUERY.csv -o OUTPUT_DIR

# Batch download, then archive and delete each experiment's raw files
xnatcli download --csv PATH/TO/QUERY.csv -o OUTPUT_DIR -a -d
```

`-1` and `--csv` are mutually exclusive; exactly one must be supplied.

| Argument | Description |
| --- | --- |
| `-1 PROJECT SUBJECT EXPERIMENT` | Download a single experiment. Each value may be either the XNAT ID or the user-facing label. |
| `-c`, `--csv`, `-i`, `--input` | Path to a CSV file (`xnatcli query` output) listing experiments to download. Must contain the columns `PROJECT`, `SUBJECT_LABEL`, `EXPERIMENT_LABEL`. An `ESTIMATED_SIZE_BYTES` column, if present, drives the per-experiment progress display below; any other columns (e.g., `SUBJECT_ID`, `EXPERIMENT_ID`, `EXPERIMENT_DATE`) are ignored. |
| `-o`, `--output` | **Required.** Directory to write the downloaded files into (created if missing). |
| `-n`, `--ndownload` | *Optional.* Number of parallel experiment downloads for `--csv` (default `1`). Not used with `-1`. |
| `-l`, `--log` | *Optional.* Write a per-experiment log CSV to `OUTPUT_DIR/log/download_<YYYYMMDD_HHMMSS>_log.csv` (local time, captured at run start). |
| `-a`, `--archive` | *Optional.* After downloading each experiment, tar+gzip its `OUTPUT_DIR/PROJECT/SUBJECT/EXPERIMENT` directory into `OUTPUT_DIR/archive/PROJECT-<P>_SUBJECT-<S>_EXPERIMENT-<E>.tar.gz`. Existing archives are skipped with a warning. |
| `-d`, `--delete` | *Optional.* Requires `-a/--archive`. After a successful archive, delete the `OUTPUT_DIR/PROJECT/SUBJECT/EXPERIMENT` directory. Empty `SUBJECT` and `PROJECT` parent directories are also pruned. |

## Per-experiment STATUS (and exit code)

In `--csv` mode, the run continues through all rows even if some fail and exits with a summary; in `-1` mode, the single STATUS determines the exit code. Possible STATUS values:

| STATUS | Meaning |
| --- | --- |
| `COMPLETE` | The scans and/or resources zip request(s) succeeded. |
| `FAILURE` | The experiment exists, but a zip request raised an error. If the underlying HTTP connection was dropped mid-download (a transient network/server timeout), the reported error names this cause explicitly rather than a generic message. |
| `NONEXISTENT` | The experiment lookup did not find anything on the server. |
| `EMPTY` | The experiment exists but has no scans and no session-level resources. |

Exit code is `0` if every processed experiment is `COMPLETE` or `EMPTY`, and `1` otherwise; archiving/deletion outcome (see below) does not affect it.

## Archiving and deletion (`-a`/`--archive`, `-d`/`--delete`)

With `-a/--archive`, each experiment's directory is tar+gzipped regardless of its download STATUS, and one of these is printed:

| Archive STATUS | Meaning |
| --- | --- |
| `COMPLETE` | The tarball was written successfully. |
| `SKIPPED` | An archive already exists at that path; it is left untouched. |
| `FAILURE` | An error occurred while writing the tarball. |
| `NONEXISTENT` | `OUTPUT_DIR/PROJECT/SUBJECT/EXPERIMENT` does not exist (e.g., the download itself failed). |

The tarball is written to a `.tmp` sibling and renamed into place only on success, so an interrupted run never leaves a partial archive behind. `-d/--delete` requires `-a/--archive` and only removes the experiment directory when the archive STATUS is `COMPLETE` or `SKIPPED`.

## Download progress (`--csv` mode)

Each experiment being downloaded under `--csv`/`--input` (regardless of `-n`) has its own background thread that prints a status line roughly every 5 seconds while its scans/resources zip download is in flight:

```text
  [PROJECT/SUBJECT/EXPERIMENT] 45.0% (120.0 MB / 265.0 MB est.)
```

The percentage and total are only shown when that row's `ESTIMATED_SIZE_BYTES` (from the input CSV — see `xnatcli query`) is present and non-zero; otherwise the line shows only the bytes downloaded so far. Progress is measured by polling the size of the in-progress zip file(s) on disk under `OUTPUT_DIR/PROJECT/SUBJECT/EXPERIMENT/`, so it climbs across the scans phase and then the session-resources phase, and stops once the experiment finishes (or fails). Under `-n`, multiple experiments' lines interleave as separate prints — there's no single combined bar. This progress display does not apply to `-1` single-experiment mode.

## Download log CSV (`-l`/`--log`)

When `-l/--log` is supplied, a CSV is written at `OUTPUT_DIR/log/download_<YYYYMMDD_HHMMSS>_log.csv`, where the timestamp is the local-time start of the run. The header is always:

```text
DATESTAMP,PROJECT,SUBJECT,EXPERIMENT,STATUS
```

`DATESTAMP` is the per-experiment download attempt begin time, formatted to match Python's `logging` module default `asctime` (`YYYY-MM-DD HH:MM:SS,mmm`, local time). One row is appended per processed experiment; rows are written under a lock so concurrent workers do not interleave.
