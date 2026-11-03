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

## Note (M1.3, 2026-11-03)

- **Separate env for the 3D layer.** Applying `environment-3d.yml` on top of `highland` changes the main environment, so M1.3 solved both files together into a second env with
  `micromamba create -n highland-3d -f environment.yml -f environment-3d.yml` (the same pins; the solve succeeded: vtk-base 9.7.1, pyvista 0.49.1). `highland` is untouched and its
  `environment.explicit.txt` still describes every M1.2 and M1.3 output except the 3D PNGs; `outputs/M1.3/environment-3d.explicit.txt` describes the PNG renderer. `highland` is not installed
  in that env: put `src` on `PYTHONPATH` or `pip install -e . --no-deps`.
- **Off-screen PNGs need a display.** In this env (a headless Linux box) `pv.Plotter(off_screen=True).screenshot()` fails: `pv.Report()` shows "Render Window: error"; VTK tries X (no DISPLAY),
  then EGL ("Could not initialize a device") and then OSMesa ("libOSMesa not found"). conda-forge has no OSMesa build of VTK on linux-64 (`micromamba search "*osmesa*"` finds nothing), so a pin is not possible.
  Rendering under `xvfb-run -a` (system package, GLX software rendering, `vtkXOpenGLRenderWindow`) works. M1.3's notebook was executed that way:
  `xvfb-run -a jupyter nbconvert --to notebook --execute notebooks/03_terrain.ipynb ...`. On a desktop with a display no wrapper is needed. Building the plotter without rendering (the unit test) works without a display.
