# 0004 — Environment pinning and dependency sources

- **Status:** accepted
- **Date:** 2026-11-01
- **Task:** M0.1

## Context

Results must be reproducible by a stranger. Options were exact pins on direct dependencies, or a full
conda-lock lockfile for four platforms.

## Decision

- `environment.yml` uses exact `==` pins on every direct dependency (conda-forge only, `nodefaults`).
  No lockfile yet. Transitive packages (GDAL, PROJ, GEOS) still float.
- `[project].dependencies` in `pyproject.toml` is empty: the conda env is the single source of truth, and the
  package is installed with `pip install -e . --no-deps`.
- pre-commit runs ruff and nbstripout as `repo: local`, `language: system` hooks so they use the env's pinned
  binaries; generic checks come from upstream `pre-commit-hooks` v6.0.0.

## Consequences

Simple and readable; works with `setup-micromamba`. If a transitive upgrade ever changes a result, move to
conda-lock (`conda-lock.yml`) and record it in a new ADR. Pins age and must be bumped by hand. pre-commit
needs the `highland` env active, and network on first run to fetch `pre-commit-hooks`.
