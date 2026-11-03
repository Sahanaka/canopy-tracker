"""Offline tests for highland.terrain on synthetic surfaces."""

from __future__ import annotations

import numpy as np
import pytest
from affine import Affine
from odc.geo.geobox import GeoBox

from highland import terrain as T

RES = 30.0


def plane(p: float, q: float, shape: tuple[int, int] = (9, 11), z0: float = 1000.0) -> np.ndarray:
    """z = z0 + p*x_east + q*y_north on a 30 m grid, rows north to south."""
    ny, nx = shape
    x = (np.arange(nx) * RES)[None, :]
    y = ((ny - 1 - np.arange(ny)) * RES)[:, None]
    return (z0 + p * x + q * y).astype(np.float64)


@pytest.mark.parametrize("p,q", [(0.3, 0.0), (0.0, -0.7), (0.25, 0.4), (-1.0, 0.5)])
def test_slope_on_plane(p: float, q: float) -> None:
    s = T.slope_deg(plane(p, q), RES)
    assert np.allclose(s[1:-1, 1:-1], np.degrees(np.arctan(np.hypot(p, q))), atol=1e-5)


@pytest.mark.parametrize(
    "p,q,expected",
    [
        (-0.5, 0.0, 90.0),  # descends to the east
        (0.5, 0.0, 270.0),  # descends to the west
        (0.0, 0.5, 180.0),  # rises to the north, descends south
        (0.0, -0.5, 0.0),  # descends north
        (-0.5, -0.5, 45.0),
        (-0.5, 0.5, 135.0),
        (0.5, 0.5, 225.0),
        (0.5, -0.5, 315.0),
    ],
)
def test_aspect_on_plane(p: float, q: float, expected: float) -> None:
    a = T.aspect_deg(plane(p, q), RES)
    assert np.allclose(a[1:-1, 1:-1], expected, atol=1e-4)


def test_flat_ground_and_ring() -> None:
    z = np.full((6, 7), 500.0)
    s, a = T.slope_deg(z, RES), T.aspect_deg(z, RES)
    assert np.all(s[1:-1, 1:-1] == 0) and np.all(np.isnan(a))
    z = plane(0.2, 0.1)
    s = T.slope_deg(z, RES)
    ring = np.ones(z.shape, bool)
    ring[1:-1, 1:-1] = False
    assert np.isnan(s[ring]).all() and np.isfinite(s[~ring]).all()
    assert s.dtype == np.float32


def test_row_order_sign() -> None:
    # Row 0 is north: elevation increasing with the row index means descending northwards.
    z = np.tile(np.arange(6, dtype=float)[:, None] * 30.0, (1, 6))
    dzdx, dzdy = T.horn_gradient(z, RES)
    assert np.allclose(dzdy[1:-1, 1:-1], -1.0) and np.allclose(dzdx[1:-1, 1:-1], 0.0)


def test_horn_needs_3x3() -> None:
    with pytest.raises(ValueError):
        T.horn_gradient(np.zeros((2, 5)), RES)


def test_cos_incidence_cases() -> None:
    # Facet normal to the sun: slope 30 deg facing az 135 with the sun at elevation 60.
    slope = 30.0
    t = np.tan(np.radians(slope))
    # Facing az 135 (SE): downslope vector (E, N) = (sin135, cos135).
    p, q = -t * np.sin(np.radians(135)), -t * np.cos(np.radians(135))
    ci = T.cos_incidence(plane(p, q), RES, 135.0, 60.0)
    assert np.allclose(ci[1:-1, 1:-1], 1.0, atol=1e-6)
    # Flat: sin(elev).
    flat = T.cos_incidence(np.full((5, 5), 10.0), RES, 77.0, 52.4)
    assert np.allclose(flat[1:-1, 1:-1], np.sin(np.radians(52.4)), atol=1e-6)
    # Facing directly away: cos i = cos(zenith + slope) < 0 once slope > elevation (not 90 - elev).
    el = 55.0
    t = np.tan(np.radians(el + 1))
    p, q = t * np.sin(np.radians(45)), t * np.cos(np.radians(45))  # descends to az 225, sun at 45
    ci = T.cos_incidence(plane(p, q), RES, 45.0, el)
    assert np.all(ci[1:-1, 1:-1] < 0)
    t = np.tan(np.radians(el - 1))
    p, q = t * np.sin(np.radians(45)), t * np.cos(np.radians(45))
    assert np.all(T.cos_incidence(plane(p, q), RES, 45.0, el)[1:-1, 1:-1] > 0)
    # Sun at the zenith: cos i = cos(slope).
    ci = T.cos_incidence(plane(0.4, 0.3), RES, 10.0, 90.0)
    assert np.allclose(ci[1:-1, 1:-1], np.cos(np.arctan(0.5)), atol=1e-6)


def test_cos_incidence_matches_aspect_formula() -> None:
    rng = np.random.default_rng(1)
    z = np.cumsum(np.cumsum(rng.normal(0, 3, (30, 30)), 0), 1)
    az, el = 140.0, 52.0
    ci = T.cos_incidence(z, RES, az, el)
    s, a = np.radians(T.slope_deg(z, RES).astype(float)), np.radians(T.aspect_deg(z, RES))
    zen = np.radians(90 - el)
    ref = np.cos(zen) * np.cos(s) + np.sin(zen) * np.sin(s) * np.cos(np.radians(az) - a)
    ok = np.isfinite(ref)
    assert np.allclose(ci[ok], ref[ok], atol=1e-5)


def test_hillshade_range_and_dtype() -> None:
    rng = np.random.default_rng(2)
    z = np.cumsum(rng.normal(0, 8, (20, 20)), 0)
    h = T.hillshade(z, RES)
    assert h.dtype == np.uint8 and h.min() >= 0 and h.max() <= 255
    flat = T.hillshade(np.zeros((5, 5)), RES, 315.0, 45.0)
    assert flat[2, 2] == round(255 * np.sin(np.radians(45)))


def test_terrain_stack_shape() -> None:
    st = T.terrain_stack(plane(0.2, 0.1))
    assert st.shape == (4, 9, 11) and st.dtype == np.float32
    assert np.isnan(st[3, 0, 0])


def test_nested_geobox_corners() -> None:
    fine = GeoBox((1001, 1001), Affine(10.0, 0, 489180.0, 0, -10.0, 758070.0), "EPSG:32644")
    c = T.nested_geobox(fine)
    assert c.shape == (334, 334)
    assert (c.transform.c, c.transform.f, c.transform.a) == (489180.0, 758070.0, 30.0)
    cb = T.nested_geobox(fine, buffer_m=1130.0)  # ceil(1130/30) = 38 cells
    assert cb.shape == (334 + 76, 334 + 76)
    assert cb.transform.c == 489180.0 - 38 * 30.0 and cb.transform.f == 758070.0 + 38 * 30.0
    # Every fine pixel falls in exactly one coarse cell: fine pixel k -> coarse k // 3.
    assert (cb.transform.c - fine.transform.c) % 30.0 == 0
    assert T.buffer_cells(1130.0) == 38
    with pytest.raises(ValueError):
        T.nested_geobox(fine, buffer_m=-1)
