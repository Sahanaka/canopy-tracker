"""Network smoke tests for the Copernicus DEM loader and the MTD_TL sun-angle grid."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from highland import stac
from highland import terrain as T
from highland.aoi import default_aoi_path, load_aoi

pytestmark = pytest.mark.network

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def aoi():
    path = default_aoi_path()
    if not path.exists():
        pytest.skip(f"AOI file {path} not found")
    return load_aoi(path)


@pytest.fixture(scope="module")
def s2_items(aoi):
    items, _ = stac.dedupe_s2(
        stac.search_items(stac.S2_COLLECTION, aoi, "2023-06-01", "2023-06-12")
    )
    assert items
    return items


@pytest.fixture(scope="module")
def fine(aoi, s2_items):
    return stac.aoi_geobox(aoi, s2_items[:1], "s2", asset="B02", resolution=10.0)


@pytest.fixture(scope="module")
def coarse(fine):
    return T.nested_geobox(fine, 3, buffer_m=1200.0)


@pytest.fixture(scope="module")
def dem(aoi, coarse):
    return T.load_cop_dem(aoi, coarse)


def test_dem_grid_and_values(dem, coarse, fine) -> None:
    assert dem.dtype == np.float32 and dem.shape == tuple(coarse.shape)
    assert np.isfinite(dem.values).all()
    assert str(coarse.crs) == "EPSG:32644" and coarse.transform.a == 30.0
    nb = T.buffer_cells(1200.0)
    assert coarse.transform.c + nb * 30.0 == fine.transform.c
    assert coarse.transform.f - nb * 30.0 == fine.transform.f
    meta = sorted((ROOT / "outputs" / "M1.2" / "meta").glob("s2_*.json"))
    if meta:  # M1.2 outputs are git-ignored; compare when they exist locally
        tr = json.loads(meta[0].read_text())["params"]["transform"]
        assert (fine.transform.c, fine.transform.f) == (tr[2], tr[5])
    assert 500.0 < float(dem.min()) < float(dem.max()) < 2600.0  # Sri Lanka highlands, m


def test_dem_equals_rasterio_reproject(dem, coarse) -> None:
    import planetary_computer
    import rasterio
    from rasterio.warp import Resampling, reproject

    items = dem.attrs["items"].split(",")
    cat = stac.open_client()
    out = np.full(dem.shape, np.nan, dtype=np.float32)
    for iid in items:
        it = planetary_computer.sign(cat.get_collection(T.DEM_COLLECTION).get_item(iid))
        with rasterio.open(it.assets["data"].href) as src:
            tmp = np.full(dem.shape, np.nan, dtype=np.float32)
            reproject(
                rasterio.band(src, 1),
                tmp,
                dst_transform=coarse.transform,
                dst_crs="EPSG:32644",
                dst_nodata=np.nan,
                resampling=Resampling.bilinear,
            )
        out = np.where(np.isnan(out), tmp, out)
    xs = coarse.transform.c + 30.0 * (np.arange(dem.shape[1]) + 0.5)
    away_from_seam = np.abs(xs - 500000.0) > 100.0  # tile edge: one-pixel differences allowed
    # odc-stac and a direct rasterio reproject differ by <= 0.07 m (measured; GDAL's warp approximates
    # the coordinate transform per row); 0.1 m is far below the DEM's 2-4 m relative accuracy.
    np.testing.assert_allclose(dem.values[:, away_from_seam], out[:, away_from_seam], atol=0.1)


def test_mtd_tl_mean_angle_equals_item(s2_items) -> None:
    import planetary_computer
    import requests

    it = s2_items[0]
    href = planetary_computer.sign(it).assets["granule-metadata"].href
    resp = requests.get(href, timeout=60)
    resp.raise_for_status()
    grid = T.parse_sun_grid(resp.content)
    assert grid["mean_zenith"] == pytest.approx(it.properties["s2:mean_solar_zenith"], abs=1e-6)
    assert grid["mean_azimuth"] == pytest.approx(it.properties["s2:mean_solar_azimuth"], abs=1e-6)
