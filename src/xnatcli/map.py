import argparse
import sys
from pathlib import Path

import pandas as pd

PARTICIPANT_COLS = ["participant_id", "participant_rename"]
SESSION_COLS = ["session_id", "session_rename"]


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


def map_cmd(args: argparse.Namespace) -> int:
    input_root = Path(args.input).resolve()
    if not input_root.is_dir():
        sys.exit(f"Error: input directory not found: {input_root}")

    project = args.project
    bids_dir = input_root / project
    if not bids_dir.is_dir():
        sys.exit(f"Error: BIDS dataset for project {project!r} not found at {bids_dir}")

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

    out_path = input_root / f"PROJECT-{project}_map.tsv"
    if out_path.exists():
        merged, added = _merge_existing(fresh, out_path)
        merged.to_csv(out_path, sep="\t", index=False, na_rep="")
        print(
            f"Updated {out_path} ({added} new row{'s' if added != 1 else ''} "
            f"added, {len(merged)} total)."
        )
    else:
        fresh.to_csv(out_path, sep="\t", index=False, na_rep="")
        print(f"Wrote {out_path} ({len(fresh)} rows).")

    return 0
