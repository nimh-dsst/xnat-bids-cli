# Design

The package is organized as:

- [`src/xnatcli/cli.py`](https://github.com/nimh-dsst/xnat-bids-cli/blob/main/src/xnatcli/cli.py) — argparse setup and subcommand dispatch.
- [`src/xnatcli/login.py`](https://github.com/nimh-dsst/xnat-bids-cli/blob/main/src/xnatcli/login.py) — interactive credential capture, verification, and on-disk storage; also exposes `load_credentials` for the other subcommands.
- [`src/xnatcli/download.py`](https://github.com/nimh-dsst/xnat-bids-cli/blob/main/src/xnatcli/download.py) — experiment download.
- [`src/xnatcli/query.py`](https://github.com/nimh-dsst/xnat-bids-cli/blob/main/src/xnatcli/query.py) — CSV listing of (project, subject, experiment) triplets.
- [`src/xnatcli/mriconfig.py`](https://github.com/nimh-dsst/xnat-bids-cli/blob/main/src/xnatcli/mriconfig.py) — runs `dcm2bids_helper` against a downloaded experiment directory.
- [`src/xnatcli/mriconvert.py`](https://github.com/nimh-dsst/xnat-bids-cli/blob/main/src/xnatcli/mriconvert.py) — converts downloaded XNAT sessions to BIDS via `dcm2bids`.
- [`src/xnatcli/cubids.py`](https://github.com/nimh-dsst/xnat-bids-cli/blob/main/src/xnatcli/cubids.py) — runs [`CuBIDS`](https://cubids.readthedocs.io/) `add-nifti-info` and `group` on a BIDS dataset.
- [`src/xnatcli/bidsmap.py`](https://github.com/nimh-dsst/xnat-bids-cli/blob/main/src/xnatcli/bidsmap.py) — generates/updates a participant/session mapping TSV for a `mriconvert`-produced BIDS dataset and, with `-o`, applies renames by copying the dataset to a new tree (uses [`pandas`](https://pandas.pydata.org/)).
- [`src/xnatcli/physioconvert.py`](https://github.com/nimh-dsst/xnat-bids-cli/blob/main/src/xnatcli/physioconvert.py) — converts physio recordings associated (via `mriconvert`'s `mriconvert_qc.tsv` `physio` column) with a BIDS dataset, placing them directly alongside their paired scan (uses [`phys2bids`](https://phys2bids.readthedocs.io/)).

Credentials live in `~/.xnatcli/credentials.cfg`, a plain-text [configparser](https://docs.python.org/3/library/configparser.html) file with a single `[xnatcli]` section storing `server`, `username`, and `password`. The file is created via `os.open` with mode `0o600` and re-`chmod`-ed to `0o600` after writing so only the owner can read or write it. (On Windows, `os.chmod` only toggles the read-only bit — the permissions model there is ACL-based; the `0o600` call still runs for portability.)
