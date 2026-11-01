# outputs/

Generated rasters and vectors. Everything here except this file is git-ignored.

Each task writes to `outputs/<task-id>/` (e.g. `outputs/M1.2/`) and puts a short `README.md` there saying
how the files were produced (inputs, code version, parameters). The web viewer is built from this
directory only, by `highland.viewer.build()`.
