# 0005 — Optional 3D / viewer environment

- **Status:** accepted
- **Date:** 2026-11-01
- **Task:** M0.1

## Context

pyvista (VTK), trame and Playwright are heavy and only needed for notebook 3D views and the viewer smoke test.
CI never uses them.

## Decision

They live in a separate `environment-3d.yml` (pyvista 0.49.1, trame 4.0.0, trame-vtk 2.11.17, trame-vuetify
3.3.0, pytest-playwright 0.10.0), applied on top of the main env with
`micromamba install -n highland -f environment-3d.yml`, then `playwright install chromium`. The combined solve
on linux-64 was checked. Modules `viz3d` and `viewer` must import these lazily, and viewer tests use
`pytest.importorskip("playwright")`.

## Consequences

CI stays light. M0.2 should run `pytest -m "not network and not viewer"`. Two files to keep in sync for shared
pins; none are shared today.
