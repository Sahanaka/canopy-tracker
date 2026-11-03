# 0003 — Python version

- **Status:** accepted
- **Date:** 2026-11-01
- **Task:** M0.1

## Context

rioxarray 0.23, rasterio 1.5 and numpy 2.5 require Python 3.12 or newer, so the earlier "Python 3.11+" in
CLAUDE.md could not be met with current releases. Python 3.15 is brand new on conda-forge.

## Decision

`environment.yml` pins `python=3.13.*`; `pyproject.toml` declares `requires-python = ">=3.12"`; CLAUDE.md Stack
now says "Python 3.12+". Python 3.12 is allowed by package metadata but untested; the reference environment is
3.13. The solve was checked on linux-64, osx-64, osx-arm64 and win-64 (all resolve to CPython 3.13.18) with:

```
for p in linux-64 osx-64 osx-arm64 win-64; do
  CONDA_OVERRIDE_OSX=13.0 micromamba create --dry-run -y -n t --platform $p -f environment.yml
done
```

`CONDA_OVERRIDE_OSX=13.0` supplies the macOS virtual package (`__osx`) that a non-macOS host lacks; it is not
needed on a real Mac. The linux-64 env was also actually created and the checks run in it.

## Consequences

The reference environment is 3.13. Revisit when a pinned
dependency drops 3.13 or when moving to 3.14.
