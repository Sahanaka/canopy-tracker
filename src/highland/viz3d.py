"""PyVista drape of rasters over the DEM for notebook QA views (viewing, not measuring).

3D is for viewing, never for measuring: every number comes from the 2D outputs. Every view
states its vertical exaggeration (default 1x). pyvista is imported lazily (ADR 0005); the
array preparation in `drape_arrays` is numpy/xarray only and is what the CI tests cover.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import xarray as xr

if TYPE_CHECKING:  # pragma: no cover
    import pyvista as pv

Kind = Literal["continuous", "categorical", "rgb"]
OKABE_ITO = ["#E69F00", "#56B4E9", "#009E73", "#F0E442", "#0072B2", "#D55E00", "#CC79A7", "#000000"]
NOTE = "3D is for viewing, not measuring"


def _check_crs(da: xr.DataArray, name: str) -> Any:
    import rioxarray  # noqa: F401  (registers the .rio accessor)

    crs = da.rio.crs
    if crs is None:
        raise ValueError(f"{name} has no CRS; write one with rio.write_crs before draping.")
    return crs


def drape_arrays(
    dem: xr.DataArray, layer: xr.DataArray, kind: Kind, exaggeration: float = 1.0
) -> dict[str, Any]:
    """Return mesh arrays for draping `layer` on `dem`: x, y (m), z (m x exaggeration), scalars.

    The layer is reprojected/resampled onto the DEM's grid first (bilinear for 'continuous' and
    'rgb', nearest for 'categorical'), so both share the same pixels. `dem` is (y, x) in m with a
    CRS; `layer` is (y, x) or, for 'rgb', (band=3, y, x). exaggeration > 0, default 1 (none).
    """
    if kind not in ("continuous", "categorical", "rgb"):
        raise ValueError(f"unknown kind {kind!r}")
    if exaggeration <= 0:
        raise ValueError("exaggeration must be > 0.")
    if dem.ndim != 2:
        raise ValueError("dem must be 2-D (y, x).")
    dem_crs = _check_crs(dem, "dem")
    _check_crs(layer, "layer")
    if kind == "rgb" and (layer.ndim != 3 or layer.shape[0] != 3):
        raise ValueError("rgb layer must have shape (3, y, x).")
    if kind != "rgb" and layer.ndim != 2:
        raise ValueError("continuous/categorical layer must be 2-D (y, x).")
    from rasterio.enums import Resampling

    method = Resampling.nearest if kind == "categorical" else Resampling.bilinear
    same = (
        layer.shape[-2:] == dem.shape
        and layer.rio.crs == dem_crs
        and np.allclose(layer["x"], dem["x"])
        and np.allclose(layer["y"], dem["y"])
    )
    lay = layer if same else layer.rio.reproject_match(dem, resampling=method)
    vals = np.asarray(lay.values, dtype=np.float32)
    if kind == "rgb":
        vals = np.moveaxis(vals, 0, -1)  # (y, x, 3)
    z = np.asarray(dem.values, dtype=np.float64)
    x, y = np.asarray(dem["x"].values, np.float64), np.asarray(dem["y"].values, np.float64)
    half_x, half_y = abs(float(x[1] - x[0])) / 2, abs(float(y[1] - y[0])) / 2
    return {
        "x": x,
        "y": y,
        "z": z * exaggeration,
        "scalars": vals,
        "kind": kind,
        "exaggeration": float(exaggeration),
        "extent": (
            float(x.min() - half_x),
            float(y.min() - half_y),
            float(x.max() + half_x),
            float(y.max() + half_y),
        ),
        "crs": str(dem_crs),
        "n_points": int(z.size),
    }


def _rgb_u8(
    rgb: np.ndarray, lo: float = 2.0, hi: float = 98.0
) -> tuple[np.ndarray, tuple[float, float]]:
    """Stretch float RGB (y, x, 3) to uint8 with one 2-98 percentile range for all bands."""
    finite = rgb[np.isfinite(rgb)]
    a, b = (np.percentile(finite, [lo, hi]) if finite.size else (0.0, 1.0)).tolist()
    scaled = np.clip((np.nan_to_num(rgb, nan=a) - a) / max(b - a, 1e-9), 0, 1)
    return (scaled * 255).astype(np.uint8), (float(a), float(b))


def drape(
    dem: xr.DataArray,
    layer: xr.DataArray,
    kind: Kind = "continuous",
    exaggeration: float = 1.0,
    cmap: str = "cividis",
    title: str = "",
    off_screen: bool = False,
    screenshot: str | None = None,
    window_size: tuple[int, int] = (1100, 800),
) -> pv.Plotter:
    """Drape `layer` on `dem` and return a PyVista Plotter (z in m x exaggeration; default 1x).

    The view always prints the vertical exaggeration and that 3D is not for measuring. With
    `screenshot` (a PNG path) the scene is rendered and saved; off-screen rendering needs a
    display or a wrapper such as `xvfb-run -a` (ADR 0005). Call `.show()` for the notebook view.
    """
    import pyvista as pv

    a = drape_arrays(dem, layer, kind, exaggeration)
    x, y, z = a["x"], a["y"], a["z"]
    xx, yy = np.meshgrid(x, y, indexing="ij")
    grid = pv.StructuredGrid(xx, yy, z.T)
    sc = a["scalars"]
    plotter = pv.Plotter(off_screen=off_screen, window_size=list(window_size))
    note = f"Vertical exaggeration: {exaggeration:g}x. {NOTE}"
    if kind == "rgb":
        u8, (lo, hi) = _rgb_u8(sc)
        grid.point_data["rgb"] = u8.reshape(-1, 3)
        plotter.add_mesh(grid, scalars="rgb", rgb=True)
        note += f"\nRGB stretch: one 2-98 percentile range, {lo:.3f}-{hi:.3f}"
    else:
        grid.point_data["layer"] = sc.ravel()
        if kind == "categorical":
            classes = np.unique(sc[np.isfinite(sc)])
            colors = [OKABE_ITO[i % len(OKABE_ITO)] for i in range(len(classes))]
            plotter.add_mesh(
                grid,
                scalars="layer",
                cmap=colors,
                categories=True,
                scalar_bar_args={"title": title or "class"},
            )
        else:
            plotter.add_mesh(
                grid, scalars="layer", cmap=cmap, nan_color="lightgrey",
                scalar_bar_args={"title": title or "value"},
            )  # fmt: skip
    plotter.add_text(note, position="upper_left", font_size=9)
    if title:
        plotter.add_text(title, position="lower_left", font_size=9)
    plotter.camera_position = "iso"
    if screenshot:
        plotter.screenshot(str(screenshot))
    return plotter
