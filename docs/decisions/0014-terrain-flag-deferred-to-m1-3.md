# 0014 — Terrain-shadow flag deferred to M1.3; M1.2 stores sun geometry and per-scene clear masks

- **Status:** accepted by the owner on 2026-11-03 (recommendation D4 approved 2026-11-02)
- **Date:** 2026-11-02
- **Task:** M1.2

## Context

CLAUDE.md requires a terrain-shadow / low-illumination flag, computed per scene from the DEM and the scene's sun geometry
and aggregated per composite (for example the share of a pixel's clear observations that were in shadow), with results
reported with and without it. The DEM and its derivatives are M1.3. M1.2 builds the composites that the flag has to be
aggregated over. Applying one sun angle to a multi-date composite is not allowed.

## Decision

- M1.2 does **not** compute or apply a terrain flag. Its outputs and README say so, and the GeoTIFF tags carry
  `terrain_flag = not applied (M1.3)`.
- M1.2 stores what M1.3 needs to compute the flag per scene and aggregate it per composite without re-reading any imagery:
  - `outputs/M1.2/scenes_used.csv`: per scene the processing baseline, `s2:mean_solar_azimuth` and the solar elevation
    (90 minus `s2:mean_solar_zenith`), local date, window day, clear fraction and the AOI shares of SCL classes 0, 2 and 7;
  - `outputs/M1.2/clear_masks/s2_<season-year>_<WIN>.npz`: the bit-packed per-scene 10 m valid-observation masks (the same
    masks that define `n_obs`), the acquisition ids and the sun angles, in the same scene order.
- M1.3 computes a per-scene shadow mask from the DEM and these angles, then the per-pixel share of valid observations that
  were shadowed in each composite. Composites are then reported with and without that flag.
- Until then, SCL class 2 is *not* treated as the terrain flag: it marks cast shadow only (ADR 0010), is kept as clear in
  M1.2 (ADR 0012), and does not capture self-shading of slopes facing away from the sun.

## Consequences

- Composites from M1.2 are unflagged: shaded slopes read lower NDVI/NBR than sunlit ones. Anything built on them before M1.3
  (including the M1.2 maps) must not interpret spatial differences on steep terrain as vegetation differences.
- The per-scene masks are about 50-100 kB per window-year (bit-packed, compressed), so storing them is cheap.
- The shadow share can only be as good as the stored masks: they are valid-observation masks (SCL clear, all bands non-zero)
  at 10 m on the M1.2 grid. Changing the grid, the AOI or the SCL keep-set invalidates them (resume keys catch this).
- Revisit if M1.3 finds it needs per-scene reflectance (for example a band-ratio illumination correction): that would
  require rereading imagery, and the composites would have to be rerun.

## Amendment (M1.3, 2026-11-03)

The original text above is unchanged. One correction, one confirmation and one link.

- **What the stored masks are.** The "Consequences" line calls them "SCL clear, all bands non-zero". More exactly: a pixel is a valid observation when its SCL class is in the ADR 0012 keep-set {2, 4, 5, 6}
  and all seven reflectance bands are non-zero. For the 20 m bands the non-zero test is valid only because source nodata 0 is forced for every asset (ADR 0013); ADR 0013 shows that the 43 mask files were byte-identical after that fix.
  Class 2 is therefore inside the valid set, so shaded observations are in the denominator of the M1.3 shadow share.
- **Size.** The "about 50-100 kB per window-year" estimate holds: the 43 `clear_masks/*.npz` total 2.8 MB, about 65 kB each.
- **Implemented.** M1.3 computed the flag from these stored masks and angles without rereading any imagery; the angles are the tile means from `scenes_used.csv` (checked against the MTD_TL grids, ADR 0016). The SCL class 2 statement in "Decision" stays true:
  class 2 was kept as clear, and ADR 0016 reports how it relates to the modelled shadow. See ADR 0015 (DEM, grid, derivatives) and ADR 0016 (flag).

