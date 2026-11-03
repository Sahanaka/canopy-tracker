"""Smoke tests: the package and every layout module import without network or heavy deps."""

import importlib

import pytest

import highland

MODULES = [
    "aoi",
    "stac",
    "masking",
    "indices",
    "composites",
    "landsat",
    "change",
    "terrain",
    "landcover",
    "io",
    "viz3d",
    "viewer",
]


def test_version_is_non_empty_string() -> None:
    assert isinstance(highland.__version__, str)
    assert highland.__version__


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name: str) -> None:
    assert importlib.import_module(f"highland.{name}")
