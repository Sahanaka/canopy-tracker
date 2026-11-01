# 0003 — Python version

- **Status:** accepted
- **Date:** 2026-11-01
- **Task:** M0.1

## Context

rioxarray 0.23, rasterio 1.5 and numpy 2.5 require Python 3.12 or newer, so the earlier "Python 3.11+" in
CLAUDE.md could not be met with current releases. Python 3.15 is brand new on conda-forge.

## Decision

`environment.yml` pins `python=3.13.*`; `pyproject.toml` declares `requires-python = ">=3.12"`; CLAUDE.md Stack
now says "Python 3.12+". All pins were dry-run solved on linux-64, osx-64, osx-arm64 and win-64 with micromamba
and resolved to CPython 3.13.

## Consequences

Anyone on 3.12 can still install the package with pip; the reference environment is 3.13. Revisit when a pinned
dependency drops 3.13 or when moving to 3.14.
