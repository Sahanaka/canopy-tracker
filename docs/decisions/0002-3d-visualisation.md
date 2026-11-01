# 0002 — 3D visualisation: PyVista for QA, MapLibre web viewer for sharing

- **Status:** accepted
- **Date:** 2026-10-18
- **Task:** M1.3, M1.8

## Context

The study area is steep. Whether an opened stand is a risk depends on where it sits on the slope, which is hard to read from flat maps, especially for people who don't read GIS maps. 3D views also have known pitfalls: occlusion, vertical exaggeration and distorted areas.

## Decision

- **Analysis stays 2D.** 3D is only a way of looking at outputs.
- **Notebook QA:** PyVista drapes any AOI raster over the Copernicus GLO-30 DEM (M1.3), for catching errors such as terrain shadow early.
- **Sharing:** a static MapLibre GL JS viewer in `viewer/`, published to GitHub Pages (M1.8). Terrain starts from the open Terrarium elevation tiles on AWS; layers are PMTiles. No server, no API keys.
- Rejected: CesiumJS (its convenient terrain needs a Cesium ion account), Kepler.gl (weaker terrain support), and a tile server such as TiTiler (needs hosting).

## Consequences

- Every new module gets a viewer task (M2.5, M3.6).
- Two elevation sources exist until we decide whether to cut terrain tiles from GLO-30; small mismatches between the drape and the analysis DEM are possible meanwhile.
- GitHub Pages limits (1 GB site, soft bandwidth limits) are ample for a 10 × 10 km area; revisit if the AOI grows.
