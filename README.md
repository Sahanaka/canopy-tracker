# canopy-tracker

[![ci](https://github.com/Sahanaka/canopy-tracker/actions/workflows/ci.yml/badge.svg?branch=task%2FM0.0-kit-setup)](https://github.com/Sahanaka/canopy-tracker/actions/workflows/ci.yml)

Open-source hobby project supporting restoration of Sri Lanka's central highlands: converting *Pinus caribaea* and
*Syncarpia glomulifera* (turpentine) monocultures back to native montane forest. The code turns free satellite data
(Sentinel-2 and Landsat from STAC APIs, no Google Earth Engine) into maps of canopy change, canopy classes, and slope
and watershed risk. Everything runs locally on a laptop.

The badge shows CI on the integration branch `task/M0.0-kit-setup`.

## How to run

Create the pinned environment (Python 3.13, see `environment.yml`) and activate it. Only direct dependencies and the listed geospatial libraries are pinned; other transitive
dependencies float (there is no lockfile, see [ADR 0004](docs/decisions/0004-environment-pinning.md)). To record the
environment behind a result, follow the explicit-export rule in [`outputs/README.md`](outputs/README.md).

```bash
micromamba create -f environment.yml      # or: conda env create -f environment.yml
micromamba activate highland
pip install -e . --no-deps --no-build-isolation
```

Set up and run the checks:

```bash
pre-commit install                        # once per clone
pre-commit run --all-files                # ruff, nbstripout, YAML and whitespace checks
pytest -m "not network and not viewer"    # offline tests (what CI runs)
pytest -m network                         # tests that need the network (none yet)
```

Optional 3D notebook views and the viewer smoke test (see ADR 0005):

```bash
micromamba install -n highland -f environment-3d.yml
playwright install chromium
pytest -m viewer                          # none yet; local only
```

## Where things are

- `CLAUDE.md`: project context, conventions and domain gotchas.
- `docs/ROADMAP.md`: tasks with acceptance criteria.
- `docs/decisions/`: short ADRs for every method or threshold choice.
- `src/highland/`: all real logic; `tests/`: unit tests on synthetic data.
