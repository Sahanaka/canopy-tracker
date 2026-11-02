# 0013 — Composite statistic, thin flag, index guard, output layout and item source for M1.2

- **Status:** proposed (owner approved recommendations D2, D3, D5, D6, D7 on 2026-11-02; ADR awaits owner sign-off)
- **Date:** 2026-11-02
- **Task:** M1.2

## Context

M1.2 turns the deduplicated Sentinel-2 L2A record over the AOI into 43 seasonal composites (FIM, SWM, SIM, NEM; 2016-2026 without
the partial SIM 2026; ADR 0011) with NDVI, EVI, NDMI and NBR. Five choices are not fixed by CLAUDE.md: which statistic to
take across dates, how to flag pixels the statistic cannot protect, how to handle indices that leave their nominal range,
what to write to disk, and which item list to read. This ADR also records two checks that the numbers rest on.

## Decision

### Offset and scale (verified, not assumed)

- Reflectance = `(DN - 1000) / 10000` if `s2:processing_baseline` >= 04.00, else `DN / 10000`; DN 0 = no-data -> NaN;
  negatives are kept. The decision uses the baseline only, never the date (05.xx baselines occur in 2016-2021; the archive has
  baselines 02.12 to 05.13). The DN is cast to float32 **before** subtracting (`masking.s2_dn_to_reflectance`), because
  `uint16 - 1000` wraps around under NumPy 2.
- **Verified live on 2026-11-02** (Planetary Computer, tile 44NMN, one acquisition each of baseline 02.12 (2016-04-13), 05.00
  (2019-03-09) and 04.00 (2021-12-03)): `odc.stac.load` with `stac_cfg` data_type uint16 / nodata 0 returns the **raw DN**:
  the B02 array on the 10 m AOI grid is bit-identical (`np.array_equal`) to a direct rasterio window read of the same asset
  (median DN 343, 1369 and 1698 respectively; minimum non-zero DN 1 for 02.12 and 1084 / 1055 for the offset baselines), and
  the dataset variables have no `scale_factor` / `add_offset`. None of the 762 cached items carries `raster:bands`, so odc-stac
  has nothing to apply. The offset is therefore applied exactly once, by us. The same comparison runs as a network test for a
  05.xx and a 02.12 item (`tests/test_composites_network.py`), which also checks the reflectance against the formula.
- For the 20 m bands (B8A, B11, B12) odc-stac's bilinear 10 m output equals a rasterio bilinear read of the same DN (99th
  percentile of the absolute difference 0; the 0.4 % of pixels that differ lie in the outer rows and columns of the window,
  where the two resamplers treat the edge differently). Because the offset is affine and constant, applying it after
  interpolation equals interpolating after applying it.

### D2 Composite statistic: median of per-scene indices; per-band median reflectance for display

- The four indices are computed per scene from that scene's reflectance and the per-pixel **median across the scenes' index
  values** is stored (`ndvi`, `evi`, `ndmi`, `nbr` in `_indices.tif`). Each stored value is therefore an index that some
  scene actually produced (or the mean of the two middle ones for an even count).
- The per-band **median reflectance** is stored in `_reflectance.tif` (7 bands including B03) **for display only** (true
  colour in M1.8). Do not compute indices from it. A per-band median can build a spectrum that no single date observed.
- On the written composites the two routes differ: for SWM 2023 pixels with n_obs >= 3 the absolute difference between the
  stored NDVI and the NDVI of the median bands has median 0.0067 and 99th percentile 0.064 (NBR 0.0088 / 0.060, NDMI
  0.0098 / 0.059); SIM 2023 0.0077 / 0.093, SWM 2019 0.0088 / 0.066 (NDVI). With 1-2 observations the two coincide
  (median difference <= 0.0003; for two observations the median is the mean in both cases, so only the ratio differs).
- Alternative not taken: the geometric median. It needs an extra package and yields a reflectance composite, not the per-scene
  index series that M1.5 ("annual median NBR") needs.
- **Citations (checked 2026-11-02 against Crossref, OpenAlex and the doi.org handle record; abstracts only, not the full
  papers).** Flood, N. (2013), "Seasonal Composite Landsat TM/ETM+ Images Using the Medoid (a Multi-Dimensional Median)",
  Remote Sensing 5(12): 6481-6500, doi:10.3390/rs5126481. Its abstract proposes the medoid because it is always selected from
  the available dates, "so the result is always a single observation for that pixel, thus preserving relationships between
  bands". This supports the statement that a per-band median is not an observed spectrum; it does not quantify the
  difference for this AOI (the numbers above do). Roberts, D., Mueller, N., McIntyre, A. (2017), "High-Dimensional Pixel
  Composites From Earth Observation Time Series", IEEE Trans. Geosci. Remote Sens. 55(11): 6254-6264,
  doi:10.1109/TGRS.2017.2723896 (the doi.org handle resolves to IEEE Xplore document 8004469). Its abstract describes the
  geometric median as maintaining the spectral relationship between bands and requiring no parameters. Crossref returned HTTP
  429 for the Roberts DOI, so title, authors and pages for it come from OpenAlex; the DOI itself resolves at doi.org. Neither
  paper was read beyond the abstract.

### D3 Thin flag and no extra cloud screening

- Per pixel: `n_obs` = number of valid observations (SCL clear per ADR 0012 **and** all seven bands non-zero). `thin` =
  0 if n_obs = 0, 1 if n_obs is 1 or 2, 2 if n_obs >= 3. The bound 2 is logical, not tuned: a median over fewer than three
  values cannot reject a contaminated one (the test suite shows a single outlier is rejected among 3 and not among 2).
- Per window-year: `thin_season_year` = the median `n_obs` over AOI pixels is <= 2, and `share_thin_1_2`, `share_zero_obs`
  and `share_le2_obs` are written for every window-year so any other cut can be applied later.
- No extra screening in M1.2 (no temporal-outlier rule, spectral rule or SCL buffer): each would need a k, a buffer or a
  threshold that this data cannot ground. Revisit if field checks show cloud leakage.

### D5 Output layout

Per window-year, `outputs/M1.2/s2_<season-year>_<WIN>_*` (EPSG:32644, 10 m, 1001 x 1001 px, COG, deflate):
`_indices.tif` (4 bands float32), `_reflectance.tif` (7 bands float32: blue B02, green B03, red B04, nir B08, nir08 B8A,
swir16 B11, swir22 B12), `_obs.tif` (uint16: `n_obs`, `thin`, `window_day_median`; nodata 65535 outside the AOI), plus
`clear_masks/*.npz`, `scenes_used.csv`, `composite_summary.csv`, `meta/*.json` and a README with the resolved environment.
`window_day_median` replaces the planned `doy_median` (a median day of year is meaningless for NEM, which spans the new year);
see the plan's Deviations. Measured size: 1.7 GB (about 40 MB per window-year; masks 2.8 MB in total).

### D6 Index guard

An index is NaN only where its denominator is exactly 0 or an input is NaN. Nothing is clipped. The share of finite values
outside [-1, 1] is written per composite and index. Over the 43 composites:

| index | largest out-of-range share (window) | mean over composites |
|---|---|---|
| NDVI | 0.004 % (SWM 2024) | < 0.001 % |
| NDMI | 0.004 % (SWM 2024) | < 0.001 % |
| NBR | 0.004 % (SWM 2024) | < 0.001 % |
| EVI | 0.092 % (FIM 2022) | 0.021 % |

EVI is the only index with visible leakage: its denominator `NIR + 6 Red - 7.5 Blue + 1` approaches 0 for dark, blue-bright
pixels, giving values up to 3.1e5 in magnitude (|EVI| > 1000 somewhere in 6 composites: SIM 2018, 2021, 2023, 2024, 2025 and FIM 2022). The single-
pixel extremes of the other indices also leave [-1, 1] (NDVI minimum -8.87 in FIM 2024, maximum 1.018 in SIM 2024), which only
happens when kept negative reflectance makes a sum small. Users of EVI should mask or winsorise explicitly and say so.
NDVI value ranges over the composites (percentiles over AOI pixels): 1st percentile 0.11-0.25, median 0.63-0.78, 99th
percentile 0.84-0.90, a range that is plausible for vegetated hills with some tea, grass and bare ground (not validated). The lowest medians
(SIM 2016 0.63, SIM 2025 0.64, SIM 2020 0.66) are thin or cloud-affected windows.

### D7 Item source

The frozen M1.1 search `outputs/M1.1/cache/items_s2_2015-06-01_2026-11-02.json` (762 items, sha256
`6a9885e0ca32a38cf1621e2e4c2223f70b2572b01db8800cc9274cf15f1835c6`) is read, then `dedupe_s2` leaves 699 items (item-list
sha256 `98ed2ef30a55012e93d1358934dc49f0c7b33085fe42755f3ea2dd54c1d95ad1`), the same scenes as the ADR 0011 tables.
691 acquisitions fall in the 43 window-years (the other 8 are in the partial SIM 2026). Each window-year records the hash of its
own item ids and its parameters in `meta/` and in the GeoTIFF tags, and is recomputed if either changes.

## Measured cost (one sandbox, 4 cores, Planetary Computer over the network, 4 dask threads)

- Pilot, cold: SIM 2023 (12 scenes) 58 s, peak RSS 1.2 GB; SWM 2023 (30 scenes) 132 s, peak RSS 2.6 GB.
- Full run, cold: 43 window-years, 691 acquisitions, 2970 s (49.5 min), peak RSS 3.4 GB (SWM 2025, 39 scenes). About 4.3 s per
  acquisition. Output 1.7 GB.

## Consequences

- The composites are **not terrain-flagged** (ADR 0014) and **not land-cover masked**; they are inputs.
- Thin season-years by the data-derived rule (median n_obs <= 2): FIM 2016, FIM 2017, SIM 2016, SIM 2017, NEM 2016, NEM 2017,
  SIM 2019, SIM 2023. ADR 0011's list (FIM 2016, 2017; NEM 2016; SIM 2019, 2023; also SIM 2020, FIM 2022) differs in two
  ways. SIM 2016, SIM 2017 and NEM 2017 are thin here as well (ADR 0011 did not list them). SIM 2020 and FIM 2022 have a median
  of 3 here but 46.5 % and 30.5 % of their pixels with <= 2 observations, so they stay flagged in the README as listed by the
  owner. Window-years with at least 30 % of pixels at <= 2 observations: FIM 2016, 2017, 2022; SWM 2016; SIM 2016, 2017, 2019,
  2020, 2021, 2023, 2024, 2025; NEM 2016, 2017 (the 30 % line is only a reading aid for this sentence, not a rule in the code).
  Read `share_le2_obs` per window rather than the binary flag. The full table is `outputs/M1.2/composite_summary.csv`
  (git-ignored; the notebook regenerates it).
- With 1-2 observations residual SCL cloud leakage passes straight into the composite; SIM composites are the least reliable.
  No cut-off for dropping a window-year is applied.
- Known limitation: `dedupe_s2` keeps the highest baseline (CLAUDE.md). For acquisition `sentinel-2a_2023-07-06` the kept 05.10
  item is largely empty in the AOI (B04 98 % DN 0, SCL 84 % class 1) while the dropped 05.09 item has 84 % valid data. It
  contributes no observations. It is the only kept scene with SCL class 1 over 50 % (1 of 699).
- Pre-/post-baseline-04.00 composites share the level shift through the offset, but SCL behaviour also changed at 04.00 (ADR
  0010). A step in a time series at 2021/2022 should be checked against the baseline column in `composite_summary.csv` before
  being read as change.
- SWM spans five months; use `window_day_median` before comparing years.
