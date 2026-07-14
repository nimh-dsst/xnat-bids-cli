import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

STATUS_COMPLETE = "COMPLETE"
STATUS_FAILURE = "FAILURE"

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")
_DIRECTION_CODES = ("AP", "PA", "RL", "LR", "SI", "IS")
_DIRECTION_RE = re.compile(
    rf"(?<![A-Za-z])({'|'.join(_DIRECTION_CODES)})(?![A-Za-z])"
)

_print_lock = threading.Lock()


def _safe_print(msg: str) -> None:
    with _print_lock:
        print(msg)


def _logging_now() -> str:
    now = datetime.now()
    return f"{now.strftime('%Y-%m-%d %H:%M:%S')},{now.microsecond // 1000:03d}"


class _LogWriter:
    def __init__(self, path: Path | None):
        self._path = path
        self._lock = threading.Lock()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", newline="") as f:
                csv.writer(f).writerow(
                    [
                        "DATESTAMP",
                        "PROJECT",
                        "SUBJECT",
                        "EXPERIMENT",
                        "STATUS",
                    ]
                )

    def write(
        self,
        datestamp: str,
        project: str,
        subject: str,
        experiment: str,
        status: str,
    ) -> None:
        if self._path is None:
            return
        with self._lock, self._path.open("a", newline="") as f:
            csv.writer(f).writerow(
                [datestamp, project, subject, experiment, status]
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
    helper_root = target / "tmp_dcm2bids" / "helper"

    if not helper_root.is_dir():
        print(f"Helper output directory not found: {helper_root}; skipping config draft.")
        return

    # With dcm2bids_helper -n <EXPERIMENT>, sidecars live at
    # <helper_root>/<EXPERIMENT>/*.json. Aggregate across every nested
    # experiment subdirectory so the drafted config covers the whole project.
    json_files = sorted(helper_root.rglob("*.json"))
    if not json_files:
        print(f"No JSON sidecars found under {helper_root}; no config drafted.")
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

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    config_path = target / f"dcm2bids_config_{ts}.json"

    config_path.write_text(
        json.dumps({"descriptions": descriptions}, indent=2) + "\n"
    )
    print(
        f"Drafted dcm2bids config at {config_path} "
        f"({len(descriptions)} description(s))."
    )


def _draft_blank_config(target: Path) -> None:
    """Draft a bare-bones dcm2bids config with one entry per unique identity.

    Each description has blank datatype/suffix/custom_entities and a single
    criteria field (SeriesDescription, falling back to ProtocolName or
    SidecarFilename per `_identity_for_sidecar`). Intended as a manually
    editable starting point rather than the BidsGuess-derived draft from
    `_draft_config`.
    """
    helper_root = target / "tmp_dcm2bids" / "helper"

    if not helper_root.is_dir():
        print(f"Helper output directory not found: {helper_root}; skipping blank config draft.")
        return

    json_files = sorted(helper_root.rglob("*.json"))
    if not json_files:
        print(f"No JSON sidecars found under {helper_root}; no blank config drafted.")
        return

    seen: dict[tuple[str, str], None] = {}
    for jf in json_files:
        try:
            data = json.loads(jf.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: skipping {jf.name}: cannot read JSON ({e}).")
            continue
        _identity, criteria = _identity_for_sidecar(jf.name, data)
        ckey = next(iter(criteria.items()))
        seen.setdefault(ckey, None)

    descriptions = [
        {
            "datatype": "",
            "suffix": "",
            "custom_entities": [],
            "criteria": {field: value},
        }
        for field, value in seen
    ]

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    config_path = target / f"dcm2bids_config_blank_{ts}.json"
    config_path.write_text(
        json.dumps({"descriptions": descriptions}, indent=2) + "\n"
    )
    print(
        f"Drafted blank dcm2bids config at {config_path} "
        f"({len(descriptions)} description(s))."
    )


def _discover_experiments(
    input_root: Path, args: argparse.Namespace
) -> list[tuple[str, str, str]]:
    if args.triplet is not None:
        p, s, e = args.triplet
        exp_dir = input_root / p / s / e
        if not exp_dir.is_dir():
            sys.exit(f"Error: experiment directory not found: {exp_dir}")
        return [(p, s, e)]

    if args.subject is not None:
        project, subject = args.subject
        subject_dir = input_root / project / subject
        if not subject_dir.is_dir():
            sys.exit(f"Error: subject directory not found: {subject_dir}")
        exps = [
            (project, subject, exp.name)
            for exp in sorted(subject_dir.iterdir())
            if exp.is_dir()
        ]
        if not exps:
            sys.exit(f"Error: no experiments found under {subject_dir}")
        return exps

    project = args.project
    project_dir = input_root / project
    if not project_dir.is_dir():
        sys.exit(f"Error: project directory not found: {project_dir}")
    exps: list[tuple[str, str, str]] = []
    for subject_dir in sorted(project_dir.iterdir()):
        if not subject_dir.is_dir():
            continue
        for exp_dir in sorted(subject_dir.iterdir()):
            if exp_dir.is_dir():
                exps.append((project, subject_dir.name, exp_dir.name))
    if not exps:
        sys.exit(f"Error: no experiments found under {project_dir}")
    return exps


def _delete_niftis(target: Path, experiment: str) -> int:
    """Remove *.nii.gz from this experiment's helper subdir; return count."""
    helper_subdir = target / "tmp_dcm2bids" / "helper" / experiment
    if not helper_subdir.is_dir():
        return 0
    count = 0
    for f in helper_subdir.glob("*.nii.gz"):
        try:
            f.unlink()
            count += 1
        except OSError:
            pass
    return count


def _run_helper(
    input_root: Path,
    project: str,
    subject: str,
    experiment: str,
    target: Path,
    helper_path: str,
) -> tuple[str, str | None]:
    exp_dir = input_root / project / subject / experiment
    scans_dir = exp_dir / "scans"
    if not scans_dir.is_dir():
        return STATUS_FAILURE, f"no 'scans/' subdirectory under {exp_dir}"

    cmd = [
        helper_path,
        "-d", str(scans_dir),
        "-o", str(target),
        "-n", experiment,
        "--force",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        stderr_tail = (result.stderr or "").strip().splitlines()[-1:] or [""]
        return STATUS_FAILURE, (
            f"dcm2bids_helper exited with code {result.returncode}"
            + (f" — {stderr_tail[0]}" if stderr_tail[0] else "")
        )
    return STATUS_COMPLETE, None


def mriconfig_cmd(args: argparse.Namespace) -> int:
    if args.nprep < 1:
        sys.exit("Error: -n/--nprep must be >= 1.")

    input_root = Path(args.input).resolve()
    if not input_root.is_dir():
        sys.exit(f"Error: input directory not found: {input_root}")

    experiments = _discover_experiments(input_root, args)
    project = experiments[0][0]

    output_dir = Path(args.output).resolve()
    target = output_dir / f"PROJECT-{project}_mriconfig"

    # --maps: skip running dcm2bids_helper and only (re)draft the dcm2bids config
    # from the helper JSON sidecars already present under <target>. The helper
    # and dcm2niix tools are unused on this path, so neither is required.
    if args.maps:
        if not target.is_dir():
            sys.exit(
                f"Error: mriconfig output directory not found: {target}; run "
                "mriconfig without -m/--maps first."
            )
        _draft_config(target)
        _draft_blank_config(target)
        return 0

    helper_path = _require_tool("dcm2bids_helper")
    _require_tool("dcm2niix")
    output_dir.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)

    log_path: Path | None = None
    if args.log:
        while True:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = output_dir / project / "log" / f"mriconfig_{ts}_log.csv"
            if not log_path.exists():
                break
            time.sleep(1)
    log_writer = _LogWriter(log_path)

    counts = {STATUS_COMPLETE: 0, STATUS_FAILURE: 0}

    def _one(triplet: tuple[str, str, str]) -> str:
        p, s, e = triplet
        start = _logging_now()
        status, detail = _run_helper(
            input_root, p, s, e, target, helper_path
        )
        deleted = _delete_niftis(target, e) if args.delete else 0
        line = f"{p}/{s}/{e}: {status}"
        if detail:
            line += f" — {detail}"
        if args.delete:
            line += f" (removed {deleted} .nii.gz)"
        _safe_print(line)
        log_writer.write(start, p, s, e, status)
        return status

    if args.nprep <= 1:
        for triplet in experiments:
            counts[_one(triplet)] += 1
    else:
        with ThreadPoolExecutor(max_workers=args.nprep) as ex:
            futures = [ex.submit(_one, t) for t in experiments]
            for fut in as_completed(futures):
                counts[fut.result()] += 1

    total = sum(counts.values())
    print(f"\nProcessed {total} experiment(s):")
    for status in (STATUS_COMPLETE, STATUS_FAILURE):
        print(f"  {status}: {counts[status]}")
    if log_path is not None:
        print(f"Log written to {log_path}")

    _draft_config(target)
    _draft_blank_config(target)

    return 0 if counts[STATUS_FAILURE] == 0 else 1
