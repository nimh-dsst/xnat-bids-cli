# XNAT CLI for BIDS

`xnatcli` is a command-line toolkit that takes neuroimaging data from login to analysis-ready dataset: authenticate against an XNAT (Extensible Neuroimaging Archive Toolkit) server, query and download experiments, convert them to BIDS (Brain Imaging Data Structure) with `dcm2bids`, group acquisitions with CuBIDS, map participants/sessions to anonymized IDs, and fold in physiological recordings — all through one CLI built on [PyXNAT](https://pyxnat.github.io/pyxnat/index.html).

## User Guide

Full documentation for every `xnatcli` subcommand, the on-disk layouts each one produces, and how the pieces fit together lives at [xnatbidscli.readthedocs.io](https://xnatbidscli.readthedocs.io/).

## Prerequisites

- [uv](https://docs.astral.sh/uv/) — manages the Python environment and dependencies.
- [Git](https://git-scm.com/) — to clone this repository.

## Installation

```bash
uv sync
```

See the [Installation guide](https://xnatbidscli.readthedocs.io/installation/) for making the `xnatcli` command available on `PATH` and the full list of runtime dependencies.

## Attribution

Developed by the [NIMH Data Science and Sharing Team](https://github.com/nimh-dsst).

## License

Distributed under the MIT License.
