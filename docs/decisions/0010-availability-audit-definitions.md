# 0010 — Availability audit: definitions and measured counts

- **Status:** proposed — awaiting owner acceptance
- **Date:** 2026-11-02
- **Task:** M1.1

## Context

M1.1 inventories every Sentinel-2 L2A and Landsat C2 L2 acquisition over `data/aoi/aoi.geojson` (ADR 0009, 100.0 km²)
and measures how much of the AOI is clear on each. The windows ADR (0011) and the later composite tasks rest on these
definitions, so they and the counts they produced are recorded here. Run: 2026-11-02, Planetary Computer,
`notebooks/01_availability.ipynb`, environment in `outputs/M1.1/environment.explicit.txt` (not committed; regenerate with
`micromamba env export --explicit`). Search ranges: Sentinel-2 2015-06-01 to 2026-11-02, Landsat 1982-01-01 to
2026-11-02. The notebook fixes the end date as the constant `RUN_DATE` = 2026-11-02, and the item searches are frozen in
`outputs/M1.1/cache/items_s2_2015-06-01_2026-11-02.json` and `items_landsat_1982-01-01_2026-11-02.json`; these cached files
are the input of every number below. Planetary Computer reprocessing can change item counts on a fresh search, so a rerun
without the cache may differ.

## Decision

Definitions (the plan's D2–D6 and D8):

- **D2 Read resolution.** SCL is read at its native 20 m and QA_PIXEL at 30 m, over the AOI bounding box snapped outward
  to the native grid (EPSG:32644), with nearest resampling and `fail_on_error=True`. COG overviews are not used, because
  the resampling used to build them is not documented and averaging would corrupt class codes. The S2 grid is 501 × 501
  px with 250 000 pixel centres inside the AOI; the Landsat grid is 334 × 335 px with 110 889 inside.
- **D3 Row unit.** One row per acquisition = (platform, Sri Lanka local solar day, `Asia/Colombo`), so tiles or WRS rows
  of one pass fuse into one row and overlapping pixels are counted once (first valid pixel in item order wins). Item ids
  are kept in `item_ids`. `relative_orbit`, `collection_category` and `correction` are `+`-joined strings of the sorted
  unique values of the fused items (for example `"T1+T2"`), not lists. Landsat `qa_*_share` columns are overlapping flags
  and must not be summed; the 12 `scl_share_*` columns are mutually exclusive and sum to 1.
- **D4 Clear definitions.** S2: keep SCL 4, 5, 6 (`clear_base`). Three variants are stored beside it: `clear_keep2` (also
  keeps class 2), `clear_keep7` (also keeps class 7) and `clear_keep27`. M1.2's ADR chooses among them; this audit does
  not. Landsat: mask QA_PIXEL bits 0, 1, 3, 4, 5 and, on Landsat 8/9 only, bit 2 (cirrus) (`clear`); `clear_nocirrus` leaves
  cirrus unmasked. Bit 6 is never used (USGS: wrong in some L4–7 cases). Bits per sensor are from USGS LSDS-1618 v4.0
  (L4–7) and LSDS-1619 v6 (L8–9).
- **D5 Landsat tiers.** T1 and T2 are both included and reported separately; M1.5 decides which to use.
- **D6 Plot metric.** The sum over acquisitions of the clear fraction in a month, which equals the expected number of
  clear observations per AOI pixel; it needs no threshold. Acquisition counts are plotted beside it (acquisitions with
  data over the AOI, `valid_frac` > 0).
- **D8 Per-pixel counts.** `outputs/M1.1/clear_counts_{s2,landsat}_<variant>.tif`: uint16, one band per year-month
  (description `YYYY-MM`), on the native grid. Pixels outside the AOI polygon (but inside the bounding box) are written as
  nodata 65535, so summing or masking the raster cannot include out-of-AOI pixels. Season and year tables are summed from these.
- **Seasons.** The four Met Department seasons of CLAUDE.md as month sets: first inter-monsoon 3–4, SW monsoon 5–9,
  second inter-monsoon 10–11, NE monsoon 12–2; December belongs to the following year's NE monsoon. `partial_window` is
  computed against the searched date range (end capped at the run date).

Measured counts (all from the run above):

**Search and deduplication.** Sentinel-2: 762 items found, 699 kept after deduplication on (datetime, `s2:mgrs_tile`);
63 dropped (60 `lower_baseline`, 3 `older_processing_time`). All items are on tile 44NMN (mean AOI cover of the item
footprint 0.9997). Landsat: 782 items, all WRS 141/055 (cover 1.0), no duplicates. Pilot (2023 only): S2 117 items → 72
after deduplication, Landsat 42; no errors.

Sentinel-2 items by platform and processing baseline:

| platform | 02.12 | 03.00 | 04.00 | 05.00 | 05.09 | 05.10 | 05.11 | 05.12 | 05.13 | total |
|---|---|---|---|---|---|---|---|---|---|---|
| 2A before | 151 | 27 | 41 | 1 | 29 | 51 | 33 | 9 | 2 | 344 |
| 2B before | 133 | 20 | 40 | 0 | 28 | 51 | 55 | 22 | 4 | 353 |
| 2C before | 0 | 0 | 0 | 0 | 0 | 0 | 38 | 23 | 4 | 65 |
| 2A after | 150 | 25 | 37 | 1 | 5 | 50 | 32 | 9 | 2 | 311 |
| 2B after | 133 | 19 | 30 | 0 | 9 | 51 | 55 | 22 | 4 | 323 |
| 2C after | 0 | 0 | 0 | 0 | 0 | 0 | 38 | 23 | 4 | 65 |

Dropped duplicates (kept baseline ← dropped baseline): 05.10 ← 05.09 42; 05.10 ← 04.00 14; 04.00 ← 03.00 3; 05.00 ←
02.12 1; equal baseline, older processing time dropped: 05.09 1, 05.10 1, 05.11 1. Row-level list: `dedupe_dropped.csv`.

**Acquisitions** (one row each; no fused tile differs in baseline, `mixed_baseline` = 0 for all 699): Sentinel-2A 311,
2B 323, 2C 65. Landsat: 5 215 (204 T1, 11 T2), 7 165 (157, 8), 8 296 (286, 10), 9 106 (102, 4), total 782, of which 33 are T2 (11/8/10/4);
30 of the 33 T2 acquisitions have a clear fraction of exactly 0 (T1 mean clear fraction 0.400).
Landsat 7 SLC-off acquisitions: 123; `valid_frac` over all 165 Landsat 7 acquisitions has mean 0.831 and minimum 0.697; over the 123 SLC-off acquisitions alone the mean `valid_frac` is 0.773. **Error rows: 0** in both sensors.

**Acquisitions with no data over the AOI.** Seven S2 acquisitions (relative orbit 119: 2015-12-28, 2016-03-27, 04-26,
05-16, 06-05, 06-25, 07-15) have `valid_frac` = 0: the item footprint reaches the AOI but the swath has no pixels there.
They count as zero clear. The window tables report `n_with_data` (`valid_frac` > 0), which excludes them; `n_ok` still includes them. Three more S2
acquisitions are partly outside the swath (`valid_frac` 0.999996 on 2022-04-07, 0.860 on 2023-07-06, 0.797 on
2026-09-08). The first S2 acquisition with data over the AOI is 2016-01-14, and the first Landsat acquisition is 1988-01-14.
Valid S2 acquisitions per year: 2016 18, 2017 26, 2018 67, 2019 71, 2020 71, 2021 66, 2022 71, 2023 72, 2024 71,
2025 88 (2C joins), 2026 71 (to 2 Nov).

**SCL class 2 and class 7, separately** (share of AOI pixels per acquisition; pre/post from `baseline_max`, 04.00 = (4, 0);
`baseline_min`/`mixed_baseline` show no mixed rows):

| era | n | class 2 mean | class 2 median | class 2 p90 | class 7 mean | class 7 median | class 7 p90 |
|---|---|---|---|---|---|---|---|
| baseline < 04.00 | 327 | 0.207 % | 0.082 % | 0.684 % | 2.44 % | 0.80 % | 6.90 % |
| baseline ≥ 04.00 | 372 | 0.032 % | 0.001 % | 0.084 % | 1.06 % | 0.27 % | 3.24 % |
| all | 699 | 0.114 % | 0.011 % | 0.416 % | 1.70 % | 0.36 % | 5.76 % |

The same excluding the seven acquisitions with no data over the AOI (`valid_frac` = 0; they are all in the < 04.00 era):

| era | n | class 2 mean | class 2 median | class 2 p90 | class 7 mean | class 7 median | class 7 p90 |
|---|---|---|---|---|---|---|---|
| baseline < 04.00 | 320 | 0.211 % | 0.093 % | 0.688 % | 2.49 % | 0.84 % | 6.92 % |
| baseline ≥ 04.00 | 372 | 0.032 % | 0.001 % | 0.084 % | 1.06 % | 0.27 % | 3.24 % |
| all with data | 692 | 0.115 % | 0.011 % | 0.419 % | 1.72 % | 0.38 % | 5.81 % |

Mean AOI share per SCL class over all 699 acquisitions: 0 no-data 1.05 %, 1 0.12 %, 2 0.11 %, 3 2.48 %, 4 34.32 %, 5 2.79 %,
6 0.005 %, 7 1.70 %, 8 14.14 %, 9 35.35 %, 10 7.92 %, 11 0 %. Mean clear fraction by keep-set: `clear_base` 0.3712,
`clear_keep2` 0.3723, `clear_keep7` 0.3882, `clear_keep27` 0.3894. Keeping class 2 therefore adds 0.11 percentage points and
keeping class 7 adds 1.70 on this AOI. Excluding the seven empty acquisitions (n = 692): mean share per class 0 0.05 %,
1 0.12 %, 2 0.115 %, 3 2.51 %, 4 34.67 %, 5 2.82 %, 6 0.005 %, 7 1.72 %, 8 14.28 %, 9 35.71 %, 10 8.00 %, 11 0 %; mean clear
fraction `clear_base` 0.3749, `clear_keep2` 0.3761, `clear_keep7` 0.3922, `clear_keep27` 0.3933 (class 2 adds 0.12 and class 7
adds 1.73 percentage points). The audit measures how much each class removes; it does not validate SCL, so
whether class 2 or 7 pixels here are shadowed forest or misclassified cloud is not known from this data.

## Consequences

- The class-2 share is about six times higher for baselines < 04.00 (mean 0.207 % vs 0.032 %). Source for the meaning of
  class 2: ESA's PSD 15.0 / baseline 05.11 renamed class 2 from DARK_FEATURES to CAST_SHADOW
  (https://dataspace.copernicus.eu/news/2024-7-18-sentinel-2-product-specification-document-psd-version-150), and the
  computation has been unchanged since 04.00; before 04.00, class 2 also held dark soils and burn scars, which later moved to
  class 7. CLAUDE.md's "renamed in 05.11" and this "changed algorithm at 04.00" are both true: the name changed at 05.11, the
  content at 04.00. The era split is confounded with date and sun geometry (older scenes are also earlier in the record and
  have different sun angles), so the difference is consistent with the algorithm change but does not prove it.
- SCL class 2 is cast shadow only. It does not capture self-shading or low illumination on slopes, so the 0.11 % figure does
  not measure terrain shading; the M1.3 terrain-shadow flag is still required. Masking class 2 is a small effect here;
  M1.2's ADR decides, using these shares.
- Both sensors are fully covered by one tile / one path-row. Nothing is hard-coded: tiles are read from the search.
- `n_ok` in window tables includes no-data acquisitions; use `n_with_data` as the scene count.
- The stored 12 SCL shares let any other keep-set be recomputed without re-reading. A change of AOI invalidates the cache
  (AOI hash), as does a change of grid or item ids.
- Landsat acquisitions in PC for this path/row start on 1988-01-14; nothing earlier was returned by the search (reason not
  investigated).
- Landsat tiers, cirrus handling and the keep-sets are carried forward as options, not decisions.
