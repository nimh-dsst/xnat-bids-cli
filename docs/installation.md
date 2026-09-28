# Installation

`xnatbidscli` requires Python ≥ 3.11. Install the latest release from [PyPI](https://pypi.org/project/xnatbidscli/):

```bash
pip install xnatbidscli
```

This puts the `xnatbidscli` command on your `PATH`.

Runtime dependencies (installed automatically): `pyxnat`, `dcm2bids`, `dcm2niix` (the [`dcm2niix`](https://pypi.org/project/dcm2niix/) PyPI package vendors the binary onto your `PATH`), `pydicom`, `cubids`, `nibabel` (used to read NIfTI shapes for the `mriconvert` `scans.tsv`), `pandas` (used by `bidsmap`), and `phys2bids` (used by `physioconvert` to read physiological recordings and write BIDS physio files; see the note under [`xnatbidscli physioconvert`](cli/physioconvert.md) about its `numpy` pin). `bioread` is also pulled in for `phys2bids` to read BIOPAC `.acq` files (phys2bids imports it lazily but does not depend on it directly).

## Latest unreleased features

To use features on `main` that are not yet released, install from source with [uv](https://docs.astral.sh/uv/) and [Git](https://git-scm.com/):

```bash
git clone https://github.com/nimh-dsst/xnat-bids-cli.git
cd xnat-bids-cli
uv sync
```

`uv sync` installs the package into the project's `.venv`. Activate it (`source .venv/bin/activate` on Unix, `.venv\Scripts\activate` on Windows) to use `xnatbidscli` directly, or prefix commands with `uv run`. Run `git pull` and `uv sync` again to update.

## Upgrading from `xnatcli`

The package and command were renamed from `xnatcli` to `xnatbidscli`. Run `pip install xnatbidscli` (or, from source, pull the latest changes and re-run `uv sync`) to replace the old `xnatcli` command with `xnatbidscli`. Saved credentials at `~/.xnatcli/credentials.cfg` are migrated automatically to `~/.xnatbidscli/credentials.cfg` the first time any `xnatbidscli` command needs them — see [`xnatbidscli login`](cli/login.md).
