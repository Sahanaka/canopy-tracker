# 0007 — AOI validation rules

- **Status:** accepted
- **Date:** 2026-11-01
- **Task:** M0.3

## Context

`highland.aoi.load_aoi()` must check the owner's `data/aoi/aoi.geojson` against explicit rules (roadmap M0.3: single
polygon, EPSG:4326, area 50-200 km², inside Sri Lanka's bounding box). The 50-200 km² range is the owner's number from
the roadmap, not derived from data. No satellite data is involved.

## Decision

- **D1 Area method:** planar area in EPSG:32644, the project CRS, so the AOI area matches every later area figure.
  Zone 44 has its central meridian at 81°E and k0 = 0.9996; over Sri Lanka planar area is 0.03-0.08 % below
  ellipsoidal area (checked with pyproj: areal scale 0.9992 near 81°E, 0.99971 at 79.7°E). Geodesic area
  (`pyproj.Geod`, WGS84) is used only as a test cross-check (0.1 % tolerance).
- **D2 Country box:** (W 79.64, S 5.86, E 81.95, N 9.88) degrees, the EPSG area of use of EPSG:5235 (SLD99 / Sri Lanka
  Grid 1999), no buffer. A test reads it from the local PROJ database and also asserts it lies inside 78-84°E (zone
  44), so no separate zone check exists. Cross-check: MFA Sri Lanka 5°55'-9°51' N, 79°41'-81°53' E. Natural Earth 110m
  was rejected as too coarse.
- **D3 Geometry leniency:** accept a Polygon, or a one-part MultiPolygon (unwrapped); drop Z; reject holes,
  multi-part and non-polygon geometry; reject invalid geometry with `explain_validity` and no auto `make_valid`
  (it can change the area); normalise ring orientation to counter-clockwise silently (RFC 7946 §3.1.6: parsers
  SHOULD NOT reject).
- **D4 Wrong declared CRS:** a legacy `crs` member that GDAL honours (for example EPSG:32644) is rejected with
  "re-export as EPSG:4326", not reprojected. The CRS check compares the datum (WGS84) and requires a geographic CRS,
  not the EPSG code, because GDAL labels 3-D GeoJSON as EPSG:4979.
- **D5 Default path:** repo-relative from `__file__`; valid for the editable install (ADR 0004). Other installs pass
  `path=`. No environment-variable override.
- **D6 Owner instructions:** `data/aoi/README.md`, linked from the top-level README. `python -m highland.aoi [path]`
  is the owner's validator.
- **D7 Real-file test:** `tests/test_aoi_file.py` validates the committed file, unmarked, and skips while it is
  absent.

## Consequences

- The owner gets an error that names the rule and the measured value; nothing is silently repaired or reprojected.
- Once the owner commits the file, a bad edit fails CI. M0.3 stays unticked until that file passes.
- Revisit the bounds only through a roadmap or ADR edit; revisit D5 if a non-editable use case appears.
