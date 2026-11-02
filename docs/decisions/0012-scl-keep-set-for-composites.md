# 0012 — SCL keep-set for the M1.2 composites: keep class 2, mask class 7

- **Status:** proposed (owner approved recommendation D1 on 2026-11-02; ADR awaits owner sign-off)
- **Date:** 2026-11-02
- **Task:** M1.2

## Context

CLAUDE.md leaves two Sentinel-2 SCL classes open: class 2 (cast / topographic shadow, called "dark area pixels" before
baseline 05.11) and class 7 ("unclassified"). Classes 4, 5, 6 are always kept and 0, 1, 3, 8, 9, 10, 11 always masked.
ADR 0010 measured, over the AOI and the 699 deduplicated acquisitions, how much each class removes; it did not validate SCL
against anything. The numbers below are from ADR 0010, not from new analysis.

| class | share of AOI pixels removed (mean over acquisitions with data, n = 692) | median | p90 |
|---|---|---|---|
| 2, baseline < 04.00 | 0.211 % | 0.093 % | 0.688 % |
| 2, baseline >= 04.00 | 0.032 % | 0.001 % | 0.084 % |
| 2, all | 0.115 % | 0.011 % | 0.419 % |
| 7, baseline < 04.00 | 2.49 % | 0.84 % | 6.92 % |
| 7, baseline >= 04.00 | 1.06 % | 0.27 % | 3.24 % |
| 7, all | 1.72 % | 0.38 % | 5.81 % |

Mean clear fraction by keep-set (n = 692): base (4, 5, 6) 0.3749; plus class 2 0.3761; plus class 7 0.3922; plus both 0.3933.
Class 2 therefore changes the clear fraction by 0.12 percentage points and class 7 by 1.73 points.

## Decision

- **Keep class 2, mask class 7.** The clear set is SCL {2, 4, 5, 6}. In code: `masking.SCL_KEEP_M12`, and
  `scl_clear(scl, keep_cast_shadow=True, keep_unclassified=False)`.
- A pixel is additionally a valid observation only if all seven reflectance bands are non-zero (DN 0 is no-data). This holds
  for the 20 m bands only because `stac.load_s2_acquisition` forces source nodata 0 for every asset: some files (the 02.12
  files, some 05.13 files, S2C 2026-09-08) declare no nodata, and without the override GDAL turns DN 0 into 1 in the warp, which
  the offset then maps to a small negative reflectance that bilinear resampling blends into valid neighbours.
- Both classes stay configurable (`keep=(keep_cast_shadow, keep_unclassified)` in `composites.composite_window`). The keep
  flags are part of the resume key and are written into the GeoTIFF tags, so a change recomputes the composites.
- The share of AOI pixels that classes 2 and 7 remove is logged for every scene (`outputs/M1.2/scenes_used.csv`,
  `scl_share_2`, `scl_share_7`) and averaged per composite (`composite_summary.csv`, `scl2_share_mean`, `scl7_share_mean`).

## Reasoning

- Class 2 marks cast shadow on real surfaces. On these slopes masking it would remove shaded hillsides from every composite
  in which they occur, biasing the record against them. That is the job of the terrain flag (ADR 0014, M1.3), which handles
  illumination explicitly and reports results with and without it. Before baseline 04.00 class 2 also held dark soil and
  burn scars (ADR 0010), which are the signal Module A looks for. Keeping it costs 0.12 points of clear fraction either way.
- Class 7 is "unclassified" in ESA's own wording and the data say it is not rare (1.72 % of AOI pixels, p90 5.81 %). SCL is
  known to leak cloud edges (ADR 0011, the 2024-10-23 evidence), and nothing in this data shows that class 7 pixels are
  usable surface. Masking it is the cautious choice at a cost of about 1.7 percentage points of observations.

## Consequences

- This is **not validated**. Whether class 2 pixels here are shaded forest or misclassified cloud, and whether class 7 pixels are
  usable, is unknown. The decision is a documented assumption; revisit it if field checks or the M1.3 shadow analysis show that
  class 2 is mostly cloud shadow in this AOI (then mask it) or that class 7 is mostly clear forest (then keep it).
- The ADR 0011 tables use `clear_base` (class 2 masked). M1.2 keeps class 2, so its observation counts are slightly higher
  than the tables (the ADR 0010 mean difference is 0.12 percentage points of the clear fraction); the thin-flag list in
  ADR 0013 is derived from the M1.2 counts, not copied from ADR 0011.
- The era effect is a possible confound: class 2 and 7 shares are 2-6 times larger before baseline 04.00 than after, so a
  pre/post-2021 step in the masked share could look like change. The per-scene shares are stored to check this.
