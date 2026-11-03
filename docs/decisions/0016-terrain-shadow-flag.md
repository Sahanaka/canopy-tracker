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

- **The geometric flag is almost empty in this AOI.** Self-shadow: 0 cells in all 691 scenes (the minimum cos i of any valid observation in any composite is 0.047: no facet faces away from the sun), although 0.040 % of 30 m cells have a slope above 52.38 degrees.
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
  class 2 covers a median 0.17 % (max 1.27 %) of the AOI before 04.00 and 0.023 % (max 0.96 %) from 04.00; modelled shadow covers at most 2e-5. P(modelled | class 2) has median 0 (max 0.0085 before, 0.0207 from 04.00); P(class 2 | modelled)
  is defined in 35 / 18 scenes with median 0 (max 0.75 / 1.0). The chance level (the AOI share of each) is below 1e-4 for the model, so the two disagree on where shadow is. What the data do show: from 04.00, class 2 pixels have lower cos i
  than clear pixels in 191 of 198 scenes (median cos_i_mean 0.791 vs 0.853, median difference -0.066), as expected if class 2 is topographic shadow; before 04.00 there is no such difference (0.851 vs 0.848, lower in 104 of 217 scenes),
  consistent with ADR 0010 (class 2 then also held dark soil and burns). SCL class 2 therefore tracks low-illumination slopes better than the geometric flag does; it is kept as clear (ADR 0012) and its removal remains a sensitivity option for M1.6.
- **Illumination vs reflectance** (SWM 2023, NEM 2023, deciles of cos_i_mean): median B08 *falls* with cos i (SWM 0.370 in the least-lit decile to 0.315 in the most lit; NEM 0.313 to 0.282; Pearson r at no shift -0.298 and -0.144).
  This is the opposite of an illumination effect and is confounded by land cover (steep faces are forest, gentle ground tea, grass and settlement; not masked until M1.4). It supports no calibration.
- **Alignment check inconclusive.** The correlation between composite B08 and cos_i_mean over shifts of +-3 px (10 m) varies monotonically, with its maximum on the edge of the window (SWM 2023 at dy +3, dx -3; NEM 2023 at dy -3, dx -3), so the horizontal
  registration cannot be confirmed or refuted by it. A high-pass variant (31 x 31 px box mean removed; scratch run, not in the code) also showed no consistent maximum (|r| <= 0.19). The DEM's stated 6 m CE90 (0.6 px of 10 m) is relied on.

## Consequences

- Downstream (M1.6, M1.7) carry both columns: all pixels and D6-unflagged pixels, plus `cos_i_mean` for a sensitivity test. Expect identical results for the binary flag in this AOI; do not report "terrain-flagged" as if it removed shaded hillsides.
  What would remove them: a cos i cut (needs a justification from the disturbance threshold, deferred), SCL class 2 masking for baseline >= 04.00 scenes (ADR 0012 decision to revisit), or a band-ratio illumination correction (needs reflectance, so a rerun).
- The flag is a DSM flag: it mixes terrain and the canopy of 2011-2015 (ADR 0015).
- The 30 m flag is blocky at 10 m, and bilinear smoothing lowers extreme slopes, so self-shadow may be under-flagged slightly.
- Cost: 691 scenes took 449 s in the full run (plus 31 s for the two pilot windows), about 0.7 s per scene including aggregation and COG writing, peak 0.70 GB; the MTD_TL check (691 downloads, 8 threads) 108 s; the SCL class 2 comparison 214 s.
  The 43 shadow COGs plus tables take 147 MB in `outputs/M1.3/` (141 MB for the COGs).
- Revisit if the AOI moves to steeper terrain or lower sun, if a DTM is adopted, or if M1.6 shows that cos_i_mean explains disturbance-signal variance.
