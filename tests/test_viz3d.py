"""Offline tests for highland.viz3d (array preparation; rendering is skipped without pyvista)."""

from __future__ import annotations

import numpy as np
import pytest
import rioxarray  # noqa: F401
import xarray as xr

from highland import viz3d

CRS = "EPSG:32644"


def make_grid(ny: int, nx: int, res: float, x0: float = 489180.0, y0: float = 758070.0, fill=None):
    x = x0 + res * (np.arange(nx) + 0.5)
    y = y0 - res * (np.arange(ny) + 0.5)
    data = np.arange(ny * nx, dtype=np.float32).reshape(ny, nx) if fill is None else fill
    da = xr.DataArray(data, dims=("y", "x"), coords={"y": y, "x": x})
    return da.rio.write_crs(CRS)


def test_drape_arrays_shapes_and_z() -> None:
    dem = make_grid(20, 30, 30.0) + 1000.0
    dem = dem.rio.write_crs(CRS)
    layer = make_grid(60, 90, 10.0, fill=np.random.default_rng(0).random((60, 90), np.float32))
    a = viz3d.drape_arrays(dem, layer, "continuous", exaggeration=2.0)
    assert a["n_points"] == 600 and a["scalars"].shape == (20, 30)
    assert np.allclose(a["z"], dem.values * 2.0)
    assert a["exaggeration"] == 2.0 and a["crs"].endswith("32644")
    assert a["extent"] == pytest.approx((489180.0, 758070.0 - 600.0, 489180.0 + 900.0, 758070.0))
    assert a["x"].shape == (30,) and a["y"].shape == (20,)


def test_default_exaggeration_is_one() -> None:
    dem = make_grid(5, 6, 30.0)
    assert np.array_equal(viz3d.drape_arrays(dem, dem.copy(), "continuous")["z"], dem.values)


def test_nearest_keeps_class_values() -> None:
    dem = make_grid(20, 30, 30.0)
    classes = np.random.default_rng(1).integers(0, 3, (60, 90)).astype(np.float32) * 2
    a = viz3d.drape_arrays(dem, make_grid(60, 90, 10.0, fill=classes), "categorical")
    assert set(np.unique(a["scalars"])) <= {0.0, 2.0, 4.0}
    b = viz3d.drape_arrays(dem, make_grid(60, 90, 10.0, fill=classes), "continuous")
    assert len(np.unique(b["scalars"])) > 3  # bilinear interpolates


def test_crs_mismatch_is_reprojected_and_missing_raises() -> None:
    dem = make_grid(20, 30, 30.0)
    layer = make_grid(20, 30, 30.0, fill=np.ones((20, 30), np.float32))
    ll = layer.rio.reproject("EPSG:4326")
    a = viz3d.drape_arrays(dem, ll, "continuous")
    assert a["scalars"].shape == (20, 30)
    assert np.nanmean(a["scalars"]) == pytest.approx(1.0, abs=1e-3)
    no_crs = xr.DataArray(layer.values, dims=("y", "x"), coords={"y": layer.y, "x": layer.x})
    with pytest.raises(ValueError, match="no CRS"):
        viz3d.drape_arrays(dem, no_crs, "continuous")
    with pytest.raises(ValueError, match="no CRS"):
        viz3d.drape_arrays(no_crs, layer, "continuous")


def test_rgb_and_validation() -> None:
    dem = make_grid(10, 12, 30.0)
    rgb = xr.concat(
        [make_grid(10, 12, 30.0, fill=np.full((10, 12), v, np.float32)) for v in (1, 2, 3)], "band"
    )
    rgb = rgb.rio.write_crs(CRS)
    a = viz3d.drape_arrays(dem, rgb, "rgb")
    assert a["scalars"].shape == (10, 12, 3) and a["scalars"][0, 0].tolist() == [1.0, 2.0, 3.0]
    with pytest.raises(ValueError):
        viz3d.drape_arrays(dem, dem, "rgb")
    with pytest.raises(ValueError):
        viz3d.drape_arrays(dem, dem, "continuous", exaggeration=0.0)
    with pytest.raises(ValueError):
        viz3d.drape_arrays(dem, dem, "bogus")  # type: ignore[arg-type]


def test_plotter_construction() -> None:
    pytest.importorskip("pyvista")
    dem = make_grid(10, 12, 30.0)
    p = viz3d.drape(dem, dem.copy(), "continuous", off_screen=True)
    try:
        assert p.mesh is not None or len(p.renderer.actors) > 0
    finally:
        p.close()
