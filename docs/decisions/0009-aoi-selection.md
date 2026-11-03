# 0009 — AOI selection

- **Status:** accepted (provisional; the owner may move or redraw the AOI after checking access and known stands)
- **Date:** 2026-11-02
- **Task:** M0.3

## Context

M0.3 asks the owner to draw a ~10 × 10 km AOI over the Bandarawela–Haputale belt. The owner asked the agent to
propose one from public data instead. Where the AOI sits decides which stands get mapped and where fieldwork
happens, so the method and its limits are recorded here.

## Decision

`data/aoi/aoi.geojson` is candidate **E-30_N+15**: a 10 × 10 km square in EPSG:32644 with corners
(489 185, 748 064) – (499 185, 758 064) m, stored in EPSG:4326 (W 80.90211, S 6.76765, E 80.99262, N 6.85812).
`python -m highland.aoi` reports 100.0 km² and OK.

Method (script: `docs/decisions/0009-aoi-candidates.py`, run in the pinned environment):

1. Town positions from OpenStreetMap Nominatim (2026-11-02): Bandarawela 80.98882 E, 6.83048 N; Haputale
   80.96023 E, 6.76815 N. Their midpoint in EPSG:32644 is the reference.
2. 25 candidate 10 × 10 km squares with centres offset from the midpoint by −3, −1.5, 0, 1.5 and 3 km east and
   north. Kept the 16 that contain both towns (the "belt" in CLAUDE.md).
3. For each: ESA WorldCover 2021 v200 class shares at 10 m (`esa-worldcover`, Planetary Computer), and slope and
   elevation from Copernicus GLO-30 (`cop-dem-glo-30`), reprojected bilinearly to 30 m UTM.

Results for the shortlist:

| Candidate | Tree % | Grass % | Built % | Median slope | Elevation |
|---|---|---|---|---|---|
| **E-30_N+15 (chosen)** | 71.1 | 22.1 | 4.2 | 15.5° | 1 044–1 772 m |
| E-15_N+15 | 72.2 | 21.2 | 4.9 | 15.9° | 845–1 934 m |
| E-15_N-15 | 72.9 | 22.3 | 3.8 | 17.1° | 458–1 934 m |
| E+0_N+0 | 68.8 | 24.9 | 5.2 | 17.2° | 610–1 950 m |

Tree cover is 67–76 % in all 25 squares and does not discriminate: WorldCover's tree class includes tea shade
trees, eucalyptus and pine alike. The candidates differ mainly in elevation: squares reaching south of Haputale run
down the escarpment to 270–600 m, into lowland vegetation outside the montane belt. E-30_N+15 has the highest floor
of the squares containing both towns, so the analysis stays on the montane plateau, which matches the mission
(montane forest restoration). No numeric score or weighting was used; the choice is a stated judgement.

## Consequences

- The steep south-facing Haputale escarpment is mostly outside the AOI. Module C (slope and watershed risk) loses its
  most extreme slopes; revisit if Module C needs them (shift the square south, e.g. E-15_N-15).
- Haputale town lies about 100 m inside the southern edge.
- Road and track access was not scored: bulk OpenStreetMap services (Overpass, Geofabrik) were unreachable from the
  agent's environment and the OSM editing API is not for bulk reads. The owner checks access on openstreetmap.org
  before M1.10 fieldwork.
- Whether the AOI covers known pine or turpentine stands is unverified until the owner checks it or M2 maps classes.
- Moving the AOI later is cheap: re-run M1.1 onward; the audit cache invalidates on an AOI change (AOI hash).
