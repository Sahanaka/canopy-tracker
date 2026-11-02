"""Unit tests for highland.stac using synthetic pystac items (no network)."""

from datetime import UTC, datetime

import geopandas as gpd
import pystac
import pytest
from shapely.geometry import box

from highland.stac import (
    acquisition_groups,
    aoi_geobox,
    dedupe_s2,
    load_items,
    parse_baseline,
    s2_processing_time,
    save_items,
)


def _s2(
    tile: str = "44NMN",
    dt: datetime = datetime(2023, 3, 28, 5, 6, 1, tzinfo=UTC),
    baseline: str = "05.10",
    proc: str = "20240805T215617",
    platform: str = "Sentinel-2A",
    orbit: int = 76,
) -> pystac.Item:
    iid = f"S2A_MSIL2A_{dt:%Y%m%dT%H%M%S}_R{orbit:03d}_T{tile}_{proc}"
    return pystac.Item(
        id=iid,
        geometry=None,
        bbox=None,
        datetime=dt,
        properties={
            "s2:processing_baseline": baseline,
            "s2:mgrs_tile": tile,
            "platform": platform,
            "sat:relative_orbit": orbit,
        },
    )


def _ls(platform: str, dt: datetime, row: str = "055") -> pystac.Item:
    return pystac.Item(
        id=f"{platform}_{dt:%Y%m%d}_{row}",
        geometry=None,
        bbox=None,
        datetime=dt,
        properties={"platform": platform, "landsat:wrs_row": row},
    )


def test_parse_baseline_ordering() -> None:
    assert parse_baseline("05.09") < parse_baseline("05.10")  # a float parse would reverse this
    assert parse_baseline("04.00") < parse_baseline("05.00")
    assert parse_baseline("05.10") == (5, 10)
    with pytest.raises(ValueError):
        parse_baseline("5")


def test_processing_time() -> None:
    assert s2_processing_time(_s2()) == "20240805T215617"
    with pytest.raises(ValueError):
        s2_processing_time(_ls("landsat-8", datetime(2023, 1, 1, tzinfo=UTC)))


def test_dedupe_keeps_highest_baseline() -> None:
    a = _s2(baseline="05.09", proc="20230328T085808")
    b = _s2(baseline="05.10", proc="20240805T215617")
    kept, dropped = dedupe_s2([a, b])
    assert [i.id for i in kept] == [b.id]
    assert len(dropped) == 1
    row = dropped.iloc[0]
    assert row["dropped_id"] == a.id and row["kept_id"] == b.id
    assert row["reason"] == "lower_baseline"
    assert row["dropped_baseline"] == "05.09" and row["kept_baseline"] == "05.10"


def test_dedupe_equal_baseline_latest_processing() -> None:
    a = _s2(baseline="05.10", proc="20240101T000000")
    b = _s2(baseline="05.10", proc="20240805T215617")
    kept, dropped = dedupe_s2([a, b])
    assert [i.id for i in kept] == [b.id]
    assert dropped.iloc[0]["reason"] == "older_processing_time"


def test_dedupe_different_tiles_both_kept() -> None:
    a = _s2(tile="44NMN")
    b = _s2(tile="44NNN")
    kept, dropped = dedupe_s2([a, b])
    assert len(kept) == 2 and dropped.empty
    assert list(dropped.columns)[:2] == ["dropped_id", "kept_id"]


def test_dedupe_baseline_not_float() -> None:
    a = _s2(baseline="05.9", proc="20230328T085808")  # (5, 9)
    b = _s2(baseline="05.10", proc="20240805T215617")  # (5, 10)
    kept, _ = dedupe_s2([a, b])
    assert kept[0].id == b.id


def test_acquisition_groups_merge_tiles_split_platforms() -> None:
    d = datetime(2023, 3, 28, 5, 6, 1, tzinfo=UTC)
    a, b = _s2(tile="44NMN", dt=d), _s2(tile="44NNN", dt=d)
    c = _s2(tile="44NMN", dt=d, platform="Sentinel-2B")
    groups = acquisition_groups([a, b, c], "s2")
    assert sorted(len(v) for v in groups.values()) == [1, 2]
    assert set(groups) == {"sentinel-2a_2023-03-28", "sentinel-2b_2023-03-28"}
    ls = acquisition_groups(
        [
            _ls("landsat-8", datetime(2023, 3, 19, 4, 53, tzinfo=UTC), "055"),
            _ls("landsat-8", datetime(2023, 3, 19, 4, 54, tzinfo=UTC), "056"),
            _ls("landsat-9", datetime(2023, 3, 19, 4, 53, tzinfo=UTC), "055"),
        ],
        "landsat",
    )
    assert sorted(len(v) for v in ls.values()) == [1, 2]


def test_acquisition_group_local_day() -> None:
    # 20:00 UTC is 01:30 the next day in Sri Lanka (UTC+05:30 in 2023).
    g = acquisition_groups([_ls("landsat-8", datetime(2023, 3, 19, 20, 0, tzinfo=UTC))], "landsat")
    assert list(g) == ["landsat-8_2023-03-20"]


def test_save_load_roundtrip(tmp_path) -> None:
    items = [_s2(), _s2(tile="44NNN")]
    p = save_items(items, tmp_path / "sub" / "items.json")
    back = load_items(p)
    assert [i.id for i in back] == [i.id for i in items]
    assert back[0].datetime == items[0].datetime


def _grid_item(x0: float, y0: float, asset: str, res: float) -> pystac.Item:
    it = _s2()
    it.add_asset(
        asset,
        pystac.Asset(
            href="x.tif",
            extra_fields={
                "proj:transform": [res, 0.0, x0, 0.0, -res, y0],
                "proj:code": "EPSG:32644",
            },
        ),
    )
    return it


def test_aoi_geobox_snaps_to_offset_grid() -> None:
    # Landsat-like grid with a 15 m origin offset; AOI in UTM built from a UTM box.
    aoi_utm = gpd.GeoDataFrame(
        geometry=[box(500010.0, 700010.0, 500100.0, 700100.0)], crs="EPSG:32644"
    )
    aoi = aoi_utm.to_crs("EPSG:4326")
    it = _grid_item(347685.0, 915015.0, "qa_pixel", 30.0)
    gb = aoi_geobox(aoi, [it], "landsat")
    t = gb.transform
    assert (t.c - 347685.0) % 30 == pytest.approx(0, abs=1e-6)
    assert (915015.0 - t.f) % 30 == pytest.approx(0, abs=1e-6)
    assert t.a == 30.0 and t.e == -30.0
    left, bottom, right, top = gb.boundingbox
    assert left <= 500010.0 + 1e-3 and right >= 500100.0 - 1e-3
    assert bottom <= 700010.0 + 1e-3 and top >= 700100.0 - 1e-3
    assert left > 500010.0 - 30.0 - 1e-3 and top < 700100.0 + 30.0 + 1e-3
    assert str(gb.crs) == "EPSG:32644"


def test_aoi_geobox_needs_items() -> None:
    aoi = gpd.GeoDataFrame(geometry=[box(80.9, 6.8, 80.99, 6.89)], crs="EPSG:4326")
    with pytest.raises(ValueError):
        aoi_geobox(aoi, [], "s2")


def test_aoi_geobox_rejects_wrong_resolution() -> None:
    aoi = gpd.GeoDataFrame(geometry=[box(500010.0, 700010.0, 500100.0, 700100.0)], crs="EPSG:32644")
    it = _grid_item(499980.0, 701000.0, "SCL", 10.0)  # 10 m SCL is not what s2 expects (20 m)
    with pytest.raises(ValueError, match="refusing to resample"):
        aoi_geobox(aoi.to_crs("EPSG:4326"), [it], "s2")


def test_processing_time_falls_back_to_generation_time() -> None:
    it = _s2()
    it.id = "weird_id"
    it.properties["s2:generation_time"] = "2024-08-05T21:56:17.000000Z"
    assert s2_processing_time(it) == "20240805T215617"
