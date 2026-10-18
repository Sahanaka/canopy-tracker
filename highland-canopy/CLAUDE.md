# CLAUDE.md — Highland Canopy & Watershed

Read this whole file at the start of every session. It is the project's standing context.

## Mission

Open-source hobby project to support restoration of Sri Lanka's central highlands: converting
*Pinus caribaea* and *Syncarpia glomulifera* (turpentine) monocultures back to native montane
forest. The code turns free satellite data into maps that show:

- **Module A — Canopy change:** where pine canopy has been opened, and when (Milestone 1).
- **Module B — Canopy classes:** pine vs native broadleaf vs other, incl. *Alstonia macrophylla* as a stretch goal (Milestone 2).
- **Module C — Slope and watershed risk:** which plots are unsafe to open quickly (Milestone 3).

No client, no funding. Optimise for: correct science, reproducibility, and code a stranger can rerun.

The full plan lives in the owner's planning doc; `docs/ROADMAP.md` is the in-repo source of truth for tasks.

## Hard constraints

- **No Google Earth Engine / GCP.** All data comes from free STAC APIs:
  - Primary: Microsoft Planetary Computer — `https://planetarycomputer.microsoft.com/api/stac/v1`, signed with `planetary_computer.sign_inplace`.
  - Fallback (Sentinel-2 only): AWS Earth Search — `https://earth-search.aws.element84.com/v1`.
- Processing runs **locally** on a laptop. Keep memory bounded: lazy xarray/dask, clip to the AOI early, chunk sensibly.
- **Area of interest:** `data/aoi/aoi.geojson` (~10 × 10 km, Bandarawela–Haputale belt, Uva Province). Never hard-code coordinates elsewhere; load the AOI file.
- **Never invent data, numbers, thresholds or field observations.** If a value must be chosen (a threshold, a season window), derive it from data or record it as an assumption in `docs/decisions/`.

## Stack

Python 3.11+, `pystac-client`, `planetary-computer`, `odc-stac`, `xarray`, `rioxarray`, `dask`,
`geopandas`, `shapely`, `rasterio`, `numpy`, `scikit-learn` (Milestone 2), `matplotlib`, JupyterLab.
Tooling: `ruff` (lint + format), `pytest`, `nbstripout`, `pre-commit`. Environment in `environment.yml`.
3D: `pyvista` (+ `trame` for Jupyter) for notebook views; the web viewer is plain HTML/JS with MapLibre GL JS
and `pmtiles` loaded from a CDN at pinned versions, no build step. `rio-pmtiles` / `tippecanoe` if needed to
package layers. Playwright for the viewer smoke test (local only).

## Repo layout

```
src/highland/        # all real logic lives here (importable, tested)
  aoi.py             # load/validate AOI, reproject
  stac.py            # catalog search, signing, loading via odc-stac
  masking.py         # Sentinel-2 SCL and Landsat QA_PIXEL masks
  indices.py         # NDVI, EVI, NDMI, NBR
  composites.py      # seasonal / annual median composites
  landsat.py         # Collection 2 scaling, sensor harmonisation
  change.py          # disturbance detection
  terrain.py         # DEM derivatives, terrain-shadow flag
  landcover.py       # WorldCover mask
  io.py              # GeoTIFF / GeoJSON export
  viz3d.py           # PyVista drape of rasters over the DEM (notebook QA views)
  viewer.py          # builds viewer/data/ from outputs/ for the web viewer
viewer/              # static 3D web viewer (index.html, app.js, style.css, data/) → GitHub Pages
notebooks/           # thin: call src/highland, plot, explain. Numbered 01_, 02_ ...
tests/               # pytest; unit tests on small synthetic arrays
data/aoi/            # AOI polygon (committed)
data/field/          # QField exports (committed, small)
outputs/             # generated rasters/vectors (git-ignored except outputs/README.md)
docs/ROADMAP.md      # task list with acceptance criteria — agents tick these off
docs/decisions/      # short ADRs: NNNN-title.md (context, decision, consequences)
docs/plans/          # per-task implementation plans written by the planner agent
docs/prompts/        # reusable prompts for driving phases
```

## Conventions

- **CRS:** work in UTM zone 44N (`EPSG:32644`); store AOI in EPSG:4326. Resolution 10 m for Sentinel-2, 30 m for Landsat.
- Logic goes in `src/highland/`; notebooks only orchestrate and plot. A notebook must run top to bottom from a clean kernel.
- Every public function: type hints + a one-line docstring stating units and expected value ranges.
- Reflectance inside the package is always float32 in 0–1 after scaling. Indices are float32, NaN where masked.
- Unit tests must not hit the network. Tests that need the network are marked `@pytest.mark.network` and skipped in CI.
- Small commits on a branch per roadmap task (`task/M1.2-s2-composites`). Conventional commit messages.
- Outputs are written to `outputs/<task-id>/` with a short `README.md` saying how they were produced.

## Domain gotchas (check these every time)

- **Sentinel-2 offset:** Planetary Computer's `sentinel-2-l2a` is not harmonised. Scenes with processing baseline ≥ 04.00 (from 25 Jan 2022) carry a −1000 DN offset. Check `s2:processing_baseline` and correct before scaling (÷10000).
- **SCL masking:** keep vegetation/bare/water classes; mask no-data, saturated, cloud shadow, cloud medium/high probability, thin cirrus, snow. Treat class 2 (dark area pixels) with suspicion on steep slopes — log how much it removes.
- **Landsat Collection 2 L2:** SR = DN × 0.0000275 − 0.2. Mask with `QA_PIXEL` bits (dilated cloud, cloud, cloud shadow, snow). Landsat 7 has SLC-off stripes after May 2003. Harmonise L8/9 OLI against L5/7 TM/ETM+ before mixing in a time series; record the method in an ADR.
- **Indices:** NDVI = (NIR−Red)/(NIR+Red); EVI = 2.5·(NIR−Red)/(NIR + 6·Red − 7.5·Blue + 1) on 0–1 reflectance; NDMI = (NIR−SWIR1)/(NIR+SWIR1) (this is the "moisture NDWI" — never the green/NIR water NDWI); NBR = (NIR−SWIR2)/(NIR+SWIR2).
- **Clouds:** the central hills are cloudy for much of the year (SW monsoon May–Sep, NE monsoon Dec–Feb). Composite windows come from the availability audit (`notebooks/01_*`), not assumptions.
- **Terrain:** slopes here are steep. Shaded hillsides look less green; always carry a terrain-shadow / low-illumination flag (from the DEM + sun geometry) and report results with and without it.
- **Land cover:** tea, grassland and settlements are not forest change. Mask with ESA WorldCover before disturbance analysis.

## 3D visualisation rules

- **3D is for viewing, never for measuring.** Every number (area, slope, counts, accuracy) comes from the 2D outputs. The viewer and 3D notebook views say so.
- **State vertical exaggeration** on every 3D view and screenshot; default 1×. Never exaggerate silently.
- Hills hide what is behind them: any 3D figure used in a write-up is paired with the 2D map of the same layer.
- Drapes must share the DEM's grid: reproject/resample the layer to the DEM extent first (nearest for classes, bilinear for continuous values) and check alignment on a known feature (a ridge or road).
- The viewer is static: no server, no API keys, no tracking. Pin CDN library versions. Keep `viewer/` under 100 MB; layers as PMTiles (vectors) or Cloud-Optimised GeoTIFF / raster PMTiles (rasters).
- Colour scales: colour-blind-safe, with a legend on every layer; disturbance year uses a sequential scale.
- The viewer is only rebuilt from `outputs/` by `highland.viewer.build()`; never hand-edit `viewer/data/`.

## Agentic workflow

Use the subagents in `.claude/agents/` and the commands in `.claude/commands/`:

1. `/next-task [task-id]` — picks the next unchecked task in `docs/ROADMAP.md` and runs plan → approve → implement → verify → review → commit.
2. `/review-science [path]` — independent remote-sensing correctness review.
3. `/milestone-check M1` — checks a milestone's "done when" criteria against the repo.

Rules for the loop:
- The **planner** writes `docs/plans/<task-id>.md` and stops for human approval before any code is written.
- The **implementer** follows the approved plan; deviations go back to the plan file with a reason.
- The **verifier** runs ruff, pytest, and executes touched notebooks headless; it reports, never fixes.
- The **science-reviewer** checks scaling, masks, CRS, index formulas and thresholds against this file.
- At most two fix loops per task; then stop and summarise the blocker for the human.
- Never push to `main`, never force-push, never delete data in `data/`. Open a PR with `gh` when the task passes.

## Definition of done (every task)

- Acceptance criteria in `docs/ROADMAP.md` met and ticked.
- `ruff check`, `ruff format --check`, `pytest -m "not network"` pass.
- Touched notebooks execute cleanly from a fresh kernel (the verifier runs them to a scratch copy) and are committed stripped of outputs (`nbstripout`).
- Any new threshold, window or method choice has an ADR in `docs/decisions/`.
- PR description: what changed, how it was verified, open questions.
