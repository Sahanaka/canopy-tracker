# 0015 — Copernicus DEM, 30 m nested grid, Horn derivatives, horizon buffer

- **Status:** proposed (the owner approved recommendations D1, D2, D4 and D7 of docs/plans/M1.3.md on 2026-11-03; this text awaits sign-off)
- **Date:** 2026-11-03
- **Task:** M1.3

## Context

M1.3 needs slope, aspect, a hillshade and the inputs of a per-scene terrain-shadow flag (ADR 0016) over the AOI. CLAUDE.md fixes the CRS
(EPSG:32644) and the DEM source (`cop-dem-glo-30` on Planetary Computer). The choices left: the grid the derivatives live on, the derivative
formulas, how far beyond the AOI the DEM must reach to see shadow-casting terrain, and the display hillshade. `outputs/` is git-ignored, so every number the
decision rests on is in this file.

Facts about the source, read from the files and the Product Handbook v5.0 (29 Nov 2022, Table 1 and sections 1.2.1-1.2.3):

- It is a **DSM** (canopy, buildings, water flattened) from TanDEM-X/WorldDEM, one release dated 2021-04-22; the acquisitions are 2011-2015.
  Vertical datum EGM2008 (heights in m above the geoid, not ellipsoidal), horizontal WGS84-G1150, float32, 1 arc-second spacing (about 30.9 m N-S and
  30.7 m E-W at 6.8 N). Accuracy: absolute vertical < 4 m LE90; relative vertical < 2 m LE90 for slopes up to 20 % and < 4 m for steeper; horizontal < 6 m CE90.
- Two 1-degree COG items cover the AOI plus buffer: `Copernicus_DSM_COG_10_N06_00_E080_00_DEM` and `..._E081_00_DEM` (3600 x 3600 px each, deflate, no nodata
  value declared, same `datetime`). The 81 E seam is at UTM x = 500000 m, 810 m east of the AOI edge (x 499190), inside the buffer.
- **PixelIsPoint.** Both files carry `AREA_OR_POINT=Point`. GDAL's default reading shifts the geotransform by half a pixel (origin 80.99986111 E instead of
  81.0; with `GTIFF_POINT_GEO_IGNORE=TRUE` it reads 81.0). The STAC `proj:transform` agrees with the shifted value. odc-stac and rasterio therefore place
  the posts correctly by default, in line with the handbook (posts on integer degrees, shared by neighbouring tiles). No extra shift is applied.
- The datum has no effect on slope, aspect or shadow (they depend only on height differences, and EGM2008 is smooth over 10 km).

## Decision

1. **Grid (D1a).** The DEM is resampled **bilinearly** with odc-stac to a 30 m EPSG:32644 grid that shares the top-left corner of the M1.2 10 m grid
   (489180, 758070; 1001 x 1001 px at 10 m), so the AOI grid is 334 x 334 cells and each 10 m pixel lies in exactly one 30 m cell. Slope, aspect, cos i and
   shadow are computed at 30 m and replicated (not interpolated) to 10 m. No 10 m slope is created: interpolating the DEM to 10 m would give a 3 x 3 Horn window
   of 20 m, narrower than one source post, i.e. stripe artefacts without information. Bilinear (not cubic) avoids overshoot at breaks and is the method ADR 0009 used.
2. **Derivatives (D2).** Horn (1981) 3 x 3 weights in plain numpy, rows north to south: dz/dx = ((c+2f+i) - (a+2d+g)) / (8 res), dz/dy = ((a+2b+c) - (g+2h+i)) / (8 res).
   Slope = atan(hypot), 0-90 degrees; aspect = downslope bearing, 0-360 degrees clockwise from north = atan2(-dz/dx, -dz/dy) mod 360, NaN where the gradient is
   exactly zero. cos i = unit normal (-dz/dx, -dz/dy, 1)/norm dotted with the sun unit vector (E, N, Up), which equals cos z cos s + sin z sin s cos(az - aspect).
   The outer ring of every raster is NaN; the DEM is buffered so the ring never reaches the AOI. Zevenbergen-Thorne was not used: GDAL recommends it for smooth terrain and
   these hills are not. UTM grid north is taken as true north (meridian convergence <= 0.012 degrees at 0.1 degrees from the central meridian, latitude 6.8 N).
3. **Horizon buffer (D4).** W = (z_max - z_min,AOI) / tan(elevation_min), with z_max = 2527 m (Pidurutalagala, 8292 ft, Survey Department; Wikipedia gives 2524 m, the larger
   value is used), z_min,AOI from the DEM itself and elevation_min = the lowest sun elevation in `scenes_used.csv`. Measured: elevation_min 52.380 degrees (maximum 69.517),
   z_min,AOI 1044.52 m (AOI-only load) and 1044.47 m (buffered load); W = 1142.48 m and 1142.52 m; rounded up to **39 cells = 1170 m** (buffered grid 412 x 412 cells).
   The highest cell inside the buffer is 1893.34 m and inside the AOI 1772.69 m, so the 2527 m cap is conservative. Terrain beyond W cannot shade any AOI cell.
   The DSM canopy adds tens of metres to z_min, which only shortens W. The march stops at a cell's own (z_max - z0)/tan(elevation).
4. **Step (D4).** The ray march uses 0.5 cell (15 m). Convergence on the lowest-sun scene (`sentinel-2a_2018-01-03`, elevation 52.380, azimuth 142.489), of 334 x 334 = 111,556 cells:
   steps of 1 and 0.5 cell flag 1 cell, 0.25 cell flags 3 cells (all cast shadow, no self-shadow). The result does not depend on the step at the level that matters (2 cells in 111,556).
5. **Display hillshade (D7a).** Azimuth 315 degrees, altitude 45 degrees, GDAL's documented defaults (`gdaldem hillshade --help`, GDAL 3.13.3: "default: 315", "default: 45"). Display only; analysis
   always uses the per-scene cos i. `T.hillshade` = 255 max(cos i, 0) as uint8 (outer ring 0). Checked against `gdaldem hillshade` on the buffered DEM: maximum absolute difference 1 DN,
   mean 0.336 DN, no cell differing by more than 1 DN.
6. **Cross-checks of the load.** The odc-stac DEM equals a direct rasterio bilinear `reproject` of the same items to within 0.069 m (network test, tolerance 0.1 m, seam strip excluded). The same cell differs by
   at most 0.247 m between the AOI-only and the buffered load, because GDAL's warp approximates the transform per output window. Both are far below the DEM's 2-4 m relative accuracy; the pipeline raises if the
   difference exceeds 1 m.

Result for the AOI (30 m cells, Horn): slope p01 1.46, median 15.14, p99 39.95, maximum 61.17 degrees; 0.040 % of cells are steeper than the minimum sun elevation (52.38 degrees).
The median agrees with the 15.5 degree figure in ADR 0009 (difference 0.36 degrees; sanity reference only, a different computation).

## Consequences

- **The slope is the slope of the canopy surface**, not of the ground. Stand edges 20-30 m high create false slopes and canopy cast shadows, and stands opened since 2011-2015 still appear raised. Module C
  (watershed risk) needs a DTM decision before it uses these slopes. The README and every 3D view say "DSM".
- Slopes at 30 m underestimate peaks (bilinear smoothing) and cannot resolve features narrower than about 60 m (the Horn window).
- The 30 m flag is blocky at 10 m. M1.7 patch attributes (mean slope) should be read from `terrain_30m.tif`, not from a replicated 10 m copy.
- Revisit if a DTM (for example FABDEM or an airborne product) is adopted, or if the AOI changes (the buffer and the nesting depend on the M1.2 grid; `composite_shadow` raises on a mismatch).
- Cost (this machine, 4 cores, 15 GB): DEM load plus terrain rasters 6.8 s, peak 0.28 GB; the whole run peaked at 0.70 GB.
