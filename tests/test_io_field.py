"""Unit tests for highland.io.load_field_points on synthetic GeoJSON fixtures (no network)."""

import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest

from highland import io as hio
from highland.io import FieldDataError, load_field_points, main

FIX = Path(__file__).parent / "fixtures" / "field"
AOI_FIX = Path(__file__).parent / "fixtures" / "aoi" / "valid.geojson"
NOW = datetime(2026, 11, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def aoi() -> gpd.GeoDataFrame:
    return gpd.read_file(AOI_FIX)


def fx(name: str) -> Path:
    return FIX / f"{name}.geojson"


def problems_of(name: str | list[str], aoi: gpd.GeoDataFrame, **kw) -> list[str]:
    paths = [fx(n) for n in name] if isinstance(name, list) else fx(name)
    with pytest.raises(FieldDataError) as ei:
        load_field_points(paths, aoi=aoi, now=NOW, **kw)
    return ei.value.problems


def test_valid_file_types(aoi: gpd.GeoDataFrame) -> None:
    pts = load_field_points(fx("valid"), aoi=aoi, now=NOW)
    assert len(pts) == 9
    assert pts.crs.to_epsg() == 4326
    assert str(pts["observed_at"].dt.tz) == "UTC"
    assert pts["canopy_open_pct"].dtype == "Int16"
    assert pts["gps_accuracy_m"].dtype == "Float32"
    assert pts["class"].dtype.name == "category"
    assert set(pts["class"].cat.categories) == set(hio.FIELD_CLASSES)
    assert not pts.geometry.has_z.any()
    assert set(pts["source_file"]) == {"valid.geojson"}


def test_offset_converted_to_utc(aoi: gpd.GeoDataFrame) -> None:
    pts = load_field_points(fx("valid"), aoi=aoi, now=NOW).set_index("point_id")
    assert pts.loc["syn-001", "observed_at"] == pd.Timestamp("2026-05-01T04:45:00Z")
    assert pts.loc["syn-003", "observed_at"] == pd.Timestamp("2026-05-01T04:45:00Z")


def test_photos_and_optionals(aoi: gpd.GeoDataFrame) -> None:
    pts = load_field_points(fx("valid"), aoi=aoi, now=NOW).set_index("point_id")
    assert pts.loc["syn-001", "photos"] == ["photos/syn-001_a.jpg", "photos/syn-001_b.JPG"]
    assert pts.loc["syn-005", "photos"] == []
    assert len(pts.loc["syn-004", "photos"]) == 3
    assert pd.isna(pts.loc["syn-003", "gps_accuracy_m"])
    assert pts.loc["syn-001", "secondary_class"] == "alstonia"
    assert pd.isna(pts.loc["syn-002", "secondary_class"])
    assert pts.loc["syn-001", "extra_note"] == "kept"


def test_other_group_is_subset_of_classes() -> None:
    assert hio.OTHER_GROUP <= set(hio.FIELD_CLASSES)


@pytest.mark.parametrize(
    ("name", "rule"),
    [
        ("bad_class", "'class' value 'banana' is not allowed"),
        ("bad_vocab", "'opened' value 'maybe' is not allowed"),
        ("bad_vocab", "'openness_method' value 'eyeball'"),
        ("bad_vocab", "'secondary_class' value 'banana'"),
        ("missing_required", "required field 'observer' is empty"),
        ("missing_required", "required field 'canopy_open_pct' is empty"),
        ("pct_out_of_range", "value 120 is outside 0-100"),
        ("pct_out_of_range", "value -5 is outside 0-100"),
        ("pct_out_of_range", "not a whole number"),
        ("naive_datetime", "no UTC offset"),
        ("outside_aoi", "outside the AOI polygon"),
        ("swapped_latlon", "look swapped"),
        ("polygon_geom", "geometry is Polygon"),
        ("abs_photo_path", "not an absolute path"),
        ("abs_photo_path", "must not contain '..'"),
        ("abs_photo_path", "must end with"),
        ("legacy_crs_32644", "CRS is EPSG:32644"),
        ("empty_file", "cannot read as GeoJSON"),
    ],
)
def test_rule_messages(name: str, rule: str, aoi: gpd.GeoDataFrame) -> None:
    msgs = problems_of(name, aoi)
    assert any(rule in m for m in msgs), msgs
    assert all(m.startswith(f"{name}.geojson") for m in msgs)


def test_future_date_uses_injected_now(aoi: gpd.GeoDataFrame) -> None:
    assert any("in the future" in m for m in problems_of("future_date", aoi))
    later = datetime(2040, 1, 1, tzinfo=UTC)
    assert len(load_field_points(fx("future_date"), aoi=aoi, now=later)) == 1


def test_duplicate_id_across_files(aoi: gpd.GeoDataFrame) -> None:
    msgs = problems_of(["duplicate_id_a", "duplicate_id_b"], aoi)
    assert len(msgs) == 1
    assert "duplicate_id_b.geojson, dup-1: point_id is not unique" in msgs[0]
    assert "duplicate_id_a.geojson" in msgs[0]


def test_same_place_and_time_is_duplicate(aoi: gpd.GeoDataFrame, tmp_path: Path) -> None:
    shutil.copy(fx("valid"), tmp_path / "a.geojson")
    text = fx("valid").read_text().replace("syn-", "dup-")
    (tmp_path / "b.geojson").write_text(text)
    with pytest.raises(FieldDataError) as ei:
        load_field_points(tmp_path, aoi=aoi, now=NOW)
    assert any("same location and time" in m for m in ei.value.problems)


def test_collects_all_problems(aoi: gpd.GeoDataFrame) -> None:
    msgs = problems_of("multi_error", aoi)
    assert len(msgs) >= 3
    kinds = ["'class'", "canopy_open_pct", "observed_at"]
    assert all(any(k in m for m in msgs) for k in kinds)


def test_error_message_lists_every_problem(aoi: gpd.GeoDataFrame) -> None:
    with pytest.raises(FieldDataError, match="3 problem") as ei:
        load_field_points(fx("multi_error"), aoi=aoi, now=NOW)
    assert all(p in str(ei.value) for p in ei.value.problems)


def test_bbox_fallback_warns(caplog: pytest.LogCaptureFixture, monkeypatch) -> None:
    monkeypatch.setattr(hio, "default_aoi_path", lambda: Path("/nonexistent/aoi.geojson"))
    with caplog.at_level(logging.WARNING, logger="highland.io"):
        pts = load_field_points(fx("valid"), now=NOW)
    assert len(pts) == 9
    assert any("bounding box" in r.message for r in caplog.records)


def test_bbox_fallback_rejects_outside_sri_lanka(monkeypatch) -> None:
    monkeypatch.setattr(hio, "default_aoi_path", lambda: Path("/nonexistent/aoi.geojson"))
    with pytest.raises(FieldDataError) as ei:
        load_field_points(fx("swapped_latlon"), now=NOW)
    assert "look swapped" in ei.value.problems[0]


def test_photo_root_existence(aoi: gpd.GeoDataFrame, tmp_path: Path) -> None:
    msgs = problems_of("valid", aoi, photo_root=tmp_path)
    assert len(msgs) == 6
    assert all("not found under" in m for m in msgs)
    for rel in [
        "photos/syn-001_a.jpg",
        "photos/syn-001_b.JPG",
        "photos/syn-002.jpeg",
        "photos/syn-004.png",
        "photos/syn-004b.jpg",
        "photos/syn-004c.jpg",
    ]:
        f = tmp_path / rel
        f.parent.mkdir(exist_ok=True)
        f.write_bytes(b"x")
    assert len(load_field_points(fx("valid"), aoi=aoi, photo_root=tmp_path, now=NOW)) == 9


def test_no_features_gives_empty_result(aoi: gpd.GeoDataFrame) -> None:
    assert len(load_field_points(fx("no_features"), aoi=aoi, now=NOW)) == 0


def test_missing_path_raises_file_not_found() -> None:
    with pytest.raises(FileNotFoundError):
        load_field_points("/nonexistent/field.geojson")


def test_main_exit_codes(capsys: pytest.CaptureFixture[str], monkeypatch) -> None:
    monkeypatch.setattr(hio, "default_aoi_path", lambda: Path("/nonexistent/aoi.geojson"))
    assert main([str(fx("valid"))]) == 0
    out = capsys.readouterr().out
    assert "9 valid point(s)" in out
    assert "pine: 1" in out
    assert main([str(fx("multi_error"))]) == 1
    assert "3 problem(s)" in capsys.readouterr().out
    assert main(["/nonexistent/x.geojson"]) == 1
