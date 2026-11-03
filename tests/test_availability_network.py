"""Network smoke test of the M1.1 audit against Planetary Computer (skips without an AOI)."""

import os
from pathlib import Path

import pytest

from highland import inventory, stac
from highland.aoi import default_aoi_path, load_aoi

pytestmark = pytest.mark.network


def _aoi_path() -> Path:
    # HIGHLAND_TEST_AOI lets a developer point at another AOI file; coordinates are never inlined.
    env = os.environ.get("HIGHLAND_TEST_AOI")
    path = Path(env) if env else default_aoi_path()
    if not path.exists():
        pytest.skip(f"AOI file {path} not found (owner adds data/aoi/aoi.geojson)")
    return path


@pytest.mark.parametrize(
    ("sensor", "collection"),
    [("s2", stac.S2_COLLECTION), ("landsat", stac.LANDSAT_COLLECTION)],
)
def test_one_month_search_and_audit_one_acquisition(sensor, collection, tmp_path) -> None:
    aoi = load_aoi(_aoi_path())
    items = stac.search_items(collection, aoi, "2023-03-01", "2023-03-31")
    assert items, f"no {collection} items in 2023-03 over the AOI"
    if sensor == "s2":
        items, _ = stac.dedupe_s2(items)
    groups = stac.acquisition_groups(items, sensor)
    key, members = next(iter(groups.items()))
    geobox = stac.aoi_geobox(aoi, members, sensor)
    inside = inventory.aoi_inside_mask(aoi, geobox)
    row, clears = inventory.audit_acquisition(members, sensor, key, geobox, inside)
    assert inside.sum() > 0
    assert 0.0 <= row["valid_frac"] <= 1.0
    for name in inventory.VARIANTS[sensor]:
        assert 0.0 <= row[name] <= 1.0
        assert clears[name].shape == inside.shape
