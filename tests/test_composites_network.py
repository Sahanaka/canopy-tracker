"""Network smoke test of the M1.2 loader against Planetary Computer (skips without an AOI)."""

import os
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.windows import from_bounds

from highland import composites, stac
from highland.aoi import default_aoi_path, load_aoi
from highland.masking import s2_dn_to_reflectance

pytestmark = pytest.mark.network


def _aoi_path() -> Path:
    env = os.environ.get("HIGHLAND_TEST_AOI")
    path = Path(env) if env else default_aoi_path()
    if not path.exists():
        pytest.skip(f"AOI file {path} not found (owner adds data/aoi/aoi.geojson)")
    return path


@pytest.mark.parametrize(
    ("start", "end", "baseline_prefix"),
    [("2025-03-01", "2025-03-20", "05."), ("2016-04-10", "2016-04-20", "02.")],
)
def test_loader_returns_raw_dn_and_reflectance_matches_formula(start, end, baseline_prefix) -> None:
    import planetary_computer

    aoi = load_aoi(_aoi_path())
    items, _ = stac.dedupe_s2(stac.search_items(stac.S2_COLLECTION, aoi, start, end))
    items = [
        i for i in items if str(i.properties["s2:processing_baseline"]).startswith(baseline_prefix)
    ]
    assert items, f"no baseline {baseline_prefix}xx item between {start} and {end}"
    members = stac.acquisition_groups(items, "s2")
    key, group = next(iter(members.items()))
    gb = stac.aoi_geobox(aoi, group, "s2", asset="B02", resolution=10.0)

    ds = stac.load_s2_acquisition(group, gb).compute()
    bl = ds.attrs["baseline"]
    assert str(gb.crs) == "EPSG:32644" and abs(gb.transform.a) == 10.0
    for b in ("blue", "green", "red", "nir"):
        assert ds[b].dtype == np.uint16  # raw DN: no automatic scale/offset
    assert ds["scl"].dtype == np.uint8

    # 10 m bands equal a direct rasterio window read (so the offset is applied only once, by us).
    signed = planetary_computer.sign(group[0])
    left, bottom, right, top = gb.boundingbox
    with rasterio.open(signed.assets["B04"].href) as src:
        raw = src.read(
            1, window=from_bounds(left, bottom, right, top, src.transform).round(), boundless=True
        )
    got = ds["red"].isel(time=0).values
    np.testing.assert_array_equal(got, raw)

    refl = s2_dn_to_reflectance(got, bl)
    assert refl.dtype == np.float32
    nz = got > 0
    offset = 1000 if stac.parse_baseline(bl) >= (4, 0) else 0
    np.testing.assert_array_equal(
        refl[nz], ((got[nz].astype(np.float32) - offset) / np.float32(10000))
    )
    assert np.isnan(refl[~nz]).all()

    # The lazy stack for this one scene gives the same red reflectance where valid.
    st = composites.scene_stack({key: group}, gb).compute()
    valid = st["valid"].isel(time=0).values
    np.testing.assert_array_equal(st["red"].isel(time=0).values[valid], refl[valid])


def test_20m_bands_keep_nodata_zero_on_file_without_declared_nodata() -> None:
    """2016-04-26 (02.12, R119) is empty over the AOI and its assets declare no nodata.

    Without a forced source nodata, GDAL turns DN 0 in the 20 m bands into 1 and the offset
    maps that to a small negative reflectance; with it, every band is NaN where B04 is DN 0.
    """
    aoi = load_aoi(_aoi_path())
    items, _ = stac.dedupe_s2(
        stac.search_items(stac.S2_COLLECTION, aoi, "2016-04-26", "2016-04-27")
    )
    assert items, "no S2 item on 2016-04-26 over the AOI"
    key, group = next(iter(stac.acquisition_groups(items, "s2").items()))
    gb = stac.aoi_geobox(aoi, group, "s2", asset="B02", resolution=10.0)
    ds = stac.load_s2_acquisition(group, gb).compute()
    empty = ds["red"].isel(time=0).values == 0
    assert empty.mean() > 0.5, "expected a mostly empty scene over the AOI"
    for band in ("nir08", "swir16", "swir22"):
        dn = ds[band].isel(time=0).values
        assert (dn[empty] == 0).all(), f"{band} DN 0 was altered (0 -> 1) by the warp"
        assert np.isnan(s2_dn_to_reflectance(dn, ds.attrs["baseline"])[empty]).all()
    assert (ds["scl"].isel(time=0).values[empty] == 0).all()
