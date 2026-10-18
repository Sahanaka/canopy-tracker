# 0001 — Data platform: Planetary Computer, processed locally

- **Status:** accepted
- **Date:** 2026-10-12
- **Task:** M0

## Context

Google Earth Engine requires a Google Cloud project, which the owner prefers to avoid. The area of interest is small (~10 × 10 km), so cloud-scale compute is not needed.

## Decision

Read Sentinel-1, Sentinel-2, Landsat Collection 2, Copernicus DEM GLO-30 and ESA WorldCover from the Microsoft Planetary Computer STAC API, signed with `planetary-computer`. Process locally with `odc-stac` + `xarray`. Use AWS Earth Search as the fallback for Sentinel-2.

## Consequences

- No Cloud Score+: Sentinel-2 cloud masking uses the SCL band.
- No LandTrendr: disturbance detection is implemented in Python (simple max-drop first, BFAST-style later).
- Everything is reproducible on a laptop with no account. Revisit if the AOI grows beyond what fits in memory, or if SCL masking proves too leaky.
