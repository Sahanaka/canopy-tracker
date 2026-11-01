# Area of interest (AOI)

`data/aoi/aoi.geojson` is the single polygon (about 10 x 10 km, Bandarawela-Haputale belt, Uva Province) that every
other part of the project clips to. No coordinates are hard-coded anywhere else.

## Rules

- One polygon, no holes (a MultiPolygon with one part is accepted).
- Coordinates in EPSG:4326 (longitude, latitude in degrees); GeoJSON order is longitude first.
- Area 50-200 km² (measured in EPSG:32644), aim for roughly 10 x 10 km.
- Entirely inside Sri Lanka's bounding box (W 79.64, S 5.86, E 81.95, N 9.88).
- Save it as `data/aoi/aoi.geojson`.

## Drawing it

- **geojson.io:** draw one polygon, then Save -> GeoJSON.
- **QGIS:** create a polygon layer, draw one polygon, then Export -> Save Features As -> GeoJSON, CRS EPSG:4326,
  layer option `RFC7946=YES`.
- Before exporting, make sure the layer CRS really is EPSG:4326 (WGS 84), not Kandawala (EPSG:4244) or SLD99
  (EPSG:5233). A file without a `crs` member is read as WGS 84, so a polygon drawn in another datum and saved
  without one would pass validation shifted by hundreds of metres; the validator cannot detect this.

## Validating it

```bash
python -m highland.aoi            # checks data/aoi/aoi.geojson; prints area, bounds and OK, or what to fix
python -m highland.aoi other.geojson
```

When it prints `OK`, commit the file and open a PR. CI then checks the committed file on every run.
