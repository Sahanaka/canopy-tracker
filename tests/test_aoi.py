"""Unit tests for highland.aoi on tiny synthetic GeoJSON fixtures (no network)."""

from pathlib import Path

import pyproj
import pytest
import shapely
from shapely.geometry import Polygon

from highland import aoi
from highland.aoi import AOIError, aoi_area_km2, default_aoi_path, load_aoi, main, to_utm

FIX = Path(__file__).parent / "fixtures" / "aoi"


def fx(name: str) -> Path:
    return FIX / f"{name}.geojson"


@pytest.mark.parametrize(
    "name",
    [
        "valid",
        "feature",
        "bare_geometry",
        "multipolygon_one_part",
        "clockwise_ring",
        "with_z",
    ],
)
def test_valid_variants(name: str) -> None:
    gdf = load_aoi(fx(name))
    assert len(gdf) == 1
    assert gdf.crs.equals("EPSG:4326", ignore_axis_order=True)
    geom = gdf.geometry.iloc[0]
    assert isinstance(geom, Polygon)
    assert not shapely.has_z(geom)
    assert geom.exterior.is_ccw
    assert 50 <= aoi_area_km2(gdf) <= 200


def test_properties_are_kept() -> None:
    assert load_aoi(fx("multipolygon_one_part")).iloc[0]["name"] == "test aoi"


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("empty_file", "Cannot read"),
        ("empty_fc", "exactly 1 feature"),
        ("two_features", "exactly 1 feature"),
        ("multipolygon_two_parts", "2 parts"),
        ("point", "Point"),
        ("bowtie", "Self-intersection"),
        ("with_hole", "hole"),
        ("too_small", "15.8 km^2"),
        ("too_large", "395.7 km^2"),
        ("outside_sri_lanka", "bounding box"),
        ("swapped_latlon", "swapped"),
        ("projected_coords", "not degrees"),
        ("legacy_crs_32644", "re-export as EPSG:4326"),
    ],
)
def test_invalid_cases(name: str, text: str) -> None:
    with pytest.raises(AOIError) as exc:
        load_aoi(fx(name))
    assert text.lower() in str(exc.value).lower()


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match=r"data/aoi/README\.md"):
        load_aoi(tmp_path / "nope.geojson")


def test_area_matches_geodesic_within_0p1_percent() -> None:
    gdf = load_aoi(fx("valid"))
    geod_area, _ = pyproj.Geod(ellps="WGS84").geometry_area_perimeter(gdf.geometry.iloc[0])
    assert aoi_area_km2(gdf) == pytest.approx(abs(geod_area) / 1e6, rel=1e-3)


def test_to_utm_is_metres() -> None:
    assert to_utm(load_aoi(fx("valid"))).crs.to_epsg() == 32644


def test_bbox_matches_epsg5235_and_zone44() -> None:
    bounds = pyproj.CRS("EPSG:5235").area_of_use.bounds
    assert aoi.SRI_LANKA_BBOX == pytest.approx(bounds, abs=0.01)
    assert 78 <= aoi.SRI_LANKA_BBOX[0] and aoi.SRI_LANKA_BBOX[2] <= 84


def test_default_path_is_absolute_and_cwd_independent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    before = default_aoi_path()
    assert before.is_absolute()
    assert before.parts[-3:] == ("data", "aoi", "aoi.geojson")
    monkeypatch.chdir(tmp_path)
    assert default_aoi_path() == before


def test_main_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(fx("valid"))]) == 0
    assert "OK" in capsys.readouterr().out
    assert main([str(fx("too_small"))]) == 1
    assert "ERROR" in capsys.readouterr().out
