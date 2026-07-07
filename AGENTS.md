# AGENTS

## Overview

This project is a command-line interface for logging into an Extensible Neuroimaging Archive Toolkit (XNAT) server, querying experiments and downloading files, then converting to the Brain Imaging Data Structure (BIDS) standard format.

## Always (for every prompt and follow-up)

- Always ask clarifying questions if a request is unclear or if there are distinctly different performance or behavior expectations among possible solutions.
- Always update the README.md when something changes in the code that affects the behaviors described in the README.md. But keep README changes concise so the README does not end up too long.
- Always make clear and concise comments and numpy docstrings inside the code. The code should be mostly self-explanatory, and comments should be used to clarify complex logic or design decisions.
- Always use "uv run" to run anything requiring the environment in this repository.

## Don't

- Don't assume the user wants one option over another without explicitly asking a clarifying question.
- Don't write code comments that are very verbose or redundant.
- Don't ever enter, read, or touch a data/ directory. A data/ directory is for user data only and should never be read or modified by the agent.
