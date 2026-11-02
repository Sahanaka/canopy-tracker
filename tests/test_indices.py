"""Unit tests for highland.indices on hand-computed synthetic values."""

import numpy as np
import pytest
import xarray as xr

from highland.indices import (
    all_indices,
    evi,
    nbr,
    ndmi,
    ndvi,
    normalized_difference,
    out_of_range_share,
)


def test_ndvi_hand_value() -> None:
    assert ndvi(0.05, 0.45) == pytest.approx(0.8)


def test_evi_hand_value() -> None:
    # 2.5 * 0.4 / (0.45 + 0.3 - 0.15 + 1) = 1.0 / 1.6 = 0.625
    assert evi(0.02, 0.05, 0.45) == pytest.approx(0.625)


def test_ndmi_and_nbr_use_their_own_swir_band() -> None:
    nir, s1, s2 = 0.40, 0.20, 0.10
    assert ndmi(nir, s1) == pytest.approx(0.2 / 0.6)
    assert nbr(nir, s2) == pytest.approx(0.3 / 0.5)
    assert ndmi(nir, s1) != pytest.approx(nbr(nir, s2))


def test_argument_order_sign() -> None:
    assert ndvi(0.4, 0.1) == pytest.approx(-0.6)  # red 0.4, NIR 0.1: negative
    assert normalized_difference(0.3, 0.1) == pytest.approx(0.5)


@pytest.mark.parametrize("fn,args", [(ndvi, 2), (evi, 3), (ndmi, 2), (nbr, 2)])
def test_dtype_nan_propagation_and_zero_denominator(fn, args) -> None:  # type: ignore[no-untyped-def]
    ok = [np.full((2, 2), 0.1, dtype=np.float64) for _ in range(args)]
    out = fn(*ok)
    assert out.dtype == np.float32
    nan_in = [a.copy() for a in ok]
    nan_in[-1][0, 0] = np.nan
    res = fn(*nan_in)
    assert np.isnan(res[0, 0]) and np.isfinite(res[1, 1])
    assert res.dtype == np.float32


def test_zero_denominator_is_nan_no_clipping() -> None:
    z = np.zeros(1, dtype=np.float32)
    assert np.isnan(ndvi(z, z)).all() and np.isnan(ndmi(z, z)).all() and np.isnan(nbr(z, z)).all()
    # EVI denominator: 0.5 + 6 * 0.375 - 7.5 * 0.5 + 1 = 0 exactly in float32.
    assert np.isnan(evi(0.5, 0.375, 0.5))
    # Negative reflectance (kept after the S2 offset) can leave [-1, 1]; nothing is clipped.
    assert ndvi(-0.02, 0.01) == pytest.approx(-3.0, abs=1e-5)  # (0.01+0.02)/(0.01-0.02)
    assert ndvi(np.float32(0.0), np.float32(0.3)) == pytest.approx(1.0)


def test_all_indices_matches_functions_and_is_lazy() -> None:
    rng = np.random.default_rng(0)
    arr = {
        k: rng.uniform(0.02, 0.5, (8, 8)).astype(np.float32)
        for k in ("blue", "red", "nir", "nir08", "swir16", "swir22")
    }
    ds = xr.Dataset({k: (("y", "x"), v) for k, v in arr.items()}).chunk({"x": 4, "y": 4})
    out = all_indices(ds)
    assert all(v.dtype == np.float32 for v in out.data_vars.values())
    np.testing.assert_allclose(out["ndvi"].values, ndvi(arr["red"], arr["nir"]), rtol=1e-6)
    np.testing.assert_allclose(
        out["evi"].values, evi(arr["blue"], arr["red"], arr["nir"]), rtol=1e-6
    )
    np.testing.assert_allclose(out["ndmi"].values, ndmi(arr["nir08"], arr["swir16"]), rtol=1e-6)
    np.testing.assert_allclose(out["nbr"].values, nbr(arr["nir08"], arr["swir22"]), rtol=1e-6)


def test_out_of_range_share() -> None:
    a = np.array([0.5, -2.0, 1.5, np.nan, 1.0, -1.0], dtype=np.float32)
    assert out_of_range_share(a) == pytest.approx(2 / 5)
    assert np.isnan(out_of_range_share(np.array([np.nan])))
