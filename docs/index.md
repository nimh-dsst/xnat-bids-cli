# XNAT CLI for BIDS

A command-line interface for logging into an Extensible Neuroimaging Archive Toolkit (XNAT) server, querying experiments and downloading files, then converting to the Brain Imaging Data Structure (BIDS) standard format.

## Contents

The [`src/xnatcli/`](https://github.com/nimh-dsst/xnat-bids-cli/tree/main/src/xnatcli) directory is the installable package that provides the `xnatcli` CLI (`xnatcli login`, `xnatcli download`, `xnatcli query`, `xnatcli mriconfig`, `xnatcli mriconvert`, `xnatcli cubids`, `xnatcli bidsmap`, `xnatcli physioconvert`), built on [PyXNAT](https://pyxnat.github.io/pyxnat/index.html).

See [Installation](installation.md) to get set up, [Design](design.md) for how the package is organized, and the CLI Reference section for each subcommand.
