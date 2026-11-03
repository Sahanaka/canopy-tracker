"""Unit tests for highland.composites and io.write_cog on synthetic data (no network)."""

from datetime import UTC, datetime

import geopandas as gpd
import numpy as np
import pystac
import pytest
import rasterio
import xarray as xr
from affine import Affine
from odc.geo.geobox import GeoBox
from shapely.geometry import box

from highland import composites as C
from highland.indices import ndvi
from highland.io import write_cog
from highland.stac import S2_BANDS

CRS = "EPSG:32644"
GB = GeoBox((6, 6), Affine(10, 0, 500000.0, 0, -10, 700060.0), CRS)


def _item(dt: datetime, baseline: str = "05.10", tile: str = "44NMN") -> pystac.Item:
    return pystac.Item(
        id=f"S2B_MSIL2A_{dt:%Y%m%dT%H%M%S}_R076_T{tile}_20240101T000000",
        geometry=None,
        bbox=None,
        datetime=dt,
        properties={
            "s2:processing_baseline": baseline,
            "s2:mgrs_tile": tile,
            "platform": "Sentinel-2B",
            "s2:mean_solar_azimuth": 90.0,
            "s2:mean_solar_zenith": 30.0,
            "sat:relative_orbit": 76,
        },
    )


# ---- windows ---------------------------------------------------------------------------------


def test_season_year_list_has_43_and_excludes_partial_sim_2026() -> None:
    sy = C.season_years()
    assert len(sy) == 43
    assert ("SIM", 2026) not in sy and ("SWM", 2026) in sy and ("NEM", 2016) in sy


def test_nem_2016_takes_dec_2015_to_feb_2016() -> None:
    dts = [
        datetime(2015, 11, 28, 5, 0, tzinfo=UTC),
        datetime(2015, 12, 3, 5, 0, tzinfo=UTC),
        datetime(2016, 1, 14, 5, 0, tzinfo=UTC),
        datetime(2016, 2, 29, 5, 0, tzinfo=UTC),
        datetime(2016, 3, 4, 5, 0, tzinfo=UTC),
        datetime(2016, 12, 5, 5, 0, tzinfo=UTC),  # belongs to NEM 2017
    ]
    items = [_item(d) for d in dts]
    g = C.window_groups(items, "NEM", 2016)
    assert [k.split("_")[1] for k in g] == ["2015-12-03", "2016-01-14", "2016-02-29"]
    assert [k.split("_")[1] for k in C.window_groups(items, "NEM", 2017)] == ["2016-12-05"]
    assert [k.split("_")[1] for k in C.window_groups(items, "FIM", 2016)] == ["2016-03-04"]


def test_window_uses_local_date_not_utc() -> None:
    # 2016-04-30 20:00 UTC is 2016-05-01 01:30 in Sri Lanka: SWM, not FIM.
    items = [_item(datetime(2016, 4, 30, 20, 0, tzinfo=UTC))]
    assert C.window_groups(items, "FIM", 2016) == {}
    assert len(C.window_groups(items, "SWM", 2016)) == 1


# ---- median / thin / n_obs ------------------------------------------------------------------


def _stack(values: list[float], valid: list[bool] | None = None) -> xr.Dataset:
    """One-pixel-per-scene stack whose red band is `values` (others fixed), NaN where invalid."""
    t = len(values)
    valid_a = np.array(valid if valid is not None else [True] * t)
    shape = (t, 1, 1)
    data: dict[str, tuple] = {}
    for b in (*C.REFLECTANCE_BANDS, *C.INDEX_NAMES):
        data[b] = (("time", "y", "x"), np.full(shape, 0.2, np.float32))
    red = np.array(values, np.float32).reshape(shape)
    nir = np.full(shape, 0.5, np.float32)
    data["red"] = (("time", "y", "x"), red)
    data["nir"] = (("time", "y", "x"), nir)
    data["ndvi"] = (("time", "y", "x"), ndvi(red, nir).astype(np.float32))
    ds = xr.Dataset(data)
    ds["valid"] = (("time", "y", "x"), valid_a.reshape(shape))
    ds["scl"] = (("time", "y", "x"), np.full(shape, 4, np.uint8))
    for b in (*C.REFLECTANCE_BANDS, *C.INDEX_NAMES):
        ds[b] = ds[b].where(ds["valid"])
    return ds.assign_coords(window_day=("time", np.arange(1, t + 1) * 10))


@pytest.mark.parametrize("n,thin", [(0, 0), (1, 1), (2, 1), (3, 2), (4, 2), (5, 2)])
def test_n_obs_and_thin_flag(n: int, thin: int) -> None:
    t = 6
    valid = [True] * n + [False] * (t - n)
    comp = C.median_composite(_stack([0.1] * t, valid)).squeeze()
    assert int(comp["n_obs"]) == n and comp["n_obs"].dtype == np.uint16
    assert int(comp["thin"]) == thin and comp["thin"].dtype == np.uint8
    if n == 0:
        assert np.isnan(comp["ndvi"]).all() and int(comp["window_day_median"]) == 0
    else:
        assert np.isfinite(float(comp["ndvi"]))


def test_median_odd_even_and_outlier_rejection() -> None:
    # 3 scenes: the cloud-contaminated one (red 0.30) is rejected; with 2 it passes through.
    three = C.median_composite(_stack([0.05, 0.06, 0.30])).squeeze()
    assert float(three["red"]) == pytest.approx(0.06)
    two = C.median_composite(_stack([0.05, 0.30])).squeeze()
    assert float(two["red"]) == pytest.approx(0.175)  # mean of the two: no outlier rejection
    four = C.median_composite(_stack([0.05, 0.06, 0.07, 0.30])).squeeze()
    assert float(four["red"]) == pytest.approx(0.065)
    assert float(three["window_day_median"]) == 20


def test_median_ignores_invalid_observations() -> None:
    comp = C.median_composite(_stack([0.05, 0.9, 0.07], [True, False, True])).squeeze()
    assert int(comp["n_obs"]) == 2
    assert float(comp["red"]) == pytest.approx(0.06)
    assert float(comp["window_day_median"]) == 20  # days 10 and 30


def test_median_of_indices_differs_from_index_of_medians() -> None:
    # Documents ADR 0013 (D2): the median NDVI is not the NDVI of the median bands.
    red = np.array([0.05, 0.10, 0.40], np.float32)
    nir = np.array([0.50, 0.20, 0.45], np.float32)
    per_scene = np.median(ndvi(red, nir))
    of_medians = ndvi(np.median(red), np.median(nir))
    assert per_scene != pytest.approx(float(of_medians), abs=0.05)
    ds = _stack(list(red))
    ds["nir"] = ds["nir"] * 0 + xr.DataArray(nir.reshape(3, 1, 1), dims=("time", "y", "x"))
    ds["ndvi"] = (("time", "y", "x"), ndvi(red, nir).reshape(3, 1, 1).astype(np.float32))
    comp = C.median_composite(ds).squeeze()
    assert float(comp["ndvi"]) == pytest.approx(float(per_scene))


# ---- scene_stack / composite_window with a patched loader --------------------------------------


def _fake_loader(dn: dict[str, int], scl_val: int, baseline: str):  # type: ignore[no-untyped-def]
    def load(items, geobox, bands=tuple(S2_BANDS)):  # type: ignore[no-untyped-def]
        shape = (1, *geobox.shape)
        ds = xr.Dataset(
            {b: (("time", "y", "x"), np.full(shape, dn[b], np.uint16)) for b in S2_BANDS}
        )
        ds["scl"] = (("time", "y", "x"), np.full(shape, scl_val, np.uint8))
        ds.attrs["baseline"] = baseline
        return ds.chunk({"x": 3, "y": 3})

    return load


def test_scene_stack_applies_offset_by_baseline_and_scl(monkeypatch: pytest.MonkeyPatch) -> None:
    # DN 999 would wrap in uint16 if subtracted in integer arithmetic; here it is a kept negative.
    dn = {b: 999 for b in S2_BANDS}
    dn["red"], dn["nir"] = 1500, 3000
    groups = C.window_groups([_item(datetime(2020, 6, 5, 5, tzinfo=UTC), "05.10")], "SWM", 2020)
    monkeypatch.setattr(C.stac, "load_s2_acquisition", _fake_loader(dn, 4, "05.10"))
    st = C.scene_stack(groups, GB, season="SWM", sy=2020).compute()
    assert st["red"].dtype == np.float32
    assert float(st["red"].mean()) == pytest.approx(0.05)
    assert float(st["blue"].mean()) == pytest.approx(-0.0001, abs=1e-7)
    assert float(st["ndvi"].mean()) == pytest.approx((0.2 - 0.05) / 0.25, rel=1e-5)
    assert bool(st["valid"].all())
    assert int(st["window_day"][0]) == 36  # 5 June is day 36 counting from 1 May

    monkeypatch.setattr(C.stac, "load_s2_acquisition", _fake_loader(dn, 3, "05.10"))  # cloud shadow
    st = C.scene_stack(groups, GB).compute()
    assert not st["valid"].any() and st["ndvi"].isnull().all()


@pytest.mark.parametrize(
    "scl,keep,expect",
    [
        (2, (True, False), True),
        (2, (False, False), False),
        (7, (True, False), False),
        (7, (True, True), True),
    ],
)
def test_scene_stack_keep_set(monkeypatch, scl, keep, expect) -> None:  # type: ignore[no-untyped-def]
    dn = {b: 1500 for b in S2_BANDS}
    groups = C.window_groups([_item(datetime(2020, 6, 5, 5, tzinfo=UTC))], "SWM", 2020)
    monkeypatch.setattr(C.stac, "load_s2_acquisition", _fake_loader(dn, scl, "05.10"))
    st = C.scene_stack(groups, GB, keep).compute()
    assert bool(st["valid"].all()) is expect


def test_composite_window_outputs_and_resume(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    aoi = gpd.GeoDataFrame(geometry=[box(500000, 700000, 500050, 700060)], crs=CRS).to_crs(
        "EPSG:4326"
    )  # right 10 columns... covers x 0-4 of 6 columns
    dts = [datetime(2020, 6, d, 5, tzinfo=UTC) for d in (5, 10, 15)]
    items = [_item(d) for d in dts]
    dn = {b: 2000 for b in S2_BANDS}
    calls = {"n": 0}
    base = _fake_loader(dn, 4, "05.10")

    def counting(*a, **k):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return base(*a, **k)

    monkeypatch.setattr(C.stac, "load_s2_acquisition", counting)
    obs = C.composite_window(items, aoi, "SWM", 2020, tmp_path, geobox=GB)
    assert calls["n"] == 3
    with rasterio.open(tmp_path / "s2_2020_SWM_indices.tif") as src:
        assert src.count == 4 and src.dtypes[0] == "float32" and str(src.crs) == CRS
        assert src.transform == GB.transform
        assert src.descriptions == ("ndvi", "evi", "ndmi", "nbr")
        v = src.read(1)
        assert np.isfinite(v[2, 2]) and np.isnan(v[0, 5])  # outside the AOI polygon is NaN
        assert v[2, 2] == pytest.approx(0.0, abs=1e-6)  # all bands equal -> NDVI 0
    with rasterio.open(tmp_path / "s2_2020_SWM_reflectance.tif") as src:
        assert src.count == 7 and src.read(1)[2, 2] == pytest.approx(0.1)
    with rasterio.open(obs) as src:
        assert src.descriptions == C.OBS_BANDS and src.nodata == C.OBS_NODATA
        n = src.read(1)
        assert n[2, 2] == 3 and n[0, 5] == C.OBS_NODATA
        assert src.read(2)[2, 2] == 2  # thin = 2 at n_obs >= 3
        assert src.tags()["terrain_flag"].startswith("not applied")
    masks, ids = C.unpack_clear_masks(tmp_path / "clear_masks" / "s2_2020_SWM.npz")
    assert masks.shape == (3, 6, 6) and masks.all() and len(ids) == 3
    summ, scenes = C.collect_summaries(tmp_path)
    assert summ.loc[0, "n_obs_median"] == 3 and not summ.loc[0, "thin_season_year"]
    assert len(scenes) == 3 and scenes["sun_elevation_deg"].iloc[0] == pytest.approx(60.0)
    assert scenes["scl_share_2"].iloc[0] == 0.0

    # Resume: same inputs skip, changed item list recomputes, force recomputes.
    C.composite_window(items, aoi, "SWM", 2020, tmp_path, geobox=GB)
    assert calls["n"] == 3
    C.composite_window(items[:2], aoi, "SWM", 2020, tmp_path, geobox=GB)
    assert calls["n"] == 5
    C.composite_window(items[:2], aoi, "SWM", 2020, tmp_path, geobox=GB, force=True)
    assert calls["n"] == 7


# ---- write_cog ------------------------------------------------------------------------------


@pytest.mark.parametrize("dtype,nodata", [(np.float32, np.nan), (np.uint16, 65535)])
def test_write_cog_roundtrip(tmp_path, dtype, nodata) -> None:  # type: ignore[no-untyped-def]
    arr = (np.arange(2 * 20 * 30).reshape(2, 20, 30) % 100).astype(dtype)
    tr = Affine(10, 0, 500000.0, 0, -10, 700200.0)
    p = write_cog(arr, tmp_path / "a" / "x.tif", tr, CRS, nodata, ["b1", "b2"], {"k": "v"})
    with rasterio.open(p) as src:
        assert src.driver == "GTiff" and src.dtypes == (np.dtype(dtype).name,) * 2
        assert str(src.crs) == CRS and src.transform == tr
        assert src.descriptions == ("b1", "b2") and src.tags()["k"] == "v"
        assert np.isnan(src.nodata) if dtype is np.float32 else src.nodata == nodata
        assert src.compression.name == "deflate"
        np.testing.assert_array_equal(src.read(), arr)
        assert "OVERVIEWS" in str(src.tags(ns="IMAGE_STRUCTURE")) or src.overviews(1) == []
    assert not list((tmp_path / "a").glob("*.tmp*"))


def test_write_cog_validates_inputs(tmp_path) -> None:  # type: ignore[no-untyped-def]
    tr = Affine(10, 0, 0, 0, -10, 0)
    with pytest.raises(ValueError):
        write_cog(np.zeros((2, 3, 3), np.float32), tmp_path / "x.tif", tr, CRS, np.nan, ["only"])
    with pytest.raises(ValueError):
        write_cog(np.zeros(3, np.float32), tmp_path / "x.tif", tr, CRS, np.nan)
