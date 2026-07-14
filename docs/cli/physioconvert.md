# `xnatcli physioconvert`

Converts physio recordings **associated with an `xnatcli mriconvert` BIDS dataset** to [BIDS physiological recordings](https://bids-specification.readthedocs.io/en/stable/modality-specific-files/physiological-recordings.html) (`_physio.tsv.gz` + `_physio.json`), using [`phys2bids`](https://phys2bids.readthedocs.io/) (imported as a Python library) to read the files and write the BIDS output. It must run **after** `xnatcli mriconvert` and **before** `xnatcli bidsmap -o` (the association it consumes lives in `mriconvert`'s raw `mriconvert_qc.tsv`, and the `physio` column is dropped once `bidsmap` promotes it to `scans.tsv`).

Each physio recording is tied to one MRI scan by hand: fill in `mriconvert_qc.tsv`'s `physio` column with the raw recording's basename (with extension), found by browsing the flat directory recorded as `PhysioParent` in `mriconvert_qc.json` (set via `xnatcli mriconvert -y/--physio`). `physioconvert` then converts and places that recording **directly alongside its paired scan** — no filename parsing or guessed entities.

1. Validates that `OUTPUT_DIR/PROJECT-<PROJECT>_mriconvert_qc.tsv` exists (i.e. `xnatcli mriconvert` has already run); exits with a message if `phys2bids` is unavailable.
2. Reads `mriconvert_qc.tsv` (read-only — `physioconvert` never writes it back) and scopes to every row with a non-blank `physio` column.
3. **Collision check**: if the same `physio` basename is referenced by more than one row, **none** of those rows are converted — each is marked `COLLISION` and a `WARNING` lists every row referencing it. Clear all but one row's `physio` column and re-run to resolve.
4. Reads `PhysioParent` from `OUTPUT_DIR/PROJECT-<PROJECT>_mriconvert_qc.json`. For each remaining row, resolves the raw file as `PhysioParent/<physio>`; a missing `PhysioParent` or basename not found under it is reported as `SOURCE_MISSING` for that row (and does not block other rows).
5. **Validates that each match is really physiological data** by loading it with the matching `phys2bids` loader — not just trusting the extension. A file that fails to load (e.g. a stray `.txt` that is not a recording) is reported as `NOT_PHYSIO`.
6. Runs the `phys2bids` workflow into a temporary directory for **every** remaining row — `physioconvert` always reconverts from the raw source, every run, regardless of whether that association was converted before — then places its `.tsv.gz`/`.json` output(s) directly at `OUTPUT_DIR/PROJECT/<participant_id>/[<session_id>/]<datatype>/`, next to the associated `.nii.gz`, named from the row's `rename` (if set) or `bids_name` with its trailing suffix token replaced by `physio` — e.g. `bids_name` `task-rest_bold` → `task-rest_physio`, so the pair becomes `..._task-rest_physio.tsv.gz`/`.json`. Any `echo-<N>` entity is dropped from that name first, since a physio recording captures the whole run and applies to every echo of a multi-echo scan, not just the one row it was associated with — e.g. `bids_name` `task-rest_echo-1_bold` → `task-rest_physio`. A leftover output from an earlier run of the *same* association at that same computed path is replaced; a destination already written by a *different* association **this run** is never overwritten — that row is instead marked `CONVERT_ERROR` with a detail message. Editing a row's `physio` value to a *different* raw file converts the new one fresh; if the row's naming changed since a prior run (e.g. `rename`, `participant_id`, `session_id`, `datatype`), the old, differently-named output is left in place as an orphan (not auto-relocated or deleted — review and remove it by hand).
7. `phys2bids` automatically splits a recording whose channels have different sampling frequencies into one output file per frequency; each such file is given a `recording-<label>` entity (the `<freq>Hz` suffix `phys2bids` assigns) inserted just before `physio`.
8. Regenerates the per-row metrics every run: `n_channels` (channel count, including the time channel), `sampling_frequencies` (unique channel frequencies in Hz, ascending), `sample_count` (samples per frequency), and `duration_seconds` (acquisition length in seconds at 0.001 s precision, `sample_count / sampling_frequency`). These are comma-separated and aligned position-by-position, so a recording split across frequencies reports one entry per frequency in each. A blocked/failed row keeps blank metrics.
9. Writes `OUTPUT_DIR/PROJECT-<PROJECT>_physioconvert_qc.tsv`, fully regenerated from scratch every run — it is a visual reference for an expert reviewer, not a store consulted by a later run. One row per in-scope association, columns `physio`, `status`, `n_channels`, `sampling_frequencies`, `sample_count`, `duration_seconds`, sorted by `physio` (the sole reference/key column — no `filename`, `output_files`, or `bids_name`). A `COLLISION` group (same `physio` referenced by multiple `mriconvert_qc.tsv` rows) contributes a single row for that `physio` value.
10. Writes the static data dictionary [`src/assets/physioconvert_qc.json`](https://github.com/nimh-dsst/xnat-bids-cli/blob/main/src/assets/physioconvert_qc.json) as `OUTPUT_DIR/PROJECT-<PROJECT>_physioconvert_qc.json`, describing every column, injecting a top-level `PhysioParent` key (`{"Description": ..., "Value": "<path or empty>"}`) — the same `PhysioParent` value read from `mriconvert_qc.json` for this run — always the first key in the file.

```bash
# Serial
xnatcli physioconvert -o BIDS_DIR -p MYPROJ

# 4 conversions in parallel
xnatcli physioconvert -o BIDS_DIR -p MYPROJ -n 4
```

With `-n/--nphysio` > 1, the `phys2bids` conversions run in parallel across separate **processes** (real parallelism, since `phys2bids` is an in-process Python library rather than an external command). The conversions run in workers, but all placement, `physioconvert_qc.tsv`, and the log are written **serially in the main process**, drained in **sorted-filename order** (out-of-order completions are buffered until their turn) — so results are fully deterministic regardless of `-n`.

## Per-association STATUS (and exit code)

| STATUS | Meaning |
| --- | --- |
| `CONVERTED` | `phys2bids` read the raw file and its `_physio.tsv.gz`/`.json` were written next to the paired `.nii.gz`. |
| `NOT_PHYSIO` | The referenced file matched a supported extension but could not be loaded as physiological data. |
| `READER_MISSING` | The optional reader package `phys2bids` needs for this format is not installed (e.g. `bioread` for `.acq`, `scipy` for `.mat`, `sonpy` for `.smr`). This is an environment problem, not a data problem — install the package and re-run. |
| `CONVERT_ERROR` | `phys2bids` raised while converting, produced no `.tsv.gz` output, or its destination was already occupied by a different association. |
| `SOURCE_MISSING` | `PhysioParent` was unset/not a directory, the named basename was not found under it, or the `mriconvert_qc.tsv` row is missing `participant_id`/`datatype`. |
| `COLLISION` | This `physio` basename is referenced by more than one `mriconvert_qc.tsv` row; none were converted until resolved. |

Exit code is `1` if any association is `CONVERT_ERROR` or `READER_MISSING`, and `0` otherwise.

| Argument | Description |
| --- | --- |
| `-o`, `--output` | **Required.** Same BIDS root `xnatcli mriconvert` wrote to (`OUTPUT_DIR` must hold `PROJECT-<PROJECT>_mriconvert_qc.tsv`/`PROJECT-<PROJECT>_mriconvert_qc.json`). Physio outputs are written directly into `OUTPUT_DIR/PROJECT/<participant_id>/[<session_id>/]<datatype>/`, alongside the associated `.nii.gz`. |
| `-p`, `--project` | **Required.** Project directory name under `OUTPUT_DIR` identifying the BIDS dataset produced by `xnatcli mriconvert`. |
| `-n`, `--nphysio` | *Optional.* Number of physio conversions to run in parallel, one `phys2bids` conversion per process (default `1`). |
| `-l`, `--log` | *Optional.* Write a per-association log CSV to `OUTPUT_DIR/log/physioconvert_<YYYYMMDD_HHMMSS>_log.csv` (header `DATESTAMP,STATUS,MRI_FILENAME,PHYSIO_SOURCE,DESTINATION_PATH`). One row per processed association, except a conversion that produced several outputs emits one row per output; associations with no output get a single blank-`DESTINATION_PATH` row. Also mirrors everything printed to stdout/stderr into a companion text log at `OUTPUT_DIR/log/physioconvert_<YYYYMMDD_HHMMSS>_log.txt`, the Python equivalent of piping through `tee`. Off by default. |

> **Note:** `phys2bids` 2.10.0 caps `numpy` at `<1.24`, which conflicts with `nibabel`'s `numpy>=1.25` requirement. Because `phys2bids` does not actually use any `numpy` APIs removed in 1.24+, `pyproject.toml` carries a `[tool.uv] override-dependencies = ["numpy>=1.25,<2"]` so the whole stack shares one `numpy` (held on the 1.x series, which predates `numpy` 2.0).
