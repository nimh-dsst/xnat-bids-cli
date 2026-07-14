# `xnatcli cubids`

Runs [`CuBIDS`](https://cubids.readthedocs.io/) on a BIDS dataset produced by `xnatcli mriconvert` — first `cubids add-nifti-info` (which annotates JSON sidecars with NIfTI header fields) and then `cubids group` (which groups acquisitions by their parameters and writes `_summary.tsv`, `_files.tsv`, `_AcqGrouping.tsv`, and `_AcqGroupInfo.txt`).

1. Validates that `--input` is an existing directory and that `INPUT_DIR/<PROJECT>/` (the BIDS dataset) exists. The expected layout is the one produced by `xnatcli mriconvert` — `<input>/PROJECT/sub-X/ses-Y/...`.
2. Verifies that `cubids` is on `PATH`; exits with an error if it is missing.
3. Creates the output directory `INPUT_DIR/PROJECT-<PROJECT>_cubids/` if it does not already exist. If it does, the directory is reused (CuBIDS writes its own outputs into it).
4. If `INPUT_DIR/<PROJECT>/tmp_dcm2bids/` exists (leftover dcm2bids scratch), it is moved out to `INPUT_DIR/.<PROJECT>_cubids_stash_tmp_dcm2bids/` for the duration of the run so CuBIDS does not scan it, and moved back when the run finishes (success or failure). CuBIDS has no built-in ignore mechanism; it walks the whole BIDS tree.
5. Invokes `cubids add-nifti-info <bids_dir>` without `--use-datalad` (datalad is disabled by default in CuBIDS), so the BIDS dataset itself is mutated in place to add NIfTI header info to sidecars.
6. Invokes `cubids group <bids_dir> v0`, which writes `v0_summary.tsv`, `v0_files.tsv`, `v0_AcqGrouping.tsv`, and `v0_AcqGroupInfo.txt` into `INPUT_DIR/<PROJECT>/code/CuBIDS/`.
7. On a successful `group`, the `INPUT_DIR/<PROJECT>/code/CuBIDS/` directory is merged into `INPUT_DIR/PROJECT-<PROJECT>_cubids/CuBIDS/` (existing files with the same name are overwritten; unrelated files in the destination are left alone) and the source is removed. If `INPUT_DIR/<PROJECT>/code/` is empty afterwards, it is also removed.
8. If `add-nifti-info` exits non-zero, `group` is skipped and the command exits `1`. Otherwise the exit code is `0` if both steps succeeded and `1` if `group` failed.

```bash
xnatcli cubids -i BIDSCONVERT_OUTPUT_DIR -p PROJECT
```

For example, if you ran `xnatcli mriconvert -i DOWNLOAD_DIR -p MYPROJ -o /data/bids -c config.json`, the BIDS dataset lives at `/data/bids/MYPROJ/`, and `xnatcli cubids -i /data/bids -p MYPROJ` writes CuBIDS outputs to `/data/bids/PROJECT-MYPROJ_cubids/CuBIDS/v0_*.tsv`.

| Argument | Description |
| --- | --- |
| `-i`, `--input` | **Required.** Parent directory holding the BIDS dataset at `INPUT_DIR/PROJECT/` (i.e., the output of `xnatcli mriconvert`). The CuBIDS output subdirectory is created here. |
| `-p`, `--project` | **Required.** Project directory name under `INPUT_DIR` identifying the BIDS dataset to process. |
| `-l`, `--log` | *Optional.* Write a per-step log CSV to `INPUT_DIR/PROJECT-<PROJECT>_cubids/log/cubids_<YYYYMMDD_HHMMSS>_log.csv` with header `DATESTAMP,PROJECT,STEP,STATUS`. One row per CuBIDS step (`add-nifti-info`, `group`), each with `STATUS` of `COMPLETE` or `FAILURE`. |
