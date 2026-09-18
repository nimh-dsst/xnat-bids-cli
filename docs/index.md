# XNAT CLI for BIDS

A command-line interface for logging into an Extensible Neuroimaging Archive Toolkit (XNAT) server, querying experiments and downloading files, then converting to the Brain Imaging Data Structure (BIDS) standard format.

## Contents

The [`src/xnatbidscli/`](https://github.com/nimh-dsst/xnat-bids-cli/tree/main/src/xnatbidscli) directory is the installable package that provides the `xnatbidscli` CLI (`xnatbidscli login`, `xnatbidscli download`, `xnatbidscli query`, `xnatbidscli mriconfig`, `xnatbidscli mriconvert`, `xnatbidscli cubids`, `xnatbidscli bidsmap`, `xnatbidscli physioconvert`), built on [PyXNAT](https://pyxnat.github.io/pyxnat/index.html).

See [Installation](installation.md) to get set up, [Design](design.md) for how the package is organized, and the CLI Reference section for each subcommand.
