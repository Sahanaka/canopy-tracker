# 0004 — Environment pinning and dependency sources

- **Status:** accepted
- **Date:** 2026-11-01
- **Task:** M0.1

## Context

Results must be reproducible by a stranger. Options were exact pins on direct dependencies, or a full
conda-lock lockfile for four platforms.

## Decision

- `environment.yml` uses exact `==` pins on every direct dependency (conda-forge only, `nodefaults`).
  No lockfile yet.
- Transitive packages that decide pixel values or grid alignment are also pinned, at the versions the linux-64
  env resolved to: `odc-geo==0.5.3`, `odc-loader==0.6.5`, `pystac==1.15.2`, `pyproj==3.8.0`,
  `libgdal-core==3.13.3`, `proj==9.9.0`, `geos==3.14.1`. The four-platform dry-run solves still succeed.
- `hatchling==1.33.0` is in the env so the editable install also works offline:
  `pip install -e . --no-deps --no-build-isolation`. CI may keep `pip install -e . --no-deps`.
- Everything else (e.g. fsspec, aiohttp, jupyter internals, other libs) still floats.
- Reproducibility is enforced per output: every `outputs/<task-id>/README.md` must record the resolved
  environment (`micromamba env export --explicit` or `conda list --explicit`, saved alongside; at least the
  rasterio, GDAL, PROJ and odc-* versions). A move to conda-lock is triggered if a transitive upgrade changes
  a result.
- `[project].dependencies` in `pyproject.toml` is empty: the conda env is the single source of truth, and the
  package is installed with `pip install -e . --no-deps`.
- pre-commit runs ruff and nbstripout as `repo: local`, `language: system` hooks so they use the env's pinned
  binaries; generic checks come from upstream `pre-commit-hooks` v6.0.0.

## Consequences

Simple and readable; works with `setup-micromamba`. If a transitive upgrade ever changes a result, move to
conda-lock (`conda-lock.yml`) and record it in a new ADR. Pins age and must be bumped by hand. pre-commit
needs the `highland` env active, and network on first run to fetch `pre-commit-hooks`.
