# `xnatcli mriconfig`

Runs `dcm2bids_helper` (from [`Dcm2Bids`](https://unfmontreal.github.io/Dcm2Bids/)) on one or many downloaded XNAT experiment directories and drafts a project-level dcm2bids config. The input directory follows the layout produced by `xnatcli download` (`<input>/PROJECT/SUBJECT/EXPERIMENT/scans/...`).

1. Validates `--input` and verifies that both `dcm2bids_helper` and `dcm2niix` are on `PATH`.
2. Resolves the set of experiments to process from one of the mutually exclusive selectors:
   - `-1 PROJECT SUBJECT EXPERIMENT` — exactly one experiment.
   - `-s/--subject PROJECT SUBJECT` — every `EXPERIMENT` directory under that subject.
   - `-p/--project PROJECT` — every `EXPERIMENT` directory under every subject in the project.
3. Computes the target directory `OUTPUT_DIR/PROJECT-<PROJECT>_mriconfig/` and creates it if missing. Existing contents are kept; per-experiment helper runs overwrite their own nested subdirectory.
4. For each experiment, invokes `dcm2bids_helper -d <input>/PROJECT/SUBJECT/EXPERIMENT/scans -o <target> -n EXPERIMENT --force`. The `-n` flag nests each experiment's NIfTI/JSON outputs under `<target>/tmp_dcm2bids/helper/<EXPERIMENT>/` so runs do not collide; `--force` lets re-runs overwrite stale files within that subdirectory. Experiments are processed serially or in parallel (`-n/--nprep`, one helper invocation per experiment per worker).
5. After every experiment has been processed (regardless of individual success), drafts a project-level dcm2bids config at `<target>/dcm2bids_config_<YYYYMMDD_HHMMSS>.json` by aggregating sidecars from **every** `<target>/tmp_dcm2bids/helper/<EXPERIMENT>/*.json` present on disk (including prior runs). Each run gets its own timestamped file so older drafts are preserved:
   - For each sidecar with a `BidsGuess` field (set by recent `dcm2niix` releases), parses it into `datatype`, `custom_entities`, and `suffix` (e.g., `["func", "_task-rest_bold"]` → `datatype: func`, `custom_entities: ["task-rest"]`, `suffix: bold`). `run-*`, `echo-*`, and `acq-*` entities are stripped — dcm2bids assigns run/echo numbering automatically, and `acq-*` from BidsGuess (typically a protocol-name shorthand like `acq-epfid2p3`) is replaced by a SeriesDescription-derived `acq-<label>` only when needed for disambiguation.
   - One description is emitted per unique identity, where identity is the first non-empty of `SeriesDescription`, `ProtocolName`, or `SidecarFilename` (basename). The chosen field becomes the description's `criteria`. Multiple sidecars sharing one identity (e.g., multi-run / multi-echo series with the same `SeriesDescription`, including across experiments) collapse into a single description.
   - When two or more identities map to the same `(datatype, custom_entities, suffix)` slot, the disambiguator first looks for phase-encoding direction codes (`AP`, `PA`, `RL`, `LR`, `SI`, `IS`) inside each identity. A code only matches when bordered by non-letters (so `MAPS`, `ISIS`, `RAPID` are not false positives); the *last* such code in the identity wins.
     - Identities sharing a detected code (or none) get bucketed; each bucket gets a `dir-<code>` entity and emits its own description, with the matched code masked out before computing any further `acq-` label. If a bucket still has multiple identities after this, an `acq-<label>` is added — the minimal substring that distinguishes them (longest common prefix and suffix removed, sanitized to `[A-Za-z0-9]`); when minimal-diff fails to yield unique non-empty labels, the full sanitized identity is used instead.
     - If `BidsGuess` already contained a `dir-XX` for the slot, no promotion happens (the entity is already there); but if any identity contains a *different* direction code, a loud warning is printed flagging the inconsistency.
     - When a slot has only one identity, no `dir-` promotion happens — the rule is conflict-only.
   - Sidecars with missing or empty `BidsGuess` are skipped with a warning.
6. Alongside it, a second, separate config is always drafted at `<target>/dcm2bids_config_blank_<YYYYMMDD_HHMMSS>.json`: one description per unique identity (`SeriesDescription`, falling back to `ProtocolName` or `SidecarFilename`, same priority as above) with blank `datatype`, `suffix`, and `custom_entities`, and `criteria` set to the matched field. This is a minimal starting point meant for manual editing rather than the `BidsGuess`-derived draft above.

```bash
# One experiment
xnatcli mriconfig -i DOWNLOAD_DIR -1 PROJECT SUBJECT EXPERIMENT -o OUTPUT_DIR

# All experiments of one subject, 4 in parallel
xnatcli mriconfig -i DOWNLOAD_DIR -s PROJECT SUBJECT -o OUTPUT_DIR -n 4

# All experiments of all subjects in a project
xnatcli mriconfig -i DOWNLOAD_DIR -p PROJECT -o OUTPUT_DIR
```

Per-experiment helper output is uniformly nested under `<target>/tmp_dcm2bids/helper/<EXPERIMENT>/` regardless of which selector was used. Multiple `mriconfig` invocations against the same project accumulate: each run refreshes the helper subdir(s) it touches (via `--force`) and re-drafts the project-level config by aggregating across **all** nested helper subdirectories present on disk.

## Per-experiment helper STATUS (and exit code)

| STATUS | Meaning |
| --- | --- |
| `COMPLETE` | `dcm2bids_helper` exited 0. |
| `FAILURE` | `dcm2bids_helper` exited non-zero, or `<input>/PROJECT/SUBJECT/EXPERIMENT/scans/` does not exist. |

Exit code is `0` if every processed experiment is `COMPLETE`, and `1` otherwise. Both config drafts are attempted regardless.

With `-l/--log`, a CSV identical in shape to `download`'s and `mriconvert`'s logs (`DATESTAMP,PROJECT,SUBJECT,EXPERIMENT,STATUS`) is written to `OUTPUT_DIR/log/mriconfig_<YYYYMMDD_HHMMSS>_log.csv` (local time, captured at run start) — the same `log/` directory used by `mriconvert`. One row is appended per processed experiment; rows are written under a lock so concurrent workers do not interleave.

With `-d/--delete`, every `*.nii.gz` file in each experiment's helper subdir (`OUTPUT_DIR/PROJECT-<PROJECT>_mriconfig/tmp_dcm2bids/helper/<EXPERIMENT>/`) is removed right after `dcm2bids_helper` returns for that experiment, regardless of STATUS. JSON sidecars are kept — the project-level config draft only needs the JSONs, and the NIfTI images are typically far larger. The per-experiment status line gets a trailing `(removed N .nii.gz)` so the deletion is visible. Use this when you only need the drafted config and not the helper-stage NIfTIs.

| Argument | Description |
| --- | --- |
| `-i`, `--input` | **Required.** Root directory holding `PROJECT/SUBJECT/EXPERIMENT` subdirectories. |
| `-1 PROJECT SUBJECT EXPERIMENT` | Run helper on a single experiment. Mutually exclusive with `-s` and `-p`. Values must match the directory names under `INPUT_DIR`. |
| `-s`, `--subject PROJECT SUBJECT` | Run helper on every experiment of one subject. |
| `-p`, `--project PROJECT` | Run helper on every experiment of every subject in a project. |
| `-o`, `--output` | **Required.** Directory under which `PROJECT-<PROJECT>_mriconfig/` is created (the parent directory is created if missing). |
| `-n`, `--nprep` | *Optional.* Number of parallel dcm2bids_helper invocations, one per experiment per worker (default `1`). |
| `-l`, `--log` | *Optional.* Write a per-experiment log CSV to `OUTPUT_DIR/log/mriconfig_<YYYYMMDD_HHMMSS>_log.csv`. |
| `-d`, `--delete` | *Optional.* After each experiment's helper run, delete `*.nii.gz` from its `tmp_dcm2bids/helper/<EXPERIMENT>/` subdir. JSON sidecars are kept. |
| `-m`, `--maps` | *Optional.* Skip running `dcm2bids_helper` and only (re)draft the config from the helper JSON sidecars already under `OUTPUT_DIR/PROJECT-<PROJECT>_mriconfig/`. `dcm2bids_helper`/`dcm2niix` are not required. |
