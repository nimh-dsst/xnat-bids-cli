import argparse
import csv
import json
import os
import shutil
import sys
from pathlib import Path

import pandas as pd

PARTICIPANT_COLS = ["participant_id", "participant_rename"]
SESSION_COLS = ["session_id", "session_rename"]

# Directories skipped when walking the source BIDS tree during copy-with-rename.
_SKIP_DIRS = {"tmp_dcm2bids", "tmp_phys2bids"}

# Extensions treated as imaging sidecars that share a stem with a .nii.gz and
# receive the same bids_name substitution when that .nii.gz is renamed.
_SIDECAR_EXTS = (".json", ".bval", ".bvec")

# QC filter: boolean columns where "FALSE" means exclude from copy.
_EXCLUDE_IF_FALSE = ("recommend_for_use", "complete", "usable")

# QC filter: qc_rating values that exclude a file from copy.
_EXCLUDE_QC_RATINGS = {"FAIL", "UNCERTAIN"}

# Columns dropped from the output scans.tsv produced by mrimap -o.
# rename: has been applied; task/acquisition/echo/run/suffix: redundant with filename.
_SCANS_DROP_COLS = frozenset({"rename", "task", "acquisition", "echo", "run", "suffix"})


def _scan_pairs(
    bids_dir: Path,
) -> tuple[list[dict[str, str]], bool, list[str]]:
    """Walk a BIDS dataset for sub-*/ses-* directories.

    Returns (rows, has_sessions, skipped). ``rows`` is a list of dicts with
    ``participant_id`` (and ``session_id`` when the dataset uses sessions).
    ``has_sessions`` is True when at least one participant has a ``ses-*``
    subdirectory; in that case participants without any session directory are
    omitted and listed in ``skipped``.
    """
    participants = sorted(
        p.name for p in bids_dir.iterdir() if p.is_dir() and p.name.startswith("sub-")
    )

    sessions_by_participant: dict[str, list[str]] = {}
    for participant in participants:
        sessions_by_participant[participant] = sorted(
            s.name
            for s in (bids_dir / participant).iterdir()
            if s.is_dir() and s.name.startswith("ses-")
        )

    has_sessions = any(sessions_by_participant.values())

    rows: list[dict[str, str]] = []
    skipped: list[str] = []
    if has_sessions:
        for participant in participants:
            sessions = sessions_by_participant[participant]
            if not sessions:
                skipped.append(participant)
                continue
            for session in sessions:
                rows.append({"participant_id": participant, "session_id": session})
    else:
        for participant in participants:
            rows.append({"participant_id": participant})

    return rows, has_sessions, skipped


def _blank_map(rows: list[dict[str, str]], has_sessions: bool) -> pd.DataFrame:
    columns = list(PARTICIPANT_COLS)
    key_cols = ["participant_id"]
    if has_sessions:
        columns = PARTICIPANT_COLS + SESSION_COLS
        key_cols = ["participant_id", "session_id"]

    fresh = pd.DataFrame(rows, columns=columns).fillna("")
    fresh = fresh.sort_values(key_cols, ignore_index=True)
    return fresh


def _merge_existing(
    fresh: pd.DataFrame, existing_path: Path
) -> tuple[pd.DataFrame, int]:
    existing = pd.read_csv(existing_path, sep="\t", dtype=str).fillna("")

    # Key columns are whichever of the fresh key columns the existing file
    # also has; this keeps the merge robust if the dataset gained or lost
    # sessions since the file was first generated.
    key_cols = [c for c in ("participant_id", "session_id") if c in fresh.columns]
    shared_keys = [c for c in key_cols if c in existing.columns]
    if not shared_keys:
        sys.exit(
            f"Error: existing map file {existing_path} has no participant_id "
            "column to merge on. Move or delete it and re-run."
        )

    existing_keys = set(map(tuple, existing[shared_keys].to_numpy()))
    fresh_key_tuples = fresh[shared_keys].apply(tuple, axis=1)
    new_rows = fresh[~fresh_key_tuples.isin(existing_keys)]

    merged = pd.concat([existing, new_rows], ignore_index=True).fillna("")
    merged = merged.sort_values(key_cols, ignore_index=True)
    return merged, len(new_rows)


def _load_participant_session_map(
    map_path: Path,
) -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    """Read PROJECT-<P>_mrimap.tsv and return (participant_map, session_map).

    ``participant_map`` is ``{sub_old: sub_new}`` for rows where
    ``participant_rename`` is non-empty.  ``session_map`` is
    ``{(sub_old, ses_old): ses_new}`` for rows where ``session_rename`` is
    non-empty.  Blank rename columns are treated as "keep the original label".
    """
    participant_map: dict[str, str] = {}
    session_map: dict[tuple[str, str], str] = {}

    try:
        with map_path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                sub_old = (row.get("participant_id") or "").strip()
                sub_new = (row.get("participant_rename") or "").strip()
                ses_old = (row.get("session_id") or "").strip()
                ses_new = (row.get("session_rename") or "").strip()
                if sub_old and sub_new:
                    participant_map[sub_old] = sub_new
                if sub_old and ses_old and ses_new:
                    session_map[(sub_old, ses_old)] = ses_new
    except OSError as exc:
        sys.exit(f"Error reading map file {map_path}: {exc}")

    return participant_map, session_map


def _load_rename_map(scans_tsv: Path) -> dict[str, str]:
    """Read scans.tsv and return ``{rel_posix_path: new_bids_name}`` for rows
    with a non-empty ``rename`` column.  Returns an empty dict if the file does
    not exist or cannot be read.
    """
    rename_map: dict[str, str] = {}
    if not scans_tsv.is_file():
        return rename_map
    try:
        with scans_tsv.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                filename = (row.get("filename") or "").strip()
                rename = (row.get("rename") or "").strip()
                if filename and rename:
                    rename_map[filename] = rename
    except OSError as exc:
        print(f"WARNING: could not read {scans_tsv}: {exc}", file=sys.stderr)
    return rename_map


def _load_scans_levels(scans_json: Path) -> dict[str, set[str]]:
    """Read scans.json and return ``{column: {valid_level, ...}}`` for columns
    that define a ``Levels`` dict.  Returns an empty dict if the file is absent
    or unreadable.
    """
    if not scans_json.is_file():
        return {}
    try:
        with scans_json.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        col: set(info["Levels"].keys())
        for col, info in data.items()
        if isinstance(info, dict) and "Levels" in info
    }


def _load_exclusion_info(
    scans_tsv: Path,
    scans_json: Path,
) -> tuple[set[str], list[str]]:
    """Read scans.tsv and return ``(excluded, warnings)``.

    ``excluded`` is the set of relative POSIX paths whose row triggers a QC
    exclusion: ``recommend_for_use``, ``complete``, or ``usable`` == ``"FALSE"``
    (exact, case-sensitive), or ``qc_rating`` in ``{"FAIL", "UNCERTAIN"}``.

    ``warnings`` contains:
    - One notice per excluded file listing all triggered criteria.
    - One notice per non-empty cell whose value is not a valid Level for that
      column (case mismatch or typo), since such entries are silently ignored
      by the exclusion check.
    """
    excluded: set[str] = set()
    warnings: list[str] = []

    levels = _load_scans_levels(scans_json)

    if not scans_tsv.is_file():
        return excluded, warnings

    try:
        with scans_tsv.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f, delimiter="\t"))
    except OSError as exc:
        warnings.append(f"WARNING: could not read {scans_tsv}: {exc}")
        return excluded, warnings

    for row in rows:
        filename = (row.get("filename") or "").strip()

        # Warn about non-empty values that do not match any valid Level.
        for col, valid_vals in levels.items():
            val = (row.get(col) or "").strip()
            if val and val not in valid_vals:
                warnings.append(
                    f"WARNING: {filename}: column {col!r} has unrecognized value "
                    f"{val!r} (valid Levels: {sorted(valid_vals)}); "
                    "this entry will be ignored for QC filtering."
                )

        if not filename:
            continue

        # Collect all triggered exclusion criteria for this row.
        reasons: list[str] = []
        for col in _EXCLUDE_IF_FALSE:
            if (row.get(col) or "").strip() == "FALSE":
                reasons.append(f"{col}=FALSE")
        qc = (row.get("qc_rating") or "").strip()
        if qc in _EXCLUDE_QC_RATINGS:
            reasons.append(f"qc_rating={qc!r}")

        if reasons:
            excluded.add(filename)
            warnings.append(
                f"WARNING: {filename} excluded from copy "
                f"({', '.join(reasons)})."
            )

    return excluded, warnings


def _build_excluded_stems(excluded: set[str]) -> set[tuple[str, str]]:
    """Convert ``{nii_rel_posix}`` to ``{(parent_dir_posix, old_stem)}``.

    Allows sidecar files to be excluded by the same key as their .nii.gz.
    """
    result: set[tuple[str, str]] = set()
    for nii_rel in excluded:
        if nii_rel.endswith(".nii.gz"):
            p = Path(nii_rel)
            result.add((p.parent.as_posix(), p.name[:-7]))
    return result


def _build_stem_rename(
    rename_map: dict[str, str],
) -> dict[tuple[str, str], str]:
    """Pivot ``{nii_rel_posix: new_bids_name}`` to
    ``{(parent_dir_posix, old_stem): new_bids_name}``.

    The pivot lets sidecar files (.json, .bval, .bvec) be looked up by the
    same key as their .nii.gz sibling without reconstructing the .nii.gz path.
    """
    stem_rename: dict[tuple[str, str], str] = {}
    for nii_rel, new_bids_name in rename_map.items():
        p = Path(nii_rel)
        stem_rename[(p.parent.as_posix(), p.name[:-7])] = new_bids_name
    return stem_rename


def _new_filename(
    filename: str,
    sub_old: str | None,
    ses_old: str | None,
    sub_new: str | None,
    ses_new: str | None,
    stem_rename: dict[tuple[str, str], str],
    rel_parent_posix: str,
) -> str:
    """Return the renamed filename for a single file.

    Applies participant/session label substitution, then overrides the
    ``bids_name`` portion for .nii.gz files and their sidecars when
    ``stem_rename`` has an entry for the file.
    """
    if filename.endswith(".nii.gz"):
        old_stem, ext = filename[:-7], ".nii.gz"
    else:
        ext = next((e for e in _SIDECAR_EXTS if filename.endswith(e)), None)
        if ext is not None:
            old_stem = filename[: -len(ext)]
        else:
            # Non-sidecar file: apply sub/ses label substitution only.
            result = filename
            if sub_old and sub_new and sub_old != sub_new:
                result = result.replace(sub_old, sub_new)
            if ses_old and ses_new and ses_old != ses_new:
                result = result.replace(ses_old, ses_new)
            return result

    # Check for a bids_name override from the rename column in scans.tsv.
    if (rel_parent_posix, old_stem) in stem_rename:
        new_bids_name = stem_rename[(rel_parent_posix, old_stem)]
        if sub_new and ses_new:
            prefix = f"{sub_new}_{ses_new}_"
        elif sub_new:
            prefix = f"{sub_new}_"
        else:
            prefix = ""
        return prefix + new_bids_name + ext

    # No bids_name override: substitute sub/ses labels in the stem only.
    new_stem = old_stem
    if sub_old and sub_new and sub_old != sub_new:
        new_stem = new_stem.replace(sub_old, sub_new)
    if ses_old and ses_new and ses_old != ses_new:
        new_stem = new_stem.replace(ses_old, ses_new)
    return new_stem + ext


def _build_copy_plan(
    source_root: Path,
    participant_map: dict[str, str],
    session_map: dict[tuple[str, str], str],
    rename_map: dict[str, str],
    excluded_stems: set[tuple[str, str]],
) -> list[tuple[Path, str]]:
    """Walk source_root and return ``[(src_abs_path, dest_rel_posix)]``.

    Does not touch the filesystem beyond reading directory entries.  Skips
    ``tmp_dcm2bids`` and ``tmp_phys2bids`` directories, and omits any
    .nii.gz (and its sidecars) whose stem is in ``excluded_stems``.
    """
    stem_rename = _build_stem_rename(rename_map)
    plan: list[tuple[Path, str]] = []

    for dirpath_str, dirnames, filenames in os.walk(source_root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)

        dirpath = Path(dirpath_str)
        rel_dir = dirpath.relative_to(source_root)
        parts = rel_dir.parts  # e.g. ("sub-A", "ses-X", "anat") or ()

        sub_old = next((p for p in parts if p.startswith("sub-")), None)
        ses_old = next((p for p in parts if p.startswith("ses-")), None)
        sub_new = participant_map.get(sub_old, sub_old) if sub_old else None
        ses_new = (
            session_map.get((sub_old, ses_old), ses_old)
            if (sub_old and ses_old)
            else ses_old
        )

        new_parts: list[str] = []
        for part in parts:
            if part == sub_old and sub_new and sub_new != sub_old:
                new_parts.append(sub_new)
            elif part == ses_old and ses_new and ses_new != ses_old:
                new_parts.append(ses_new)
            else:
                new_parts.append(part)

        new_rel_dir = Path(*new_parts) if new_parts else Path(".")
        rel_parent_posix = rel_dir.as_posix()

        for filename in filenames:
            # Derive the check stem: non-None only for .nii.gz and sidecar files.
            if filename.endswith(".nii.gz"):
                check_stem: str | None = filename[:-7]
            else:
                ext = next((e for e in _SIDECAR_EXTS if filename.endswith(e)), None)
                check_stem = filename[: -len(ext)] if ext else None

            if check_stem is not None and (rel_parent_posix, check_stem) in excluded_stems:
                continue

            new_fname = _new_filename(
                filename, sub_old, ses_old, sub_new, ses_new,
                stem_rename, rel_parent_posix,
            )
            dest_rel = (
                new_fname
                if new_rel_dir == Path(".")
                else (new_rel_dir / new_fname).as_posix()
            )
            plan.append((dirpath / filename, dest_rel))

    return plan


def _existing_session_dirs(dest_bids: Path) -> set[str]:
    """Relative posix keys (``sub-X`` or ``sub-X/ses-Y``) for every session
    directory already present under ``dest_bids`` before this run.

    Used to detect when a copy plan is about to add files into a BIDS
    session that was already mapped by a previous run, so that can be
    flagged with a WARNING instead of happening silently.
    """
    keys: set[str] = set()
    if not dest_bids.is_dir():
        return keys
    for sub_dir in dest_bids.iterdir():
        if not sub_dir.is_dir() or not sub_dir.name.startswith("sub-"):
            continue
        ses_dirs = [
            d for d in sub_dir.iterdir() if d.is_dir() and d.name.startswith("ses-")
        ]
        if ses_dirs:
            keys.update(f"{sub_dir.name}/{ses_dir.name}" for ses_dir in ses_dirs)
        else:
            keys.add(sub_dir.name)
    return keys


def _session_key(dest_rel: str) -> str | None:
    """``sub-X`` or ``sub-X/ses-Y`` prefix of a plan destination path, or
    ``None`` for a root-level file (e.g. ``scans.tsv``)."""
    parts = dest_rel.split("/")
    if not parts or not parts[0].startswith("sub-"):
        return None
    if len(parts) > 1 and parts[1].startswith("ses-"):
        return f"{parts[0]}/{parts[1]}"
    return parts[0]


def _partition_plan_for_incremental(
    plan: list[tuple[Path, str]],
    dest_root: Path,
) -> tuple[list[tuple[Path, str]], list[tuple[Path, str]]]:
    """Split a copy plan into ``(to_copy, already_mapped)``.

    A file under ``sub-*/`` whose destination already exists on disk is
    treated as already mapped by a previous run and left untouched. Root-level
    BIDS metadata files (``scans.tsv``, ``participants.tsv``,
    ``dataset_description.json``, ...) are always re-copied, since they
    reflect the fully merged state already maintained on the source side
    (e.g. by ``mriconvert``), and are patched in place afterward.
    """
    to_copy: list[tuple[Path, str]] = []
    already_mapped: list[tuple[Path, str]] = []
    for src, dest_rel in plan:
        is_root_level = "/" not in dest_rel
        if not is_root_level and (dest_root / dest_rel).exists():
            already_mapped.append((src, dest_rel))
        else:
            to_copy.append((src, dest_rel))
    return to_copy, already_mapped


def _check_collisions(
    plan: list[tuple[Path, str]],
) -> tuple[list[tuple[Path, str]], list[str]]:
    """Detect destination collisions in the copy plan.

    Returns ``(clean_plan, warnings)`` where ``clean_plan`` excludes any entry
    whose destination is shared by more than one source, and ``warnings``
    names each collision.  Colliding files are omitted entirely rather than
    having one silently win.
    """
    dest_to_sources: dict[str, list[Path]] = {}
    for src, dest_rel in plan:
        dest_to_sources.setdefault(dest_rel, []).append(src)

    colliding: set[str] = {d for d, srcs in dest_to_sources.items() if len(srcs) > 1}

    warnings: list[str] = []
    for dest_rel in sorted(colliding):
        srcs_str = ", ".join(str(s) for s in dest_to_sources[dest_rel])
        warnings.append(
            f"WARNING: destination collision — {srcs_str} all map to "
            f"{dest_rel!r}; none will be copied."
        )

    clean_plan = [(src, d) for src, d in plan if d not in colliding]
    return clean_plan, warnings


def _execute_copy_plan(
    plan: list[tuple[Path, str]],
    dest_root: Path,
) -> list[str]:
    """Copy each (src, dest_rel) into dest_root, creating directories as needed.

    Returns a list of warning strings for any files that fail to copy.
    """
    warnings: list[str] = []
    for src, dest_rel in plan:
        dest = dest_root / dest_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, dest)
        except OSError as exc:
            warnings.append(f"WARNING: could not copy {src} to {dest}: {exc}")
    return warnings


def _update_participants_tsv(
    dest_participants: Path,
    participant_map: dict[str, str],
) -> None:
    """Rewrite participant_id values in the output participants.tsv."""
    if not dest_participants.is_file() or not participant_map:
        return
    try:
        with dest_participants.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)
    except OSError as exc:
        print(f"WARNING: could not read {dest_participants}: {exc}", file=sys.stderr)
        return
    if "participant_id" not in fieldnames:
        return
    for row in rows:
        old_id = row.get("participant_id", "")
        row["participant_id"] = participant_map.get(old_id, old_id)
    try:
        with dest_participants.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(rows)
    except OSError as exc:
        print(f"WARNING: could not update {dest_participants}: {exc}", file=sys.stderr)


def _update_output_scans_tsv(
    dest_scans: Path,
    participant_map: dict[str, str],
    session_map: dict[tuple[str, str], str],
    rename_map: dict[str, str],
    excluded: set[str],
) -> None:
    """Patch the copied root-level scans.tsv in-place.

    Updates ``filename``, ``bids_name``, ``participant_id``, and ``session_id``
    to their renamed values, omits rows for files in ``excluded`` (those files
    were not copied), and drops the columns in ``_SCANS_DROP_COLS`` from the
    output.  All other reviewer columns are preserved.
    """
    if not dest_scans.is_file():
        return
    try:
        with dest_scans.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)
    except OSError:
        return

    stem_rename = _build_stem_rename(rename_map)

    out_rows: list[dict] = []
    for row in rows:
        old_filename = (row.get("filename") or "").strip()
        if not old_filename or old_filename in excluded:
            continue

        old_path = Path(old_filename)
        parts = old_path.parts
        sub_old = next((p for p in parts if p.startswith("sub-")), None)
        ses_old = next((p for p in parts if p.startswith("ses-")), None)
        sub_new = participant_map.get(sub_old, sub_old) if sub_old else sub_old
        ses_new = (
            session_map.get((sub_old, ses_old), ses_old)
            if (sub_old and ses_old)
            else ses_old
        )

        old_file = old_path.name
        old_stem = old_file[:-7] if old_file.endswith(".nii.gz") else old_file
        old_parent_posix = old_path.parent.as_posix()

        new_dir_parts: list[str] = []
        for part in parts[:-1]:
            if part == sub_old and sub_new and sub_new != sub_old:
                new_dir_parts.append(sub_new)
            elif part == ses_old and ses_new and ses_new != ses_old:
                new_dir_parts.append(ses_new)
            else:
                new_dir_parts.append(part)

        if (old_parent_posix, old_stem) in stem_rename:
            new_bids_name = stem_rename[(old_parent_posix, old_stem)]
            if sub_new and ses_new:
                prefix = f"{sub_new}_{ses_new}_"
            elif sub_new:
                prefix = f"{sub_new}_"
            else:
                prefix = ""
            new_file = prefix + new_bids_name + ".nii.gz"
            if "bids_name" in row:
                row["bids_name"] = new_bids_name
        else:
            new_file = old_file
            if sub_old and sub_new and sub_old != sub_new:
                new_file = new_file.replace(sub_old, sub_new)
            if ses_old and ses_new and ses_old != ses_new:
                new_file = new_file.replace(ses_old, ses_new)

        row["filename"] = "/".join(new_dir_parts + [new_file])
        if "participant_id" in row and sub_new:
            row["participant_id"] = sub_new
        if "session_id" in row and ses_new:
            row["session_id"] = ses_new
        out_rows.append(row)

    out_fieldnames = [f for f in fieldnames if f not in _SCANS_DROP_COLS]
    try:
        with dest_scans.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=out_fieldnames, delimiter="\t", extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(out_rows)
    except OSError as exc:
        print(f"WARNING: could not update {dest_scans}: {exc}", file=sys.stderr)


def mrimap_cmd(args: argparse.Namespace) -> int:
    input_root = Path(args.input).resolve()
    if not input_root.is_dir():
        sys.exit(f"Error: input directory not found: {input_root}")

    project = args.project
    bids_dir = input_root / project
    if not bids_dir.is_dir():
        sys.exit(f"Error: BIDS dataset for project {project!r} not found at {bids_dir}")

    # --- Step 1: always generate/update the map TSV ---
    rows, has_sessions, skipped = _scan_pairs(bids_dir)
    if not rows:
        sys.exit(f"Error: no sub-* directories found in {bids_dir}; nothing to map.")
    if skipped:
        print(
            "Warning: the dataset uses sessions but these participants have no "
            f"ses-* subdirectory and were skipped: {', '.join(skipped)}",
            file=sys.stderr,
        )

    fresh = _blank_map(rows, has_sessions)

    map_path = input_root / f"PROJECT-{project}_mrimap.tsv"
    if map_path.exists():
        merged, added = _merge_existing(fresh, map_path)
        merged.to_csv(map_path, sep="\t", index=False, na_rep="")
        print(
            f"Updated {map_path} ({added} new row{'s' if added != 1 else ''} "
            f"added, {len(merged)} total)."
        )
    else:
        fresh.to_csv(map_path, sep="\t", index=False, na_rep="")
        print(f"Wrote {map_path} ({len(fresh)} rows).")

    if not getattr(args, "output", None):
        return 0

    # --- Step 2: copy-with-rename to the output directory ---
    output_root = Path(args.output).resolve()
    dest_bids = output_root / project
    existing_sessions = _existing_session_dirs(dest_bids)

    participant_map, session_map = _load_participant_session_map(map_path)
    rename_map = _load_rename_map(bids_dir / "scans.tsv")

    all_warnings: list[str] = []

    # QC filter: determine which files to exclude before building the copy plan.
    excluded, qc_warnings = _load_exclusion_info(
        bids_dir / "scans.tsv", bids_dir / "scans.json",
    )
    all_warnings.extend(qc_warnings)
    for w in qc_warnings:
        print(w)

    excluded_stems = _build_excluded_stems(excluded)

    plan = _build_copy_plan(
        bids_dir, participant_map, session_map, rename_map, excluded_stems,
    )
    plan, collision_warnings = _check_collisions(plan)
    all_warnings.extend(collision_warnings)
    for w in collision_warnings:
        print(w)

    to_copy, already_mapped = _partition_plan_for_incremental(plan, dest_bids)

    # Loudly flag any BIDS session that already exists in the output and is
    # about to receive additional, previously-unmapped files.
    touched_existing_sessions = sorted(
        {
            key
            for _, dest_rel in to_copy
            if (key := _session_key(dest_rel)) is not None
            and key in existing_sessions
        }
    )
    session_warnings = [
        f"WARNING: BIDS session {dest_bids / key} already exists; mapping in "
        f"{sum(1 for _, d in to_copy if _session_key(d) == key)} new file(s) "
        "to it."
        for key in touched_existing_sessions
    ]
    all_warnings.extend(session_warnings)
    for w in session_warnings:
        print(w)

    copy_warnings = _execute_copy_plan(to_copy, dest_bids)
    all_warnings.extend(copy_warnings)
    for w in copy_warnings:
        print(w)

    _update_output_scans_tsv(
        dest_bids / "scans.tsv", participant_map, session_map, rename_map, excluded,
    )
    _update_participants_tsv(dest_bids / "participants.tsv", participant_map)

    print(
        f"Copied {len(to_copy)} file(s) to {dest_bids} "
        f"({len(already_mapped)} file(s) already mapped and left untouched, "
        f"{len(participant_map)} participant rename(s), "
        f"{len(session_map)} session rename(s), "
        f"{len(rename_map)} file rename(s), "
        f"{len(excluded)} file(s) excluded by QC filter)."
    )

    if all_warnings:
        sep = "=" * 60
        print(f"\n{sep}")
        print(f"  {len(all_warnings)} WARNING(s) from mrimap -o:")
        print(sep)
        for w in all_warnings:
            print(f"  {w}")
        print(sep)

    return 0
