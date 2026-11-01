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
        ("missing_required", "'canopy_open_pct' is empty"),
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


def _write(
    tmp_path: Path, features: list[dict], name: str = "t.geojson", dlon: float = 0.0
) -> Path:
    """Write a synthetic file: each dict overrides the properties of a valid base point."""
    import json

    base = json.loads(fx("valid").read_text())["features"][4]["properties"]  # tea point
    feats = []
    for i, over in enumerate(features):
        props = {**base, "point_id": f"t-{i}", **over}
        lon = 80.91 + dlon + 0.005 * i
        feats.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": {"type": "Point", "coordinates": [lon, 6.83]},
            }
        )
    path = tmp_path / name
    path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    return path


def test_padded_values_are_stripped_not_nan(aoi: gpd.GeoDataFrame, tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        [
            {
                "class": " pine",
                "opened": "yes ",
                "openness_method": " glama",
                "secondary_class": " tea ",
            }
        ],
    )
    pts = load_field_points(f, aoi=aoi, now=NOW)
    assert pts.loc[0, "class"] == "pine"
    assert pts.loc[0, "opened"] == "yes"
    assert pts.loc[0, "openness_method"] == "glama"
    assert pts.loc[0, "secondary_class"] == "tea"
    for c in ("class", "opened", "openness_method"):
        assert not pts[c].isna().any()


def test_valid_fixture_required_categoricals_have_no_nan(aoi: gpd.GeoDataFrame) -> None:
    pts = load_field_points(fx("valid"), aoi=aoi, now=NOW)
    assert not pts[["class", "opened", "openness_method"]].isna().any().any()


def test_private_flag_and_source_file(aoi: gpd.GeoDataFrame, tmp_path: Path) -> None:
    priv = tmp_path / "private"
    priv.mkdir()
    pub = _write(tmp_path, [{}], "pub.geojson")
    prv = _write(priv, [{"point_id": "p-1"}], "mine.geojson", dlon=0.05)
    pts = load_field_points([pub, prv], aoi=aoi, now=NOW).set_index("point_id")
    assert not pts.loc["t-0", "private"]
    assert pts.loc["p-1", "private"]
    assert pts.loc["p-1", "source_file"] == "private/mine.geojson"
    assert pts.loc["t-0", "source_file"] == "pub.geojson"
    assert pts["private"].dtype == bool


def test_repo_relative_source_file(aoi: gpd.GeoDataFrame) -> None:
    # fixtures are outside data/field, so they fall back to the file name
    pts = load_field_points(fx("valid"), aoi=aoi, now=NOW)
    assert not pts["private"].any()


@pytest.mark.parametrize(
    ("over", "rule"),
    [
        ({"class": "pine", "secondary_class": "pine"}, "repeats 'class'"),
        ({"secondary_class": "unsure"}, "cannot be 'unsure'"),
        ({"opened": "no", "opened_years_ago": 3}, "only allowed when 'opened' is yes"),
        ({"opened": "unsure", "opened_years_ago": 3}, "only allowed when 'opened' is yes"),
        ({"opened": "yes", "opened_years_ago": -1}, "must be 0 or more"),
        ({"opened": "yes", "opened_years_ago": 2.5}, "not a whole number"),
        ({"openness_method": "not_measured", "canopy_open_pct": 40}, "must be empty"),
        ({"openness_method": "visual", "canopy_open_pct": None}, "enter 0-100"),
        ({"gps_time_utc": "2026-05-01 04:45"}, "'gps_time_utc'"),
    ],
)
def test_new_rules(over: dict, rule: str, aoi: gpd.GeoDataFrame, tmp_path: Path) -> None:
    f = _write(tmp_path, [over])
    with pytest.raises(FieldDataError) as ei:
        load_field_points(f, aoi=aoi, now=NOW)
    assert any(rule in m for m in ei.value.problems), ei.value.problems


def test_not_measured_and_years_ago_accepted(aoi: gpd.GeoDataFrame, tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        [
            {"openness_method": "not_measured", "canopy_open_pct": None},
            {"opened": "yes", "opened_years_ago": 4, "gps_time_utc": "2026-05-01T04:45:10Z"},
        ],
    )
    pts = load_field_points(f, aoi=aoi, now=NOW)
    assert pd.isna(pts.loc[0, "canopy_open_pct"])
    assert pts["canopy_open_pct"].dtype == "Int16"
    assert pts.loc[1, "opened_years_ago"] == 4
    assert pd.isna(pts.loc[0, "gps_time_utc"])
    assert pts.loc[1, "gps_time_utc"] == pd.Timestamp("2026-05-01T04:45:10Z")


def test_reference_classes() -> None:
    assert "unsure" not in hio.REFERENCE_CLASSES
    assert set(hio.REFERENCE_CLASSES) | {"unsure"} == set(hio.FIELD_CLASSES)
