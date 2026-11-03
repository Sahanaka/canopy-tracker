# 0008 — Field data schema

- **Status:** accepted
- **Date:** 2026-11-01
- **Task:** M0.4

## Context

Field points from QField feed M1.9 (mapped vs observed disturbance) and M2.3 (Random Forest labels). The loader
`highland.io.load_field_points()` needs a fixed schema. No satellite data is involved. This ADR defines vocabulary and
value ranges only: **no GPS-accuracy cut-off and no openness class breaks are chosen** (any such filter needs its own
ADR once there is data). Sources are listed in `docs/plans/M0.4.md`.

## Decision

- **D1 Format:** GeoJSON only, exported from QGIS (EPSG:4326, `RFC7946=YES`); text diffs review well in a public
  repo. Reading is OGR-based, so GeoPackage can be added later without a schema change.
- **D2 Classes:** `class` is the dominant upper-canopy cover type of the homogeneous area around the point (not one tree); `secondary_class` is a second upper-canopy component (not understorey, not `unsure`, not equal to `class`). `pine`, `turpentine`, `eucalyptus`, `native_broadleaf`, `alstonia`, `tea`, `grassland`,
  `settlement`, `other`, `unsure`, plus optional `secondary_class`. `OTHER_GROUP` (tea, grassland, settlement, other)
  collapses to the Module B class "other" so the split stays reversible.
- **D3 Disturbance status:** `opened` is yes/no/unsure (required): evidence of canopy removal at the point, of any age; optional whole-number `opened_years_ago` (observer estimate, >= 0, only when `opened` is yes); `patch_id` optional free text. Disturbance type goes in
  `notes` until M1.10 shows it is needed.
- **D4 Canopy openness:** integer `canopy_open_pct` 0-100 (100 minus vertical cover), a coarse visual estimate;
  `openness_method` is `visual` (estimated % of sky not blocked by canopy looking straight up), `glama` (hemispherical canopy openness as reported by GLAMA, angle-weighted, a different quantity) or `not_measured` (then `canopy_open_pct` must be empty, otherwise it is required). Methods are never compared or pooled.
- **D4b Unsure:** `class = unsure` and `opened = unsure` are excluded from training and accuracy by default and reported as separate counts; `highland.io.REFERENCE_CLASSES` is `FIELD_CLASSES` without `unsure`.
- **D5 Time:** local time with an explicit offset (`+05:30`), returned as UTC; naive and future values are rejected.
  Python `datetime.fromisoformat` parses it. Probe (GDAL 3.13.3, pyogrio 0.13.0): GeoJSON text with an offset is
  preserved on write and read when read as strings (`datetime_as_string=True`), which the loader uses so mixed
  offsets and `Z` cannot degrade the column. Optional `gps_time_utc` (default `@position_timestamp`, empty for hand-placed points) cross-checks the phone clock; a wrong phone clock is a known risk, and `observed_at` must not use "Apply default value on update".
- **D6 Photos:** `photo_1`..`photo_3`, relative paths with `/`, no `..`, extensions `.jpg/.jpeg/.png`; files live in
  git-ignored `data/field/photos/` (EXIF GPS can expose locations; size).
- **D7 Privacy:** points on private land go into git-ignored `data/field/private/*.geojson` (same schema), loaded by
  default but never committed or shown in the viewer. The loader returns a boolean `private` column (True for files under `data/field/private/`) and `source_file` relative to `data/field/`; **viewer and export code must drop `private == True` rows.** Observer is initials or a handle.
- **D8 Bounds and errors:** points must lie inside the AOI polygon if `data/aoi/aoi.geojson` exists, else inside
  Sri Lanka's bounding box (ADR 0007) with a WARNING. All row problems are collected into one `FieldDataError`; only
  unreadable files and a wrong CRS stop that file early (other files are still checked).

## Consequences

- The owner sees every problem in one run (`python -m highland.io <file>`), naming file, point, rule and value.
- Rules are checked by synthetic fixtures in `tests/fixtures/field/`; `tests/test_field_files.py` guards committed
  real files and skips while there are none.
- Unverified until a real device is used: the QField default photo file name, the `format_date` default for
  `observed_at` (syntax checked against the QGIS 3.44 function list only), and the text format QGIS writes for
  `@position_timestamp` into `gps_time_utc` (if it has no offset, every row is rejected; check on the first trip).
- `gps_time_utc` is stored only; comparing it with `observed_at` needs a tolerance in its own ADR (M1.9). Revisit D5/D6 after the first trip.
- Revisit D2 if the Random Forest needs classes the form cannot give, and D4 if visual estimates prove too noisy.
- Patch-visit sampling (M1.9) measures commission error only and is road-side biased; omission needs `opened = no`
  points outside mapped patches. `patch_id` is free text that M1.9 must check against the M1.7 patch ids.
- Reviewer-flagged open decisions, none set here and each needing a source or data in its own ADR:
  - minimum homogeneous area around a point, in pixels (M1.9/M2.3);
  - canopy-share rule that defines "dominant" for `class` (M1.9/M2.3);
  - how field `opened` and `opened_years_ago` map to the disturbance-year analysis period (M1.9);
  - GPS-accuracy cut-off and the policy for hand-placed points with empty `gps_accuracy_m` (M1.9); sensor accuracy
    can be optimistic under canopy and on steep slopes.
