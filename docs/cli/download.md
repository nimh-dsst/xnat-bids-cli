# `xnatbidscli download`

Downloads every file belonging to one XNAT experiment (single-experiment mode, `-1`), one unique accession number (`--accession`), or every experiment listed in an `xnatbidscli query` CSV (batch mode, `--csv`). Each experiment is fetched as whole-experiment zip archives rather than one HTTP request per file.

1. Loads credentials from `~/.xnatbidscli/credentials.cfg`; if the file is missing or incomplete, exits with a message telling you to run `xnatbidscli login`.
2. Connects to the stored server via PyXNAT.
3. For each experiment, walks `project → subject → experiment`, then issues zip requests against XNAT's REST API: one bulk request for all scans, and one request per session-level resource (XNAT has no bulk "all resources" export endpoint, unlike scans). With `--accession`, an extra lookup happens first: the given ID is checked against XNAT's server-wide `/subjects` listing, then its `/experiments` listing, to find which project (and, for an experiment accession, which subject) it belongs to. This works with a bare XNAT ID because IDs are unique across the whole server; labels are not (a label is only unique within its parent), so `--accession` does not accept labels — use `-1` for that.
4. Each zip is extracted directly into `OUTPUT_DIR/PROJECT/SUBJECT/EXPERIMENT/`, following XNAT's own scan/resource folder naming (not a custom path scheme), then discarded. If a resource resolves to a single file, XNAT sometimes streams that file directly instead of wrapping it in a zip; this is detected and the file is saved as-is rather than failing the experiment. If some resources download successfully and others fail, the successful ones are still kept and the experiment is reported as `FAILURE` with each failing resource named in the error.

    `PROJECT` is the canonical XNAT project ID; `SUBJECT` and `EXPERIMENT` are the user-facing labels emitted by `xnatbidscli query`, unless overridden by that row's `SUBJECT_BIDS_RENAME`/`EXPERIMENT_BIDS_RENAME` values in `--csv` mode, or by `--rename-subject`/`--rename-experiment` in `-1`/`--accession` mode (see [Manual Interventions](../manual.md)) — XNAT is still queried using the original labels either way. In `--accession` mode, the resolved XNAT IDs (not labels) are used both to query XNAT and, absent a rename, as the on-disk `SUBJECT`/`EXPERIMENT` directory names.

5. With `-a/--archive`, after each experiment is downloaded, its `OUTPUT_DIR/PROJECT/SUBJECT/EXPERIMENT` directory (using any rename override) is tar+gzipped to `OUTPUT_DIR/archive/PROJECT-<P>_SUBJECT-<S>_EXPERIMENT-<E>.tar.gz`. An existing archive at that path is left untouched and reported as `SKIPPED`. With `-d/--delete` (requires `-a/--archive`), the `EXPERIMENT` directory is removed once its archive is `COMPLETE` or `SKIPPED`; the `SUBJECT` and then `PROJECT` parent directories are also removed if they become empty as a result.

```bash
# Single experiment
xnatbidscli download -1 PROJECT SUBJECT EXPERIMENT -o OUTPUT_DIR

# Single experiment, renamed on disk
xnatbidscli download -1 PROJECT SUBJECT EXPERIMENT -o OUTPUT_DIR --rename-subject 01 --rename-experiment baseline

# All experiments for one subject, by that subject's accession number alone
xnatbidscli download --accession XNAT_S00001 -o OUTPUT_DIR

# One experiment, by its accession number alone
xnatbidscli download --accession XNAT_E00042 -o OUTPUT_DIR --rename-experiment baseline

# Batch from a query CSV
xnatbidscli download --csv PATH/TO/QUERY.csv -o OUTPUT_DIR

# Batch download, then archive and delete each experiment's raw files
xnatbidscli download --csv PATH/TO/QUERY.csv -o OUTPUT_DIR -a -d
```

`-1`, `--accession`, and `--csv` are mutually exclusive; exactly one must be supplied.

| Argument | Description |
| --- | --- |
| `-1 PROJECT SUBJECT EXPERIMENT` | Download a single experiment. Each value may be either the XNAT ID or the user-facing label. |
| `--accession ACCESSION` | Download by a single unique XNAT ID, with no `PROJECT`/`SUBJECT` needed — must be an ID, not a label (labels aren't unique server-wide). If `ACCESSION` identifies a subject, every experiment for that subject is downloaded; if it identifies an experiment, only that one is. |
| `--rename-subject` | *Optional, `-1`/`--accession` only.* Rename the on-disk `SUBJECT` directory to this value (`sub-` prepended if missing) — see [Manual Interventions](../manual.md). Errors with `--csv`. |
| `--rename-experiment` | *Optional, `-1` or an experiment `--accession` only.* Rename the on-disk `EXPERIMENT` directory to this value (`ses-` prepended if missing) — see [Manual Interventions](../manual.md). Errors with `--csv` or a subject `--accession` (which may resolve to more than one experiment). |
| `-c`, `--csv`, `-i`, `--input` | Path to a CSV file (`xnatbidscli query` output) listing experiments to download. Must contain the columns `PROJECT`, `SUBJECT_LABEL`, `EXPERIMENT_LABEL`. An `ESTIMATED_SIZE_BYTES` column, if present, drives the per-experiment progress display below. `SUBJECT_BIDS_RENAME`/`EXPERIMENT_BIDS_RENAME` columns, if present, rename the on-disk `SUBJECT`/`EXPERIMENT` directories — see [Manual Interventions](../manual.md). Any other columns (e.g., `SUBJECT_ID`, `EXPERIMENT_ID`, `EXPERIMENT_DATE`) are ignored. |
| `-o`, `--output` | **Required.** Directory to write the downloaded files into (created if missing). |
| `-n`, `--ndownload` | *Optional.* Number of parallel experiment downloads for `--csv`, or for a subject `--accession` with more than one experiment (default `1`). Not used with `-1`. |
| `-l`, `--log` | *Optional.* Write a per-experiment log CSV to `OUTPUT_DIR/log/download_<YYYYMMDD_HHMMSS>_log.csv` (local time, captured at run start). |
| `-a`, `--archive` | *Optional.* After downloading each experiment, tar+gzip its `OUTPUT_DIR/PROJECT/SUBJECT/EXPERIMENT` directory into `OUTPUT_DIR/archive/PROJECT-<P>_SUBJECT-<S>_EXPERIMENT-<E>.tar.gz`. Existing archives are skipped with a warning. |
| `-d`, `--delete` | *Optional.* Requires `-a/--archive`. After a successful archive, delete the `OUTPUT_DIR/PROJECT/SUBJECT/EXPERIMENT` directory. Empty `SUBJECT` and `PROJECT` parent directories are also pruned. |

## Per-experiment STATUS (and exit code)

In `--csv` mode, and in `--accession` mode when it resolves to a subject, the run continues through all experiments even if some fail and exits with a summary; in `-1` mode, and in `--accession` mode when it resolves to an experiment, the single STATUS determines the exit code. Possible STATUS values:

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

## Download progress (`--csv` mode, and a multi-experiment `--accession`)

Each experiment being downloaded under `--csv`/`--input`, or under a subject `--accession` (regardless of `-n`), has its own background thread that updates a status line roughly every 5 seconds while its scans/resources zip download is in flight:

```text
  [PROJECT/SUBJECT/EXPERIMENT] 45.0% (120.0 MB / 265.0 MB est.)
```

The percentage and total are only shown when that row's `ESTIMATED_SIZE_BYTES` (from the input CSV — see `xnatbidscli query`) is present and non-zero; a subject `--accession` has no such estimate, so its lines always show only the bytes downloaded so far. Progress is measured by polling the size of the in-progress zip file(s) on disk under `OUTPUT_DIR/PROJECT/SUBJECT/EXPERIMENT/`, so it climbs across the scans phase and then the session-resources phase, and stops once the experiment finishes (or fails), at which point its line disappears. When stdout is a terminal, each experiment gets one line that updates in place (via ANSI cursor movement), so under `-n` you see one persistently-updating row per concurrently-downloading experiment rather than a new printed line every interval; other messages (errors, archive status) print normally above the progress rows. When stdout isn't a terminal (e.g. redirected to a file), this falls back to a plain new line per update. This progress display does not apply to `-1` single-experiment mode or an experiment `--accession`.

## Stopping a run (Ctrl+C)

In `--csv` mode, or a subject `--accession`, with `-n` > 1, pressing Ctrl+C cancels every experiment that hasn't started downloading yet, then exits immediately — it does not wait for experiments already mid-transfer, since their zip download can't be cancelled cooperatively. Those in-flight experiments' output under `OUTPUT_DIR` may be left incomplete and should be re-run. With `-n 1` (the default), `-1` single-experiment mode, or an experiment `--accession`, Ctrl+C stops after the current experiment finishes.

## Download log CSV (`-l`/`--log`)

When `-l/--log` is supplied, a CSV is written at `OUTPUT_DIR/log/download_<YYYYMMDD_HHMMSS>_log.csv`, where the timestamp is the local-time start of the run. The header is always:

```text
DATESTAMP,USER,PROJECT,SUBJECT,EXPERIMENT,STATUS
```

`DATESTAMP` is the per-experiment download attempt begin time, formatted to match Python's `logging` module default `asctime` (`YYYY-MM-DD HH:MM:SS,mmm`, local time). `USER` is the OS-level username running the command (via Python's `getpass.getuser()`). `SUBJECT`/`EXPERIMENT` reflect any rename override (`SUBJECT_BIDS_RENAME`/`EXPERIMENT_BIDS_RENAME` in `--csv` mode, `--rename-subject`/`--rename-experiment` in `-1`/`--accession` mode — see [Manual Interventions](../manual.md)), i.e. the on-disk directory names actually written under `OUTPUT_DIR` (for `--accession` without a rename, this is the resolved XNAT ID, not a label), not necessarily the original XNAT labels. One row is appended per processed experiment; rows are written under a lock so concurrent workers do not interleave.
