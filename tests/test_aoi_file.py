"""Guard the committed data/aoi/aoi.geojson; skipped until the owner adds it."""

import pytest

from highland.aoi import default_aoi_path, load_aoi


def test_committed_aoi_is_valid() -> None:
    path = default_aoi_path()
    if not path.exists():
        pytest.skip(f"{path} not committed yet (owner draws it; see data/aoi/README.md)")
    assert len(load_aoi(path)) == 1
