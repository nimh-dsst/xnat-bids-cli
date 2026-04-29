import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")
_DIRECTION_CODES = ("AP", "PA", "RL", "LR", "SI", "IS")
_DIRECTION_RE = re.compile(
    rf"(?<![A-Za-z])({'|'.join(_DIRECTION_CODES)})(?![A-Za-z])"
)


def _detect_direction(identity: str) -> str | None:
    """Last letter-bordered direction code in identity, or None."""
    matches = _DIRECTION_RE.findall(identity)
    return matches[-1] if matches else None


def _mask_direction(identity: str, code: str) -> str:
    """Drop all letter-bordered occurrences of code from identity."""
    pattern = re.compile(rf"(?<![A-Za-z]){re.escape(code)}(?![A-Za-z])")
    return pattern.sub("", identity)


def _require_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        sys.exit(
            f"Error: required tool '{name}' was not found on PATH. "
            "Install it and try again."
        )
    return path


def _parse_bids_guess_suffix(suffix_str: str) -> tuple[tuple[str, ...], str]:
    """Split BidsGuess[1] like '_task-rest_bold' into (entities, suffix).

    Returns (("task-rest",), "bold"). For '_T1w' returns ((), "T1w").
    """
    parts = suffix_str.lstrip("_").split("_")
    return tuple(parts[:-1]), parts[-1]


def _common_prefix(strings: list[str]) -> str:
    if not strings:
        return ""
    s_min = min(strings)
    s_max = max(strings)
    i = 0
    while i < len(s_min) and i < len(s_max) and s_min[i] == s_max[i]:
        i += 1
    return s_min[:i]


def _common_suffix(strings: list[str]) -> str:
    return _common_prefix([s[::-1] for s in strings])[::-1]


def _disambiguation_labels(values: list[str]) -> list[str]:
    """One BIDS-safe acq label per value; all guaranteed unique and non-empty.

    Tries minimal-diff (strip longest common prefix and suffix, sanitize to
    [A-Za-z0-9]). Falls back to fully sanitized values if any minimal label is
    empty or duplicates another. Final safety net appends an index when even
    the fallback is empty or non-unique.
    """
    prefix = _common_prefix(values)
    suffix = _common_suffix(values)

    minimal: list[str] = []
    for v in values:
        if len(prefix) + len(suffix) <= len(v):
            core = v[len(prefix): len(v) - len(suffix)] if suffix else v[len(prefix):]
        else:
            core = ""
        minimal.append(_NON_ALNUM.sub("", core))
    if all(minimal) and len(set(minimal)) == len(minimal):
        return minimal

    full = [_NON_ALNUM.sub("", v) for v in values]
    if all(full) and len(set(full)) == len(full):
        return full

    seen: dict[str, int] = {}
    out: list[str] = []
    for f in full:
        base = f or "acq"
        seen[base] = seen.get(base, 0) + 1
        out.append(f"{base}{seen[base]}")
    return out


def _identity_for_sidecar(
    jf_name: str, data: dict
) -> tuple[str, dict[str, str]]:
    """Return (identity_value, criteria_dict) for one helper sidecar.

    identity_value drives the acq-label LCP/LCS comparison. criteria_dict is
    written verbatim into the dcm2bids description's `criteria` field.
    Priority: SeriesDescription > ProtocolName > SidecarFilename.
    """
    sd = data.get("SeriesDescription")
    pn = data.get("ProtocolName")
    sd = sd.strip() if isinstance(sd, str) else ""
    pn = pn.strip() if isinstance(pn, str) else ""
    if sd:
        return sd, {"SeriesDescription": sd}
    if pn:
        return pn, {"ProtocolName": pn}
    return Path(jf_name).stem, {"SidecarFilename": jf_name}


def _draft_config(target: Path) -> None:
    helper_dir = target / "tmp_dcm2bids" / "helper"
    config_path = target / "dcm2bids_config.json"

    if config_path.exists():
        sys.exit(
            f"Error: {config_path} already exists; refusing to overwrite."
        )
    if not helper_dir.is_dir():
        print(f"Helper output directory not found: {helper_dir}; skipping config draft.")
        return

    json_files = sorted(helper_dir.glob("*.json"))
    if not json_files:
        print(f"No JSON sidecars found in {helper_dir}; no config drafted.")
        return

    # Outer group: (datatype, entities, suffix). Inner sub-group: unique
    # identity (criteria field + value) within that group, mapped to the raw
    # identity string used for acq-label disambiguation.
    groups: dict[
        tuple[str, tuple[str, ...], str],
        dict[tuple[str, str], str],
    ] = {}

    for jf in json_files:
        try:
            data = json.loads(jf.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: skipping {jf.name}: cannot read JSON ({e}).")
            continue

        guess = data.get("BidsGuess")
        if (
            not guess
            or not isinstance(guess, list)
            or len(guess) != 2
            or not guess[0]
            or not guess[1]
        ):
            print(
                f"Warning: skipping {jf.name}: missing or empty BidsGuess."
            )
            continue

        datatype, suffix_str = guess[0], guess[1]
        entities, suffix = _parse_bids_guess_suffix(suffix_str)
        # dcm2bids assigns run/echo numbering automatically. acq- from
        # BidsGuess is a protocol-name shorthand (e.g., "acq-epfid2p3") that
        # collides with the SeriesDescription-based acq label this draft
        # adds for disambiguation; drop it here so we never emit two acq-.
        entities = tuple(
            e for e in entities
            if not (
                e.startswith("run-")
                or e.startswith("echo-")
                or e.startswith("acq-")
            )
        )
        if not suffix:
            print(
                f"Warning: skipping {jf.name}: cannot parse suffix from "
                f"BidsGuess {guess!r}."
            )
            continue

        identity, criteria = _identity_for_sidecar(jf.name, data)
        ckey = next(iter(criteria.items()))
        groups.setdefault((datatype, entities, suffix), {}).setdefault(
            ckey, identity
        )

    if not groups:
        print("No usable BidsGuess entries; no config drafted.")
        return

    descriptions: list[dict] = []
    for (datatype, entities, suffix), sub in groups.items():
        items = list(sub.items())  # [((field, value), identity), ...]
        if len(items) == 1:
            (field, value), _identity = items[0]
            desc: dict = {"datatype": datatype, "suffix": suffix}
            if entities:
                desc["custom_entities"] = list(entities)
            desc["criteria"] = {field: value}
            descriptions.append(desc)
            continue

        # Multi-identity slot. Try to refine via direction codes.
        guess_dir = next(
            (e[len("dir-"):] for e in entities if e.startswith("dir-")),
            None,
        )
        slot = (
            f"{datatype}/"
            f"{('_'.join(entities) + '_') if entities else ''}{suffix}"
        )

        if guess_dir is not None:
            # BidsGuess already pinned a direction. Don't promote; flag
            # any identity whose detected direction disagrees with it.
            adjusted: list[tuple[tuple[str, str], str]] = []
            for ckey, identity in items:
                detected = _detect_direction(identity)
                if detected and detected != guess_dir:
                    print(
                        f"WARNING: identity {identity!r} contains direction "
                        f"code {detected!r} but BidsGuess for slot {slot} "
                        f"says dir-{guess_dir}. Leaving identity unchanged."
                    )
                    adjusted.append((ckey, identity))
                elif detected == guess_dir:
                    adjusted.append((ckey, _mask_direction(identity, detected)))
                else:
                    adjusted.append((ckey, identity))
            labels = _disambiguation_labels([i for _, i in adjusted])
            for ((field, value), _ident), label in zip(adjusted, labels):
                descriptions.append({
                    "datatype": datatype,
                    "suffix": suffix,
                    "custom_entities": list(entities) + [f"acq-{label}"],
                    "criteria": {field: value},
                })
            continue

        # No dir in BidsGuess. Bucket identities by detected direction code.
        by_dir: dict[str | None, list[tuple[tuple[str, str], str]]] = {}
        for ckey, identity in items:
            detected = _detect_direction(identity)
            by_dir.setdefault(detected, []).append((ckey, identity))

        for code, members in by_dir.items():
            if code is not None:
                masked = [_mask_direction(i, code) for _, i in members]
                bucket_entities = list(entities) + [f"dir-{code}"]
            else:
                masked = [i for _, i in members]
                bucket_entities = list(entities)

            if len(members) == 1:
                (field, value), _identity = members[0]
                desc = {"datatype": datatype, "suffix": suffix}
                if bucket_entities:
                    desc["custom_entities"] = bucket_entities
                desc["criteria"] = {field: value}
                descriptions.append(desc)
            else:
                labels = _disambiguation_labels(masked)
                # Place acq- before dir- to follow BIDS canonical ordering.
                base_entities = list(entities)
                tail = [f"dir-{code}"] if code is not None else []
                for ((field, value), _ident), label in zip(members, labels):
                    descriptions.append({
                        "datatype": datatype,
                        "suffix": suffix,
                        "custom_entities": base_entities
                        + [f"acq-{label}"]
                        + tail,
                        "criteria": {field: value},
                    })

    config_path.write_text(
        json.dumps({"descriptions": descriptions}, indent=2) + "\n"
    )
    print(
        f"Drafted dcm2bids config at {config_path} "
        f"({len(descriptions)} description(s))."
    )


def bidsprep_cmd(args: argparse.Namespace) -> int:
    experiment_dir = Path(args.experiment_dir).resolve()
    if not experiment_dir.is_dir():
        sys.exit(f"Error: input is not a directory: {experiment_dir}")

    scans_dir = experiment_dir / "scans"
    if not scans_dir.is_dir():
        sys.exit(
            f"Error: expected 'scans/' subdirectory under {experiment_dir}"
        )

    # Layout: <...>/PROJECT/SUBJECT/EXPERIMENT
    project_dir = experiment_dir.parent.parent
    project = project_dir.name
    if not project or project_dir == experiment_dir:
        sys.exit(
            "Error: could not derive PROJECT from two directories above "
            f"{experiment_dir}"
        )

    helper = _require_tool("dcm2bids_helper")
    _require_tool("dcm2niix")

    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"PROJECT-{project}_bidsprep"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    cmd = [helper, "-d", str(scans_dir), "-o", str(target)]
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        sys.exit(
            f"Error: dcm2bids_helper exited with code {result.returncode}."
        )
    print(f"Helper output written to {target}")

    _draft_config(target)
    return 0
