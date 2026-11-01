# QField field form

This describes the form you fill in on the phone and the GeoJSON file it produces. Field points are used to check the
disturbance map (M1.9) and to train the class map (M2.3). **Every value is what you saw; if you are not sure, say so
(`unsure`) rather than guess.**

## Rules to remember in the field

1. Record what is on the ground now in `class`, and whether you see signs of removal in `opened` (stumps, slash,
   fresh gaps). A stand opened years ago that is now grass is `class = grassland`, `opened = yes`.
2. Stand where you measure and wait for the GPS to settle. Points you place by hand on the map are allowed, but the
   form records them as `position_source = manual`.
3. **Privacy:** do not record homes or people. Points on private land go in the `private` layer (see below), never in
   the public file. Photos stay out of git.
4. Observer is initials or a handle, never a full name.

## Layer setup in QGIS (once, on the computer)

1. New GeoPackage layer, geometry Point, CRS EPSG:4326 (WGS 84), with the fields in the table below.
2. Set the widgets as listed (Value Map shows a label, stores the code). For `photo_1`..`photo_3` use the Attachment
   widget, storage "relative to project path", and keep the pictures in a `photos/` folder next to the project.
3. Make a second copy of the layer for private land (same fields); export it only into `data/field/private/`.
4. Pack the project for QField with QFieldSync (or QFieldCloud), copy to the phone, and test one point before a trip.

## Fields

Property names are the contract; the loader rejects anything marked "required" that is empty.

| field | type | required | QField widget / default | allowed values |
|---|---|---|---|---|
| `point_id` | text | yes | Text, default `uuid('WithoutBraces')`, not editable | unique across all files |
| `observed_at` | text | yes | Text, default `replace(format_date(now(), 'yyyy-MM-dd HH:mm:ss'), ' ', 'T') \|\| '+05:30'` | ISO 8601 **with offset**, e.g. `2026-05-01T10:15:00+05:30`; not in the future |
| `observer` | text | yes | Text, default `@cloud_username`, editable | initials/handle only |
| `class` | text | yes | Value Map | `pine`, `turpentine`, `eucalyptus`, `native_broadleaf`, `alstonia`, `tea`, `grassland`, `settlement`, `other`, `unsure` |
| `secondary_class` | text | no | Value Map | same list, or empty (e.g. `alstonia` under opened pine) |
| `opened` | text | yes | Value Map | `yes`, `no`, `unsure`: canopy recently opened at this point |
| `patch_id` | text | no | Text | id of a mapped disturbance patch if you were visiting one (M1.7) |
| `canopy_open_pct` | integer | yes | Range 0-100 | 0-100 = 100 minus vertical canopy cover |
| `openness_method` | text | yes | Value Map, default `visual` | `visual`, `glama` |
| `photo_1`, `photo_2`, `photo_3` | text | no | Attachment, relative to project | `photos/<name>.jpg` (`.jpg`, `.jpeg`, `.png`), `/` separators, no `..` |
| `gps_accuracy_m` | real | no | default `@position_horizontal_accuracy` | number >= 0 (metres) or empty |
| `position_source` | text | no | default `@position_source_name` | free text; `manual` means placed by hand |
| `notes` | text | no | Text (multiline) | free text (put disturbance type here: felled, thinned, burnt, landslide) |

Class names: `alstonia` is *Alstonia macrophylla*, an **invasive alien** in Sri Lanka that colonises opened pine
stands. `turpentine` is *Syncarpia glomulifera*. `tea`, `grassland`, `settlement` and `other` are all "other" for the
class map (`highland.io.OTHER_GROUP`); they are kept apart because tea and grass confuse the disturbance map.

Notes on the entries:

- **Canopy openness** is a visual estimate and is coarse and observer-dependent: round to the nearest 5-10 %. If you
  take a hemispherical photo and compute cover with GLAMA (Tichy 2016, doi:10.1111/jvs.12350), set
  `openness_method = glama`. Smartphone methods differ a lot from each other, so visual and GLAMA values are not pooled
  without a check, and no openness classes are defined here.
- **Time:** the default stamps the phone's local clock, so keep the phone on Sri Lanka time. Times without an offset
  are rejected (not guessed); the loader converts to UTC.
- **GPS accuracy:** recorded, never filtered here. Any accuracy cut-off for analysis will be decided later in an ADR.
- **Photos:** QField's default picture folder and file name vary by version and device, so after the first point check
  that the exported `photo_1` value looks like `photos/....jpg`. Copy the picture files into `data/field/photos/`
  (git-ignored) if you want the loader's `photo_root` check.

## Export to GeoJSON

In QGIS: right-click the layer, Export, Save Features As, format GeoJSON, CRS EPSG:4326, layer option `RFC7946=YES`.
Save public points as `data/field/<date>-<name>.geojson` and private-land points into `data/field/private/`.

## Check the file

```
python -m highland.io data/field/<file>.geojson
```

It prints the counts by class, or every problem found at once (file, point, rule, value). It never edits the file. In
Python: `highland.io.load_field_points()` returns points in EPSG:4326 with times in UTC; reproject with
`highland.aoi.to_utm` for metre work. Points must lie inside the AOI polygon (`data/aoi/aoi.geojson`); until that file
exists the Sri Lanka bounding box is used and a warning is logged.
