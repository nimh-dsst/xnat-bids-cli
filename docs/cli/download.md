# `xnatcli download`

Downloads every file belonging to one XNAT experiment (single-experiment mode, `-1`) or every experiment listed in an `xnatcli query` CSV (batch mode, `--csv`). Each experiment is fetched as whole-experiment zip archives rather than one HTTP request per file.

1. Loads credentials from `~/.xnatcli/credentials.cfg`; if the file is missing or incomplete, exits with a message telling you to run `xnatcli login`.
2. Connects to the stored server via PyXNAT.
3. For each experiment, walks `project → subject → experiment`, then issues two bulk zip requests against XNAT's REST API: one for all scans, one for all session-level resources.
4. Each zip is extracted directly into `OUTPUT_DIR/PROJECT/SUBJECT/EXPERIMENT/`, following XNAT's own scan/resource folder naming (not a custom path scheme), then discarded.

    `PROJECT` is the canonical XNAT project ID; `SUBJECT` and `EXPERIMENT` are the user-facing labels emitted by `xnatcli query`.

```bash
# Single experiment
xnatcli download -1 PROJECT SUBJECT EXPERIMENT -o OUTPUT_DIR

# Batch from a query CSV
xnatcli download --csv PATH/TO/QUERY.csv -o OUTPUT_DIR
```

`-1` and `--csv` are mutually exclusive; exactly one must be supplied.

| Argument | Description |
| --- | --- |
| `-1 PROJECT SUBJECT EXPERIMENT` | Download a single experiment. Each value may be either the XNAT ID or the user-facing label. |
| `-c`, `--csv`, `-i`, `--input` | Path to a CSV file (`xnatcli query` output) listing experiments to download. Must contain the columns `PROJECT`, `SUBJECT_LABEL`, `EXPERIMENT_LABEL`; any other columns (e.g., `SUBJECT_ID`, `EXPERIMENT_ID`, `EXPERIMENT_DATE`) are ignored. |
| `-o`, `--output` | **Required.** Directory to write the downloaded files into (created if missing). |
| `-n`, `--ndownload` | *Optional.* Number of parallel experiment downloads for `--csv` (default `1`). Not used with `-1`. |
| `-l`, `--log` | *Optional.* Write a per-experiment log CSV to `OUTPUT_DIR/log/download_<YYYYMMDD_HHMMSS>_log.csv` (local time, captured at run start). |

## Per-experiment STATUS (and exit code)

In `--csv` mode, the run continues through all rows even if some fail and exits with a summary; in `-1` mode, the single STATUS determines the exit code. Possible STATUS values:

| STATUS | Meaning |
| --- | --- |
| `COMPLETE` | The scans and/or resources zip request(s) succeeded. |
| `FAILURE` | The experiment exists, but a zip request raised an error. If the underlying HTTP connection was dropped mid-download (a transient network/server timeout), the reported error names this cause explicitly rather than a generic message. |
| `NONEXISTENT` | The experiment lookup did not find anything on the server. |
| `EMPTY` | The experiment exists but has no scans and no session-level resources. |

Exit code is `0` if every processed experiment is `COMPLETE` or `EMPTY`, and `1` otherwise.

## Download log CSV (`-l`/`--log`)

When `-l/--log` is supplied, a CSV is written at `OUTPUT_DIR/log/download_<YYYYMMDD_HHMMSS>_log.csv`, where the timestamp is the local-time start of the run. The header is always:

```text
DATESTAMP,PROJECT,SUBJECT,EXPERIMENT,STATUS
```

`DATESTAMP` is the per-experiment download attempt begin time, formatted to match Python's `logging` module default `asctime` (`YYYY-MM-DD HH:MM:SS,mmm`, local time). One row is appended per processed experiment; rows are written under a lock so concurrent workers do not interleave.
