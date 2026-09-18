# Installation

The project uses [uv](https://docs.astral.sh/uv/) and Python ≥ 3.11. Runtime dependencies: `pyxnat`, `dcm2bids`, `dcm2niix` (the [`dcm2niix`](https://pypi.org/project/dcm2niix/) PyPI package vendors the binary onto your `PATH`), `pydicom`, `cubids`, `nibabel` (used to read NIfTI shapes for the `mriconvert` `scans.tsv`), `pandas` (used by `bidsmap`), and `phys2bids` (used by `physioconvert` to read physiological recordings and write BIDS physio files; see the note under [`xnatbidscli physioconvert`](cli/physioconvert.md) about its `numpy` pin). `bioread` is also pulled in for `phys2bids` to read BIOPAC `.acq` files (phys2bids imports it lazily but does not depend on it directly).

```bash
uv sync
uv pip install -e .
```

The second command makes the `xnatbidscli` command available on your `PATH` inside the project's virtualenv. Activate the venv (`source .venv/bin/activate` on Unix, `.venv\Scripts\activate` on Windows) to use `xnatbidscli` directly, or invoke it with `uv run xnatbidscli …`.

## Upgrading from `xnatcli`

The package and command were renamed from `xnatcli` to `xnatbidscli`. Pull the latest changes and re-run `uv sync` (or `uv pip install -e .`) to replace the old `xnatcli` command with `xnatbidscli`. Saved credentials at `~/.xnatcli/credentials.cfg` are migrated automatically to `~/.xnatbidscli/credentials.cfg` the first time any `xnatbidscli` command needs them — see [`xnatbidscli login`](cli/login.md).
