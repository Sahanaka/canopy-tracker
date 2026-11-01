# CLAUDE.md — Highland Canopy & Watershed

Read this whole file at the start of every session. It is the project's standing context.

## Mission

Open-source hobby project to support restoration of Sri Lanka's central highlands: converting
*Pinus caribaea* and *Syncarpia glomulifera* (turpentine) monocultures back to native montane
forest. The code turns free satellite data into maps that show:

- **Module A — Canopy change:** where pine and turpentine canopy has been opened, and when (Milestone 1).
- **Module B — Canopy classes:** pine, turpentine (*Syncarpia*), eucalyptus plantation, native broadleaf and other
  (tea, grassland, settlement). *Alstonia macrophylla* is an **invasive alien** in Sri Lanka that colonises opened
  pine stands; mapping it as its own class is a stretch goal (Milestone 2).
- **Module C — Slope and watershed risk:** which plots are unsafe to open quickly (Milestone 3).

No client, no funding. Optimise for: correct science, reproducibility, and code a stranger can rerun.

The full plan lives in the owner's planning doc; `docs/ROADMAP.md` is the in-repo source of truth for tasks.

## Hard constraints

- **No Google Earth Engine / GCP.** All data comes from free STAC APIs:
  - Primary: Microsoft Planetary Computer — `https://planetarycomputer.microsoft.com/api/stac/v1`, signed with `planetary_computer.sign_inplace`.
  - Fallback (Sentinel-2 only): AWS Earth Search — `https://earth-search.aws.element84.com/v1`, collection
    `sentinel-2-c1-l2a`. Not a drop-in swap: no signing, different asset names (`red`, `nir`, `nir08`, `swir16`,
    `swir22`, `scl`, … instead of `B04`, `B08`, `B8A`, `B11`, `B12`, `SCL`), and scale/offset come from each
    asset's `raster:bands` metadata. `highland.stac` maps both catalogs to one internal band naming.
- Processing runs **locally** on a laptop. Keep memory bounded: lazy xarray/dask, clip to the AOI early, chunk sensibly.
- **Area of interest:** `data/aoi/aoi.geojson` (~10 × 10 km, Bandarawela–Haputale belt, Uva Province). Never hard-code coordinates elsewhere; load the AOI file.
- **Never invent data, numbers, thresholds or field observations.** If a value must be chosen (a threshold, a season window), derive it from data or record it as an assumption in `docs/decisions/`.

## Stack

Python 3.12+, `pystac-client`, `planetary-computer`, `odc-stac`, `xarray`, `rioxarray`, `dask`,
`geopandas`, `shapely`, `rasterio`, `numpy`, `scikit-learn` (Milestone 2), `matplotlib`, JupyterLab.
Tooling: `ruff` (lint + format), `pytest`, `nbstripout`, `pre-commit`. Environment in `environment.yml`.
3D: `pyvista` (+ `trame` for Jupyter) for notebook views; the web viewer is plain HTML/JS with MapLibre GL JS
and `pmtiles` loaded from a CDN at pinned versions, no build step. `rio-pmtiles` / `tippecanoe` if needed to
package layers. Playwright (`pytest-playwright`) for the viewer smoke test (local only).

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
  Browser tests for the viewer are marked `@pytest.mark.viewer` (needs `pytest-playwright`; local only). Markers are
  registered in `pyproject.toml` and pytest runs with `--strict-markers`.
- Small commits on a branch per roadmap task (`task/M1.2-s2-composites`). Conventional commit messages.
- Outputs are written to `outputs/<task-id>/` with a short `README.md` saying how they were produced.

## Domain gotchas (check these every time)

- **Sentinel-2 offset:** Planetary Computer's `sentinel-2-l2a` is not harmonised. Every item with
  `s2:processing_baseline` ≥ 04.00 carries a −1000 DN offset (BOA_ADD_OFFSET): reflectance = (DN − 1000) / 10000.
  Decide **by baseline only, never by date**: ESA's Collection-1 reprocessing (baselines 05.00/05.1x) also covers
  scenes from 2015–2021. DN 0 is no-data; small negative reflectance after the offset is real data, so don't clip
  it away silently.
- **Sentinel-2 duplicates:** the Planetary Computer holds more than one item for some acquisitions (e.g. an
  original 04.00 item and a reprocessed 05.10 item with the same date and MGRS tile). Deduplicate on
  (acquisition datetime, `s2:mgrs_tile`), keeping the highest baseline, before counting scenes or compositing.
  The constellation includes Sentinel-2A, 2B and 2C.
- **SCL masking:** classes are 0 no-data, 1 saturated/defective, 2 cast (topographic) shadow, 3 cloud shadow,
  4 vegetation, 5 not vegetated, 6 water, 7 unclassified, 8 cloud medium prob., 9 cloud high prob., 10 thin cirrus,
  11 snow/ice. Keep 4, 5, 6; mask 0, 1, 3, 8, 9, 10, 11. Class 2 marks topographic shadow (formerly "dark area
  pixels"; renamed in baseline 05.11), so on these slopes it is a terrain-illumination flag, not a cloud: masking it
  removes shaded hillsides wholesale. Class 7 is undecided. Both are configurable, the share of AOI pixels each
  removes is logged, and the choice goes in an ADR (M1.1/M1.2). SCL is 20 m: resample it to 10 m with nearest only.
- **Landsat Collection 2 L2:** SR = DN × 0.0000275 − 0.2 (L4–9). Mask with `QA_PIXEL` bits 0 fill, 1 dilated cloud,
  3 cloud, 4 cloud shadow, 5 snow; bit 2 (cirrus) is meaningful only for Landsat 8/9. Verify bit layouts per sensor
  against the USGS L4–7 and L8–9 Collection 2 Level-2 product guides. Planetary Computer asset names: `blue`,
  `green`, `red`, `nir08`, `swir16`, `swir22`, `qa_pixel`. Landsat 7 has SLC-off stripes after 31 May 2003.
  Harmonise L8/9 OLI against L5/7 TM/ETM+ before mixing in a time series; record the method in an ADR.
- **Indices:** NDVI = (NIR−Red)/(NIR+Red); EVI = 2.5·(NIR−Red)/(NIR + 6·Red − 7.5·Blue + 1) on 0–1 reflectance; NDMI = (NIR−SWIR1)/(NIR+SWIR1) (this is the "moisture NDWI" — never the green/NIR water NDWI); NBR = (NIR−SWIR2)/(NIR+SWIR2).
  Sentinel-2 band mapping: Blue B02, Red B04, SWIR1 B11, SWIR2 B12; NIR is **B08** (10 m) for NDVI/EVI and
  **B8A** (narrow NIR, 20 m) for NDMI/NBR, so NIR and SWIR share a footprint and the indices are closer to Landsat.
  20 m reflectance bands go to the 10 m grid with bilinear resampling. Deviating from this mapping needs an ADR.
- **Clouds:** the central hills are cloudy for much of the year. Sri Lanka's Met Department has four seasons:
  first inter-monsoon (Mar–Apr), SW monsoon (May–Sep), second inter-monsoon (Oct–Nov, often the wettest) and NE
  monsoon (Dec–Feb). Uva, on the eastern side of the hills, takes much of its rain in Oct–Feb. Composite windows
  come from the availability audit (`notebooks/01_*`), not assumptions.
- **Terrain:** slopes here are steep. Shaded hillsides look less green; always carry a terrain-shadow / low-illumination flag (from the DEM + sun geometry) and report results with and without it.
  Sun geometry is per scene (Sentinel-2 `s2:mean_solar_azimuth` / `s2:mean_solar_zenith`; Landsat `view:sun_azimuth`
  / `view:sun_elevation`). Compute the flag per scene and aggregate it per composite (e.g. the share of a pixel's
  clear observations that were in shadow); don't apply one sun angle to a multi-date composite.
- **Land cover:** tea, grassland and settlements are not forest change. Mask with ESA WorldCover (`esa-worldcover`)
  before disturbance analysis. WorldCover is a 2020/2021 snapshot only: a stand opened before 2020 that is now
  grass or scrub would be masked out, which hides the very signal Module A looks for. Disturbance work needs a
  forest mask valid at the **start** of the period (e.g. WorldCover tree cover combined with high early-period NBR
  from the Landsat record); the method goes in an ADR (M1.4/M1.6).

## 3D visualisation rules

- **3D is for viewing, never for measuring.** Every number (area, slope, counts, accuracy) comes from the 2D outputs. The viewer and 3D notebook views say so.
- **State vertical exaggeration** on every 3D view and screenshot; default 1×. Never exaggerate silently.
- Hills hide what is behind them: any 3D figure used in a write-up is paired with the 2D map of the same layer.
- Drapes must share the DEM's grid: reproject/resample the layer to the DEM extent first (nearest for classes, bilinear for continuous values) and check alignment on a known feature (a ridge or road).
- The viewer is static: no server, no API keys, no tracking. Pin CDN library versions. Keep `viewer/` under 100 MB; layers as PMTiles: vector PMTiles for vectors, raster PMTiles for rasters (MapLibre can't read GeoTIFF/COG
  without an extra protocol plugin, so don't ship COGs to the viewer).
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
- Never push to `main`, never force-push, never delete data in `data/`. Open a PR when the task passes (`gh` locally; the GitHub MCP tools in cloud sessions, which have no `gh`).

## Definition of done (every task)

- Acceptance criteria in `docs/ROADMAP.md` met and ticked.
- `ruff check`, `ruff format --check`, `pytest -m "not network"` pass.
- Touched notebooks execute cleanly from a fresh kernel (the verifier runs them to a scratch copy) and are committed stripped of outputs (`nbstripout`).
- Any new threshold, window or method choice has an ADR in `docs/decisions/`.
- PR description: what changed, how it was verified, open questions.
