"""Unit tests for highland.inventory with a monkeypatched reader (no network)."""

from datetime import UTC, date, datetime

import geopandas as gpd
import numpy as np
import pandas as pd
import pystac
import pytest
from shapely.geometry import box

from highland import inventory
from highland.inventory import (
    aoi_inside_mask,
    clear_counts,
    columns,
    month_year_table,
    run_audit,
    season_year,
    window_summary,
    write_inventory,
)

AOI_UTM = box(500000.0, 700000.0, 500400.0, 700400.0)  # 400 m square, 20 x 20 px at 20 m


@pytest.fixture
def aoi() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(geometry=[AOI_UTM], crs="EPSG:32644").to_crs("EPSG:4326")


def _s2_item(day: int, tile: str = "44NMN", month: int = 3) -> pystac.Item:
    dt = datetime(2023, month, day, 5, 6, 1, tzinfo=UTC)
    it = pystac.Item(
        id=f"S2A_MSIL2A_{dt:%Y%m%dT%H%M%S}_R076_T{tile}_20240805T215617",
        geometry=None,
        bbox=None,
        datetime=dt,
        properties={
            "platform": "Sentinel-2A",
            "s2:processing_baseline": "05.10",
            "s2:mgrs_tile": tile,
            "sat:relative_orbit": 76,
            "eo:cloud_cover": 10.0,
            "s2:mean_solar_azimuth": 80.0,
            "s2:mean_solar_zenith": 30.0,
        },
    )
    it.add_asset(
        "SCL",
        pystac.Asset(
            href="x.tif",
            extra_fields={
                "proj:transform": [20.0, 0.0, 499000.0, 0.0, -20.0, 701000.0],
                "proj:code": "EPSG:32644",
            },
        ),
    )
    return it


def _ls_item(platform: str, day: int, month: int = 3, year: int = 2023) -> pystac.Item:
    dt = datetime(year, month, day, 4, 53, tzinfo=UTC)
    it = pystac.Item(
        id=f"{platform}_{dt:%Y%m%d}",
        geometry=None,
        bbox=None,
        datetime=dt,
        properties={
            "platform": platform,
            "landsat:wrs_path": "141",
            "landsat:wrs_row": "055",
            "landsat:collection_category": "T1",
            "landsat:correction": "L2SP",
            "view:sun_azimuth": 100.0,
            "view:sun_elevation": 60.0,
        },
    )
    it.add_asset(
        "qa_pixel",
        pystac.Asset(
            href="x.tif",
            extra_fields={
                "proj:transform": [30.0, 0.0, 499005.0, 0.0, -30.0, 701025.0],
                "proj:code": "EPSG:32644",
            },
        ),
    )
    return it


def test_columns_and_inside_mask(aoi) -> None:
    from highland.stac import aoi_geobox

    cols = columns("s2")
    assert "scl_share_11" in cols and "clear_keep27" in cols and cols[-1] == "error"
    assert "qa_cirrus_share" in columns("landsat")
    gb = aoi_geobox(aoi, [_s2_item(1)], "s2")
    inside = aoi_inside_mask(aoi, gb)
    assert inside.shape == tuple(gb.shape)
    assert 380 <= inside.sum() <= 420  # 400 m square at 20 m is 400 px; edge snapping only


def _s2_reader(calls: list[str]):
    def reader(items, sensor, geobox):
        calls.append(items[0].id)
        scl = np.full(tuple(geobox.shape), 4, dtype=np.uint8)
        scl[:5] = 9  # cloud
        scl[5:7] = 2
        scl[7:9] = 7
        return scl

    return reader


def test_run_audit_s2_rows_resume_and_roundtrip(aoi, tmp_path) -> None:
    items = [_s2_item(3), _s2_item(8), _s2_item(8, tile="44NNN")]
    calls: list[str] = []
    df = run_audit("s2", items, aoi, tmp_path, reader=_s2_reader(calls))
    assert len(df) == 2  # two acquisitions; the two tiles on day 8 merge
    assert len(calls) == 2
    assert list(df.columns) == columns("s2")
    r = df.iloc[0]
    assert r["status"] == "ok" and r["year"] == 2023 and r["month"] == 3
    shares = [r[f"scl_share_{c}"] for c in range(12)]
    assert sum(shares) == pytest.approx(1.0)
    assert r["valid_frac"] == pytest.approx(1.0)
    assert r["sun_elevation_deg"] == pytest.approx(60.0) and r["sun_azimuth_deg"] == 80.0
    assert r["baseline_max"] == "05.10"
    assert r["clear_base"] < r["clear_keep2"] < r["clear_keep27"]
    assert r["clear_keep2"] - r["clear_base"] == pytest.approx(r["scl_share_2"])
    assert r["clear_keep7"] - r["clear_base"] == pytest.approx(r["scl_share_7"])
    # resume: nothing is read again and the rows are identical
    calls.clear()
    df2 = run_audit("s2", items, aoi, tmp_path, reader=_s2_reader(calls))
    assert calls == []
    pd.testing.assert_frame_equal(df, df2)
    # parquet round trip
    p = write_inventory(df, tmp_path / "inv.parquet")
    back = pd.read_parquet(p)
    assert list(back.columns) == columns("s2")
    assert back["acq_id"].tolist() == df["acq_id"].tolist()
    assert back["clear_base"].tolist() == pytest.approx(df["clear_base"].tolist())
    assert list(back["item_ids"].iloc[1]) == df["item_ids"].iloc[1]
    # counts
    from highland.stac import aoi_geobox

    gb = aoi_geobox(aoi, items, "s2")
    yms, counts = clear_counts(tmp_path, "s2", df, gb)
    assert yms == [(2023, 3)]
    assert set(counts) == set(inventory.S2_VARIANTS)
    assert counts["clear_base"].dtype == np.uint16
    assert counts["clear_base"].max() == 2 and counts["clear_base"][0, :5].max() == 0


def test_run_audit_error_row_and_retry(aoi, tmp_path) -> None:
    items = [_ls_item("landsat-8", 19), _ls_item("landsat-9", 27)]
    attempts: dict[str, int] = {}
    sleeps: list[float] = []

    def reader(its, sensor, geobox):
        pid = its[0].id
        attempts[pid] = attempts.get(pid, 0) + 1
        if "landsat-9" in pid:
            raise OSError("boom")
        qa = np.zeros(tuple(geobox.shape), dtype=np.uint16)
        qa[:10] = 1 << 2  # cirrus: counts only on L8/9 and only if masked
        qa[10:12] = 1 << 3
        return qa

    df = run_audit("landsat", items, aoi, tmp_path, reader=reader, sleep=sleeps.append)
    assert attempts["landsat-9_20230327"] == 3 and sleeps == [2.0, 4.0]
    bad = df[df["status"] == "error"]
    assert len(bad) == 1 and "boom" in bad.iloc[0]["error"]  # never dropped silently
    assert df.attrs["n_error"] == 1
    ok = df[df["status"] == "ok"].iloc[0]
    assert ok["qa_cirrus_share"] > 0 and ok["clear_nocirrus"] > ok["clear"]
    assert ok["clear_nocirrus"] - ok["clear"] == pytest.approx(ok["qa_cirrus_share"])
    assert not ok["slc_off"]
    # errors are not cached as done: a rerun retries them
    attempts.clear()
    run_audit("landsat", items, aoi, tmp_path, reader=reader, sleep=lambda s: None)
    assert "landsat-8_20230319" not in attempts and attempts["landsat-9_20230327"] == 3


def test_landsat7_slc_off_flag(aoi, tmp_path) -> None:
    items = [_ls_item("landsat-7", 10, month=6, year=2003), _ls_item("landsat-7", 10, year=2003)]

    def reader(its, sensor, geobox):
        return np.zeros(tuple(geobox.shape), dtype=np.uint16)

    df = run_audit("landsat", items, aoi, tmp_path, reader=reader)
    assert df.sort_values("date_local")["slc_off"].tolist() == [False, True]


def _df(rows: list[tuple[int, int, float, str]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["year", "month", "clear", "status"])


def test_month_year_table_sums() -> None:
    df = _df(
        [(2020, 1, 0.5, "ok"), (2020, 1, 0.25, "ok"), (2020, 3, 1.0, "ok"), (2021, 1, 9, "error")]
    )
    t = month_year_table(df, "clear")
    assert t.loc[2020, 1] == pytest.approx(0.75) and t.loc[2020, 3] == 1.0
    assert t.columns.tolist() == list(range(1, 13)) and 2021 not in t.index
    c = month_year_table(df, "clear", agg="count")
    assert c.loc[2020, 1] == 2


def test_season_year_december_goes_to_next_year() -> None:
    ne = (12, 1, 2)
    assert season_year(2020, 12, ne) == 2021
    assert season_year(2021, 1, ne) == 2021 and season_year(2021, 2, ne) == 2021
    assert season_year(2021, 5, ne) is None
    assert season_year(2021, 4, (3, 4)) == 2021


def test_window_summary() -> None:
    inside = np.ones((2, 2), dtype=bool)
    yms = [(2020, 12), (2021, 1), (2021, 6)]
    counts = np.zeros((3, 2, 2), dtype=np.uint16)
    counts[0, 0, 0] = 1  # Dec 2020 -> NE monsoon of 2021
    counts[1, 0, 1] = 2  # Jan 2021
    counts[2, :, :] = 5  # June, outside the window
    df = pd.DataFrame(
        {
            "year": [2019, 2020, 2021, 2021, 2021],
            "month": [1, 12, 1, 6, 2],
            "date_local": ["2019-01-05", "2020-12-05", "2021-01-05", "2021-06-05", "2021-02-10"],
            "status": ["ok", "ok", "ok", "ok", "error"],
            "valid_frac": [1.0, 1.0, 0.0, 1.0, 1.0],  # the Jan 2021 acquisition has no data
        }
    )
    out = window_summary(
        df,
        yms,
        counts,
        inside,
        {"ne": (12, 1, 2)},
        years=[2019, 2020, 2021, 2022],
        today=date(2022, 1, 15),
    )
    assert out["season_year"].tolist() == [2019, 2020, 2021, 2022]  # every year in the span
    r = out.set_index("season_year")
    assert r.loc[2021, "n_ok"] == 2 and r.loc[2021, "n_error"] == 1
    assert r.loc[2021, "n_with_data"] == 0 and r.loc[2020, "n_with_data"] == 1
    assert r.loc[2021, "share_le1_obs"] == pytest.approx(0.75)  # counts [1,2,0,0]
    assert r.loc[2021, "share_le2_obs"] == 1.0
    assert r.loc[2021, "p10_count"] == pytest.approx(0.0)
    assert r.loc[2021, "share_zero_obs"] == pytest.approx(0.5)
    assert r.loc[2021, "share_ge1_obs"] == pytest.approx(0.5)
    assert r.loc[2021, "median_count"] == pytest.approx(0.5)
    # a season-year with no ok acquisitions is reported, not omitted
    assert r.loc[2020, "n_ok"] == 0 and r.loc[2020, "share_zero_obs"] == 1.0
    assert r.loc[2020, "median_count"] == 0.0
    # partial: first data 2019-01-05 is inside Dec 2018-Feb 2019 only partly; 2022 ends after today
    assert r.loc[2019, "partial_window"] and r.loc[2022, "partial_window"]
    assert not r.loc[2020, "partial_window"] and not r.loc[2021, "partial_window"]


def test_mixed_tier_and_baseline_metadata(aoi, tmp_path) -> None:
    a, b = _ls_item("landsat-8", 19), _ls_item("landsat-8", 19)
    b.id += "_b"
    b.properties["landsat:collection_category"] = "T2"
    df = run_audit(
        "landsat", [a, b], aoi, tmp_path, reader=lambda i, s, g: np.zeros(tuple(g.shape), np.uint16)
    )
    r = df.iloc[0]
    assert len(df) == 1 and r["collection_category"] == "T1+T2" and r["mixed_tier"]
    single = run_audit(
        "landsat",
        [_ls_item("landsat-9", 27)],
        aoi,
        tmp_path / "x",
        reader=lambda i, s, g: np.zeros(tuple(g.shape), np.uint16),
    )
    assert single.iloc[0]["collection_category"] == "T1" and not single.iloc[0]["mixed_tier"]
    s2a, s2b = _s2_item(8), _s2_item(8, tile="44NNN")
    s2b.properties["s2:processing_baseline"] = "04.00"
    d2 = run_audit("s2", [s2a, s2b], aoi, tmp_path / "s", reader=_s2_reader([]))
    r2 = d2.iloc[0]
    assert (r2["baseline_min"], r2["baseline_max"], r2["mixed_baseline"]) == (
        "04.00",
        "05.10",
        True,
    )
    d3 = run_audit("s2", [s2a], aoi, tmp_path / "s3", reader=_s2_reader([]))
    assert not d3.iloc[0]["mixed_baseline"] and d3.iloc[0]["baseline_min"] == "05.10"


def test_cache_hit_and_misses(aoi, tmp_path, monkeypatch) -> None:
    items = [_s2_item(3)]
    calls: list[str] = []
    run_audit("s2", items, aoi, tmp_path, reader=_s2_reader(calls))
    run_audit("s2", items, aoi, tmp_path, reader=_s2_reader(calls))
    assert len(calls) == 1  # unchanged: hit
    # changed item ids (a reprocessed item replaces the old one) -> miss
    new = _s2_item(3)
    new.id = new.id.replace("20240805T215617", "20250101T000000")
    run_audit("s2", [new], aoi, tmp_path, reader=_s2_reader(calls))
    assert len(calls) == 2
    # changed AOI -> miss
    aoi2 = gpd.GeoDataFrame(
        geometry=[box(500000.0, 700000.0, 500380.0, 700400.0)], crs="EPSG:32644"
    )
    run_audit("s2", [new], aoi2.to_crs("EPSG:4326"), tmp_path, reader=_s2_reader(calls))
    assert len(calls) == 3
    # changed schema version -> miss
    monkeypatch.setattr(inventory, "CACHE_SCHEMA_VERSION", inventory.CACHE_SCHEMA_VERSION + 1)
    run_audit("s2", [new], aoi2.to_crs("EPSG:4326"), tmp_path, reader=_s2_reader(calls))
    assert len(calls) == 4


def test_clear_counts_refuses_other_geobox(aoi, tmp_path) -> None:
    from highland.stac import aoi_geobox

    items = [_s2_item(3)]
    df = run_audit("s2", items, aoi, tmp_path, reader=_s2_reader([]))
    gb = aoi_geobox(aoi, items, "s2")
    clear_counts(tmp_path, "s2", df, gb)  # matching grid works
    other = gb.translate_pix(1, 0)
    with pytest.raises(ValueError, match="differs from target"):
        clear_counts(tmp_path, "s2", df, other)


def test_clear_counts_refuses_mismatched_item_ids(aoi, tmp_path) -> None:
    from highland.stac import aoi_geobox

    items = [_s2_item(3)]
    df = run_audit("s2", items, aoi, tmp_path, reader=_s2_reader([]))
    gb = aoi_geobox(aoi, items, "s2")
    df = df.copy()
    df.at[0, "item_ids"] = ["some_other_item"]
    with pytest.raises(ValueError, match="item_ids"):
        clear_counts(tmp_path, "s2", df, gb)


def test_run_audit_no_items(aoi, tmp_path) -> None:
    df = run_audit("landsat", [], aoi, tmp_path)
    assert df.empty and list(df.columns) == columns("landsat")


def test_window_summary_search_range_makes_partial_exact() -> None:
    df = pd.DataFrame(
        {
            "status": ["ok"],
            "year": [2023],
            "month": [6],
            "date_local": ["2023-06-10"],
            "valid_frac": [1.0],
        }
    )
    inside = np.ones((2, 2), dtype=bool)
    counts = np.zeros((1, 2, 2), dtype=np.uint16)
    kw = {"windows": {"SWM": (5, 6, 7, 8, 9)}, "years": [2023]}
    args = (df, [(2023, 6)], counts, inside)
    # Dates in df only span 2023-06-10, so the df-based rule calls the window partial ...
    assert window_summary(*args, **kw, today=date(2024, 1, 1))["partial_window"].iloc[0]
    # ... while a search range covering May-September makes it complete.
    out = window_summary(
        *args, **kw, today=date(2024, 1, 1), search_range=(date(2023, 1, 1), date(2023, 12, 31))
    )
    assert not out["partial_window"].iloc[0]
    # A range that starts mid-window is partial.
    out = window_summary(
        *args, **kw, today=date(2024, 1, 1), search_range=(date(2023, 7, 1), date(2023, 12, 31))
    )
    assert out["partial_window"].iloc[0]


def test_baseline_era_labels() -> None:
    df = pd.DataFrame(
        {
            "baseline_min": ["02.05", "04.00", "03.00", "05.10"],
            "baseline_max": ["02.05", "05.10", "04.00", "05.10"],
        }
    )
    assert inventory.baseline_era(df).tolist() == ["pre-04.00", "post-04.00", "mixed", "post-04.00"]


def test_write_clear_counts_bands(aoi, tmp_path) -> None:
    import rasterio

    from highland.stac import aoi_geobox

    gb = aoi_geobox(aoi, [_s2_item(3)], "s2")
    arr = np.arange(2 * gb.shape[0] * gb.shape[1], dtype=np.uint16).reshape(2, *gb.shape)
    p = inventory.write_clear_counts(tmp_path / "c.tif", [(2023, 3), (2023, 4)], arr, gb)
    with rasterio.open(p) as src:
        assert src.count == 2 and src.dtypes[0] == "uint16"
        assert src.descriptions == ("2023-03", "2023-04")
        assert src.crs.to_epsg() == 32644
        np.testing.assert_array_equal(src.read(2), arr[1])
        assert src.transform.almost_equals(gb.transform)


def test_clear_rate_tables() -> None:
    df = pd.DataFrame(
        {
            "status": ["ok", "ok", "ok", "error"],
            "month": [3, 3, 6, 3],
            "valid_frac": [1.0, 0.0, 1.0, 1.0],
            "clear_base": [0.5, 0.0, 0.2, 0.9],
        }
    )
    w = inventory.clear_rate_by_window(df, "clear_base", {"A": (3, 4), "B": (5, 6, 7)})
    assert w["n_acq"].tolist() == [1, 1]  # the no-data and error rows are excluded
    assert w["mean_clear"].tolist() == pytest.approx([0.5, 0.2])
    m = inventory.clear_rate_by_month(df, "clear_base")
    assert len(m) == 12 and m.loc[3, "n_acq"] == 1 and np.isnan(m.loc[1, "mean_clear"])


def test_write_clear_counts_outside_aoi_is_nodata(aoi, tmp_path) -> None:
    import rasterio

    from highland.stac import aoi_geobox

    gb = aoi_geobox(aoi, [_s2_item(3)], "s2")
    inside = aoi_inside_mask(aoi, gb)
    arr = np.ones((1, *gb.shape), dtype=np.uint16)
    p = inventory.write_clear_counts(tmp_path / "c.tif", [(2023, 3)], arr, gb, inside)
    with rasterio.open(p) as src:
        assert src.nodata == inventory.COUNT_NODATA
        m = src.read(1, masked=True)
        assert m.mask.sum() == (~inside).sum() and m.sum() == inside.sum()
