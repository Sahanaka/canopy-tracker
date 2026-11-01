# Roadmap

Tasks are picked in order by `/next-task`. Each has acceptance criteria; tick the box only when every criterion is met.
Tasks marked **[human]** need the owner (field work, drawing, decisions); agents prepare for them but do not do them.

## Milestone 0 — Foundations

- [x] **M0.1 Package and environment**
  - `environment.yml` with pinned versions of the stack in CLAUDE.md; `pyproject.toml` installing `src/highland` in editable mode.
  - `ruff`, `pytest`, `nbstripout`, `pre-commit` configured; `pre-commit run --all-files` passes.
  - `src/highland/__init__.py` and empty module stubs from the CLAUDE.md layout; one passing smoke test.
- [x] **M0.2 CI**
  - GitHub Actions workflow running ruff and `pytest -m "not network"` on push and PR (micromamba or pip).
  - README badge and a "How to run" section.
- [ ] **M0.3 AOI** **[human draws, agent validates]**
  - Owner commits `data/aoi/aoi.geojson`. Agent adds `highland.aoi.load_aoi()` that validates: single polygon, EPSG:4326, area 50–200 km², inside Sri Lanka's bounding box; with tests.
- [ ] **M0.4 Field form spec**
  - `docs/field/form.md` describing the QField form (fields: class, canopy openness, photos, notes, date) and the expected GeoJSON schema.
  - `highland.io.load_field_points()` that validates that schema; tests with a fixture file.

## Milestone 1 — Find opened pine stands (Module A)

- [ ] **M1.1 Data availability audit** — `notebooks/01_availability.ipynb`
  - STAC search over the AOI for `sentinel-2-l2a` (full archive, mid-2015→now) and `landsat-c2-l2` (full archive, 1982→now).
  - Deduplicate reprocessed Sentinel-2 items (same datetime + MGRS tile, keep the highest baseline); report counts by platform and baseline.
  - Per scene: share of AOI pixels clear after masking (SCL / QA_PIXEL), not just scene-level cloud %.
  - Report the share of AOI pixels removed by SCL class 2 (cast shadow) and class 7 (unclassified) separately.
  - Plots: clear-pixel % by month × year for each sensor. Writes `outputs/M1.1/scene_inventory.parquet`.
  - ADR proposing composite windows (seasons) based on the plot, and the first year the Landsat record supports for M1.5.
- [ ] **M1.2 Sentinel-2 seasonal composites + indices** — `notebooks/02_s2_composites.ipynb`
  - Baseline-04.00 offset handled; SCL mask; median composites per window from the M1.1 ADR.
  - NDVI, EVI, NDMI, NBR per composite; GeoTIFFs (EPSG:32644, 10 m) in `outputs/M1.2/`.
  - Unit tests for offset correction, masking and each index on synthetic arrays.
- [ ] **M1.3 Terrain** — `notebooks/03_terrain.ipynb`
  - `cop-dem-glo-30` clipped to AOI; slope, aspect, hillshade; terrain-shadow flag for a given sun azimuth/elevation.
  - Outputs in `outputs/M1.3/`; tests for slope/aspect on a synthetic plane.
  - Interactive 3D QA view: `highland.viz3d.drape()` drapes any AOI raster (true-colour composite, slope, terrain-shadow flag) over the DEM with PyVista in Jupyter; vertical exaggeration is a parameter (default 1×) and is printed on the view.
  - Static PNG snapshot of one 3D view saved to `outputs/M1.3/`; drape function tested on a synthetic DEM (mesh shape, CRS/extent match), no rendering in CI.
- [ ] **M1.4 Land-cover mask**
  - ESA WorldCover (`esa-worldcover`, 2020/2021) tree-cover mask at 10 m and 30 m (30 m aggregation rule in an ADR); share of AOI per class reported.
  - ADR on the forest mask for disturbance analysis: WorldCover is a 2020/2021 snapshot, so stands opened before then must not be masked out (see CLAUDE.md "Land cover").
- [ ] **M1.5 Landsat annual composites** — `notebooks/04_landsat_annual.ipynb`
  - C2 scaling, QA_PIXEL mask, sensor harmonisation (ADR), annual median NBR and NDMI from the first year the M1.1 ADR supports → now, at 30 m.
  - Plot: count of clear observations per pixel per year (to show where the record is too thin).
- [ ] **M1.6 Disturbance detection v1**
  - Per pixel: largest year-on-year NBR drop, its year, and recovery slope over the following 5 years; masked with the start-of-period forest mask from the M1.4 ADR.
  - Threshold chosen from the data distribution and recorded in an ADR.
  - Outputs: `disturbance_year.tif`, `disturbance_magnitude.tif`.
- [ ] **M1.7 Patches**
  - Vectorise disturbed pixels to patches; drop below a minimum area (ADR); attributes: year, magnitude, area, mean slope, terrain-shadow share.
  - `outputs/M1.7/patches.geojson` plus a map figure.
- [ ] **M1.8 3D web viewer v1** — `viewer/`
  - Static MapLibre GL JS page (pinned version) published to GitHub Pages by a workflow; no API keys, no server.
  - Terrain: start with the open Terrarium elevation tiles on AWS; ADR on whether to switch to tiles generated from Copernicus GLO-30 so the view matches the analysis DEM.
  - Layers, each toggleable with a legend: Sentinel-2 true-colour drape (raster PMTiles), disturbance patches coloured by year (from M1.7, as PMTiles or GeoJSON), hillshade; time slider filtering patches by disturbance year.
  - Click a patch → popup with year, magnitude, area, mean slope, terrain-shadow share.
  - `highland.viewer.build()` regenerates `viewer/data/` from `outputs/`; documented in `viewer/README.md`. Total viewer size < 100 MB.
  - Footer note: 3D is for viewing; measurements come from the 2D outputs. Vertical exaggeration stated on screen.
  - Verified by a Playwright smoke test (page loads, layers toggle, no console errors) that runs locally, not in CI.
- [ ] **M1.9 Field-check sheet**
  - Notebook that picks ~20 reachable patches (near roads/paths if available from OpenStreetMap), exports them for QField, and later compares field points to the map (confusion table).
- [ ] **M1.10 Field visits** **[human]** — at least 10 patches checked; ~30 pine and ~20 native reference points, plus turpentine, eucalyptus and *Alstonia* points wherever encountered (for M2).
- [ ] **M1.11 Milestone write-up** — `docs/milestone-1.md`: what works, accuracy against field points, known failure modes; link to the live 3D viewer.

## Milestone 2 — Canopy classes (Module B) — outline, refine after M1

- [ ] M2.1 Monthly Sentinel-2 composites for one year + Sentinel-1 backscatter for cloudy months.
- [ ] M2.2 Feature stack: monthly indices, seasonal amplitude, ratios, simple texture.
- [ ] M2.3 Random Forest (scikit-learn) trained on field points; spatially blocked cross-validation. Classes: pine, turpentine, eucalyptus, native broadleaf, other; *Alstonia macrophylla* (invasive) as a stretch class.
- [ ] M2.4 Class map + accuracy report per class; feasibility note on separating turpentine, eucalyptus and invasive *Alstonia*.
- [ ] M2.5 Viewer: add the canopy class layer to the 3D viewer.

## Milestone 3 — Slope and watershed risk (Module C) — outline, refine after M2

- [ ] M3.1 Flow accumulation and stream proximity from the DEM.
- [ ] M3.2 Rainfall (CHIRPS or GPM IMERG) seasonal and extreme totals.
- [ ] M3.3 RUSLE-style erosion estimate (factor choices in ADRs).
- [ ] M3.4 Composite risk class per plot; validation against NBRO hazard maps if obtainable.
- [ ] M3.5 Combined "thinning priorities" map from M2 + M3.
- [ ] M3.6 Viewer: risk class and thinning-priority layers, stream network, and a slope-class drape.
