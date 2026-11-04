# 0016 — Terrain-shadow flag: per-scene geometry, share of valid observations, and what it did here

- **Status:** proposed (the owner approved recommendations D3, D5 and D6 of docs/plans/M1.3.md on 2026-11-03; this text awaits sign-off)
- **Date:** 2026-11-03
- **Task:** M1.3

## Context

CLAUDE.md requires a terrain-shadow / low-illumination flag computed **per scene** from the DEM and that scene's sun geometry, aggregated per composite, with results reported with and
without it. ADR 0014 deferred it to M1.3 and stored what is needed: per scene the sun azimuth and elevation (from `s2:mean_solar_*`, elevation = 90 - zenith) and the 10 m valid-observation masks of each
window-year. ADR 0015 fixes the DEM, grid and buffer. Open: what counts as shadow, which sun angles to use, how to aggregate and where to cut. All numbers below come from the 43 window-years
(691 scenes, 2016-2026) in `outputs/M1.3/`, which is git-ignored.

Sun geometry in the data: elevation 52.38 to 69.52 degrees (zenith 20.48 to 37.62), azimuth 48.07 to 146.93 degrees clockwise from north (NE in June, SE in late December/early January).
For a facet facing directly away from the sun, cos i = cos(zenith + slope) <= 0 needs the slope to exceed the **sun elevation**, so self-shadow here needs a slope above 52.38 degrees facing away.
(The plan text said 37.6 degrees; that is the zenith and was wrong. Cast shadow needs a horizon angle above the elevation, also at least 52.38 degrees.)

## Decision

1. **Definition (D3a).** The binary flag is geometric only: self-shadow (cos i <= 0 from Horn normals) union cast shadow (horizon-angle ray march, ADR 0015). It has no free parameter. No low-illumination
   cut on cos i is applied, because any such number (for example "half the flat-ground beam") would be an assumption this data cannot ground without per-scene reflectance, which ADR 0014 avoided.
   Instead the continuous **cos_i_mean** and **cos_i_min** are written per composite so that M1.6 can test sensitivity.
2. **Sun angles (D5a).** The tile-mean angles from `scenes_used.csv` (the ADR 0014 contract) are used for every scene. Check against the Sentinel-2 MTD_TL.xml sun-angle grid (5 km spacing; elements
   `Sun_Angles_Grid/{Zenith,Azimuth}/Values_List/VALUES` and `Mean_Sun_Angle`; geocoding from `Tile_Geocoding`), all 691 scenes, bilinear at the AOI centre and four corners:
   - the XML tile mean equals the STAC value (max |difference| 0 in azimuth, 7e-15 in zenith);
   - AOI centre minus tile mean: azimuth -0.342 to +0.646 degrees, zenith -0.353 to -0.126 degrees;
   - largest |difference| from the tile mean at any of the five points: azimuth 0.784, zenith 0.404 degrees; variation across the AOI (max - min of the five points): azimuth up to 0.313, zenith up to 0.131 degrees;
   - slope-equivalent error max(|dz|, |daz| sin z) over all scenes: at most 0.404 degrees.
   Criterion fixed in the plan: keep the tile mean if this is below the slope noise implied by the DEM, sigma_slope ~ 0.433 sigma_z / res (Horn, uncorrelated errors, sigma_z = LE90 / 1.645, res 30 m),
   which is 1.01 degrees for the 2 m and 2.01 degrees for the 4 m relative accuracy. 0.404 < 1.01, so the tile mean stays. (Uncorrelated errors are a pessimistic proxy; the AOI-wide variation was larger than the +-0.06
   the plan expected, but still far below the noise.)
3. **Aggregation (D6).** Shadow is computed per scene and aggregated per pixel over the window's valid observations (the M1.2 masks, which keep SCL class 2, ADR 0012, so shaded observations are in the denominator):
   `shadow_share` = shadowed valid observations / valid observations (NaN where none), plus `n_shadow`, `cos_i_mean`, `cos_i_min`. Per window-year 10 m COG `s2_<season-year>_<WIN>_shadow.tif`.
   **The composite flag is shadow_share >= 0.5**: at that share a median over the observations takes (or averages in) a shadowed value (odd n: s > n/2; even n: s >= n/2). This is logic, not a tuned threshold.
   `shadow_share > 0` ("any") is reported as well.
4. **No single angle is applied to a composite.** Land cover is not masked here (M1.4). The thin flags of M1.2 are carried into `shadow_summary.csv`.

## Results

- **The geometric flag is almost empty in this AOI, for Sentinel-2's roughly 10:30 local overpass.** (It is a property of that sun geometry, elevation 52.38 to 69.52 degrees; it does not carry over to lower sun, see Consequences.) Self-shadow: 0 cells in all 691 scenes (the minimum cos i of any valid observation in any composite is 0.047: no facet faces away from the sun), although 0.040 % of 30 m cells have a slope above 52.38 degrees.
  Cast shadow: 82 of 691 scenes show any (all in the NEM window, sun elevation 52.38 to 54.41 degrees, azimuth 138.3 to 146.9 degrees); the largest AOI share in any scene is 1.8e-5 (2 cells of 30 m).
  Valid observations shadowed in a scene: at most 6.5e-5.
- Per composite: `shadow_share >= 0.5` flags 9e-6 of the AOI (one 30 m cell, 9 pixels) in 4 of 43 window-years (NEM 2017, 2018, 2019, 2024) and nothing elsewhere; "any shadowed observation" is non-zero in 10 window-years, all NEM
  (9e-6 to 1.8e-5 of the AOI). The largest per-pixel shadow share is 0.56.
- **The continuous layer carries the illumination effect.** cos_i_mean over the AOI per window-year: median of the 43 window medians 0.874, lowest p01 0.359 (NEM 2024), highest p99 0.997; per-pixel minimum cos i of one scene 0.047.
  Window medians by season: SWM (NE sun) 0.881-0.894, FIM 0.871-0.897, SIM 0.832-0.874, NEM (SE sun, lowest elevation) 0.776-0.811.
- **With and without the flag** (`with_without_flag.csv`: AOI median, p01, p99 of NDVI, NBR, NDMI): dropping D6-flagged pixels changes no window median by more than 2.2e-6 (maximum over the three indices and 43 windows);
  keeping only pixels with no shadowed observation, 3e-6. Sensitivity rows that drop the least-lit 10 % / 25 % of AOI pixels per window (not proposed as cuts) change medians by at most 0.0097 / 0.019 (NBR),
  0.0085 / 0.018 (NDVI), 0.0077 / 0.015 (NDMI). So the binary flag does not matter in this AOI; the continuous cos i may.
- **Diagnostic against SCL class 2** (20 m SCL grid; modelled shadow taken from the 30 m cell containing each 20 m cell centre; 415 scenes with class 2 present, 217 with baseline < 04.00 and 198 >= 04.00; a diagnostic, not a calibration):
  class 2 covers a median 0.17 % (max 1.27 %) of the AOI before 04.00 and 0.023 % (max 0.96 %) from 04.00; modelled shadow covers at most 2.4e-5 of the 20 m-grid AOI (`scl2_vs_model.csv`, `share_model`; the 1.8e-5 above is the maximum on the 10 m grid in `scene_shadow.csv`; the two grids sample the same 30 m cells differently). P(modelled | class 2) has median 0 (max 0.0085 before, 0.0207 from 04.00); P(class 2 | modelled)
  is defined in 35 / 18 scenes with median 0 (max 0.75 / 1.0). The chance level (the AOI share of each) is below 1e-4 for the model, so the two disagree on where shadow is.
  Class 2 is rare (shares above) and its pixels are not clearly shadowed: from 04.00 their mean cos i is 0.791 (median over 198 scenes) against 0.853 for clear pixels (median difference -0.066, lower in 191 of 198 scenes), but 0.791 is
  about what a *flat* surface gets at the NEM sun (sin of 52-54 degrees = 0.79-0.81), so these are not steeply back-lit slopes. Before 04.00 there is no difference (0.851 vs 0.848, lower in 104 of 217 scenes), consistent with ADR 0010
  (class 2 then also held dark soil and burns). The difference is confounded by land cover: dark canopy on steep faces is darker in every band, and the classifier may call those pixels class 2 without any terrain shadow.
  This diagnostic therefore does **not** show that class 2 tracks low illumination better than the geometric flag. Masking class 2 in M1.6 could preferentially remove dark conifer or forest pixels on steep faces, which are the Module A target;
  class 2 stays kept as clear (ADR 0012) and any change must be tested against that risk.
- **Illumination vs reflectance** (SWM 2023, NEM 2023, deciles of cos_i_mean): median B08 *falls* with cos i (SWM 0.370 in the least-lit decile to 0.315 in the most lit; NEM 0.313 to 0.282; Pearson r at no shift -0.298 and -0.144).
  This is the opposite of an illumination effect and is confounded by land cover (steep faces are forest, gentle ground tea, grass and settlement; not masked until M1.4). It supports no calibration.
- **Alignment on a known feature: the flattened lakes.** The DEM flattens water bodies. Two lakes are visible as zero-gradient cores: 1244.0 m (8 core cells, 37 cells at the lake height, 30 m cells) and 1231.5 m (7 and 35 cells). The lake mask was
  correlated with the per-pixel frequency of SCL class 6 (water / observations with SCL 4, 5 or 6, no cut) from 118 scenes with baseline >= 04.00 and clear_fraction > 0.5. The SCL was re-read in 800 m windows
  around each lake (the cached M1.1 masks cannot separate class 6). Offsets of +-60 m in 10 m steps on a 10 m lattice aligned with the 20 m SCL grid; dx > 0 = S2 water east of the DEM lake, dy > 0 = NORTH (sign convention unit-tested):
  - lake 1: r = 0.679 at (+20 m E, +10 m N) against 0.663 at no offset; bootstrap over scenes (300 resamples, 2.5-97.5 %) dx 10 to 20 m, dy 0 to 10 m. Centroid of the water frequency (no cut) (+24, +14) m; of frequency >= 0.3 / 0.5 / 0.7
    (within 12 px of the mask's bounding box): (+37, +24) / (+11, +10) / (-25, -17) m. The reviewer's own recomputation gave (+21, +17) / (-8, -4) / (-25, -17) m, the same pattern: the sign changes with the cut, the core of the water lies south-west and the fringe north-east.
  - lake 2: r = 0.599 at (+30, +30) against 0.551 at no offset; bootstrap dx 10 to 30 m, dy 10 to 30 m. Centroid (+23, +55) m; at >= 0.3 / 0.5 / 0.7: (+24, +61) / (+26, +71) / (0, +66) m (reviewer: +24 to +26 east, +67 to +71 north). The 60-70 m northward
    centroid offset reflects a difference in lake extent (the DEM mask runs along the inflow channels), not a registration shift.
  - The zero-gradient cores alone (7-8 cells) are unstable: (-10, -10) with r 0.560 and (+20, +50) with r 0.621.
  **Conclusion: the registration is consistent with zero to within about one DEM cell (30 m), and the direction of any offset is not determined**: the best-offset correlation points north-east, the threshold centroids change sign with the cut for lake 1
  and are dominated by lake extent for lake 2. The resolution (30 m DEM cells, 20 m SCL, 10 m steps, about +-15 m) and the DEM-versus-S2 water definition limit the check; the bootstrap covers scene sampling only. The DEM is not shifted. The reviewer's crude centroid
  impression (S2 water 10-20 m south-east) is likewise within that uncertainty. Consequence: 30 m derived layers (slope, aspect, cos i) can be displaced by up to about one cell against the 10 m imagery; read patch attributes (M1.7) at cell level, not pixel level.
- **Weak check: composite B08 against cos_i_mean.** Shifts of +-3 px (10 m), dx > 0 east and here dy > 0 = row-positive = SOUTH (the opposite of the lake check): the strongest |r| is SWM 2023 dy -3, dx +3, r = -0.340 (r at no shift -0.298) and NEM 2023 dy +3, dx +3, r = -0.246 (-0.144), both on the edge of the window; r varies
  monotonically with shift, so there is no interior maximum. (An earlier version picked the signed maximum, which is the weakest relationship, and quoted the wrong shifts.) The check is weak because cos_i_mean is in 30 m blocks, a composite mixes
  many dates and sun angles, and a land-cover trend dominates. A high-pass variant (31 x 31 px box mean removed; scratch run, not in the code) also showed no consistent maximum (|r| <= 0.19). It is kept only as a weak consistency check.

## Consequences

- Downstream (M1.6, M1.7) carry both columns: all pixels and D6-unflagged pixels, plus `cos_i_mean` for a sensitivity test. Expect identical results for the binary flag in this AOI; do not report "terrain-flagged" as if it removed shaded hillsides.
  What would remove them: a cos i cut (needs a justification from the disturbance threshold, deferred), SCL class 2 masking for baseline >= 04.00 scenes (ADR 0012 decision to revisit), or a band-ratio illumination correction (needs reflectance, so a rerun).
- The flag is a DSM flag: it mixes terrain and the canopy of 2011-2015 (ADR 0015). In particular cos i and slope at stand edges are partly caused by the 2011-2015 canopy (20-30 m high edges), so `cos_i_mean` must not be
  used to "correct" edge pixels in M1.6 without a DTM.
- **For M1.6:** compare composites of the **same season** across years (the sun geometry of a season repeats), and record the per-pixel difference in `cos_i_mean` between the two compared composites next to every change,
  so that an illumination difference is visible beside a disturbance signal.
- **Buffer guard.** `scene_shadow` and `composite_shadow` raise when horizon_buffer_m(z_min, elevation, 2527 m) exceeds the buffer (40 cells = 1200 m here). z_min is the minimum over every 30 m cell that contains any AOI pixel at 10 m
  (1008.41 m, buffered load; ADR 0015): the partial edge cells of the 334 x 334 grid have their centres outside the polygon but feed 1000 AOI pixels. The first run used the minimum over cells with a centre inside the polygon (1044.47 m),
  a buffer of 39 cells (1170 m) and was 0.31 m too short; the guard found this, the stage was rerun with 40 cells and all 43 windows were forced (comparison below). (The earlier statement that those cells are NaN in the outputs was wrong.)
  **Landsat 5/7 winter sun elevations in the 40s need a wider buffer**: at 44 degrees (z_min 1008.41 m) W = 1572.5 m (53 cells, 1590 m) against 1200 m here (1170 m in the first run). The statement "the flag is almost empty" must not be carried to Landsat scenes.
- The 30 m flag is blocky at 10 m, and bilinear smoothing lowers extreme slopes, so self-shadow may be under-flagged slightly.
- Cost (final run, 40-cell buffer, all 43 windows forced; `run_timings.json`, `run_timings_diagnostics.json`, `lake_alignment.json`): 691 scenes in 414.4 s, about 0.6 s per scene including aggregation and COG writing, peak 702 MB resident; `run_all` wall time 448.1 s;
  the MTD_TL check (691 downloads, 8 threads) 106.1 s; the SCL class 2 comparison 213.1 s; the lake check 38.3 s (118 SCL window reads x 2 lakes). Disk (decimal MB, `du -b`): the 43 shadow COGs 146,952,865 bytes = 147.0 MB;
  the whole `outputs/M1.3/` directory 154,108,337 bytes = 154.1 MB (with the lake check, tables and PNGs).
- **Before/after the buffer fix** (39 to 40 cells; old outputs hashed and compared): `shadow_share` and `n_shadow` are bit-identical in all 43 files; `cos_i_mean` and `cos_i_min` differ by at most 4.5e-5 (the DEM is resampled on the larger grid, at most 0.0057 m
  different in the AOI), so the files are not byte-identical; `scene_shadow.csv` is identical; `shadow_summary.csv` differs by at most 1e-5; `with_without_flag.csv` medians by at most 7.6e-6 and the pixel counts of the least-lit sensitivity subsets by at most 7.
  Every number quoted in this ADR is unchanged at the precision given.
- Revisit if the AOI moves to steeper terrain or lower sun, if a DTM is adopted, or if M1.6 shows that cos_i_mean explains disturbance-signal variance.
