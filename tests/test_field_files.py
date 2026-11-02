"""Guard the committed data/field/*.geojson files; skipped until the owner adds some."""

import pytest

from highland.io import default_field_dir, load_field_points


def test_committed_field_files_are_valid() -> None:
    files = sorted(default_field_dir().glob("*.geojson"))
    if not files:
        pytest.skip(f"no field files in {default_field_dir()} yet (see docs/field/form.md)")
    assert len(load_field_points(files)) >= 0
