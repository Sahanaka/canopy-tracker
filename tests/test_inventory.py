"""Unit tests for highland.inventory with a monkeypatched reader (no network)."""

from datetime import UTC, datetime

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
    yms, counts = clear_counts(tmp_path, "s2", df)
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
        {"year": [2020, 2021, 2021], "month": [12, 1, 6], "status": ["ok", "ok", "ok"]}
    )
    out = window_summary(df, yms, counts, inside, {"ne": (12, 1, 2)})
    assert len(out) == 1
    r = out.iloc[0]
    assert r["season_year"] == 2021 and r["n_acquisitions"] == 2
    assert r["share_zero_obs"] == pytest.approx(0.5) and r["share_ge1_obs"] == pytest.approx(0.5)
    assert r["median_count"] == pytest.approx(0.5)
