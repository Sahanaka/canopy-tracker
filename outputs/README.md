# outputs/

Generated rasters and vectors. Everything here except this file is git-ignored.

Each task writes to `outputs/<task-id>/` (e.g. `outputs/M1.2/`) and puts a short `README.md` there saying
how the files were produced (inputs, code version, parameters). The web viewer is built from this
directory only, by `highland.viewer.build()`.

## Reproducibility rule

Every `outputs/<task-id>/README.md` must record the resolved environment: save the output of
`micromamba env export --explicit` (or `conda list --explicit`) alongside the outputs, and state at least the
rasterio, GDAL, PROJ and odc-* versions used.
