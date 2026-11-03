"""Spectral indices (NDVI, EVI, NDMI, NBR) on 0-1 reflectance."""

from __future__ import annotations

import numpy as np
import xarray as xr
from numpy.typing import ArrayLike, NDArray

INDEX_NAMES: tuple[str, ...] = ("ndvi", "evi", "ndmi", "nbr")


def _f32(*arrays: ArrayLike) -> list[NDArray[np.float32]]:
    return [np.asarray(a, dtype=np.float32) for a in arrays]


def _safe_ratio(num: NDArray[np.float32], den: NDArray[np.float32]) -> NDArray[np.float32]:
    """Return num/den as float32, NaN where den is exactly 0 or any input is NaN."""
    with np.errstate(divide="ignore", invalid="ignore"):
        out = num / den
    out = np.where(den == 0, np.float32(np.nan), out)
    return out.astype(np.float32)


def normalized_difference(a: ArrayLike, b: ArrayLike) -> NDArray[np.float32]:
    """Return (a - b) / (a + b) as float32 (unitless, usually -1..1); NaN where a + b == 0."""
    x, y = _f32(a, b)
    return _safe_ratio(x - y, x + y)


def ndvi(red: ArrayLike, nir: ArrayLike) -> NDArray[np.float32]:
    """Return NDVI = (NIR - Red) / (NIR + Red) from 0-1 reflectance (S2: red B04, NIR B08)."""
    r, n = _f32(red, nir)
    return _safe_ratio(n - r, n + r)


def evi(blue: ArrayLike, red: ArrayLike, nir: ArrayLike) -> NDArray[np.float32]:
    """Return EVI = 2.5 (NIR-Red) / (NIR + 6 Red - 7.5 Blue + 1) from 0-1 reflectance."""
    b, r, n = _f32(blue, red, nir)
    den = n + np.float32(6.0) * r - np.float32(7.5) * b + np.float32(1.0)
    return _safe_ratio(np.float32(2.5) * (n - r), den)


def ndmi(nir: ArrayLike, swir1: ArrayLike) -> NDArray[np.float32]:
    """Return NDMI = (NIR - SWIR1) / (NIR + SWIR1), the moisture index (S2: B8A, B11), not NDWI."""
    n, s = _f32(nir, swir1)
    return _safe_ratio(n - s, n + s)


def nbr(nir: ArrayLike, swir2: ArrayLike) -> NDArray[np.float32]:
    """Return NBR = (NIR - SWIR2) / (NIR + SWIR2) from 0-1 reflectance (S2: B8A, B12)."""
    n, s = _f32(nir, swir2)
    return _safe_ratio(n - s, n + s)


def all_indices(ds: xr.Dataset) -> xr.Dataset:
    """Return float32 NDVI, EVI, NDMI, NBR from a dataset of 0-1 reflectance (lazy-safe).

    Needs variables blue, red, nir (B08), nir08 (B8A), swir16 (B11) and swir22 (B12).
    """

    def apply(fn, *names: str) -> xr.DataArray:  # type: ignore[no-untyped-def]
        return xr.apply_ufunc(
            fn,
            *[ds[n] for n in names],
            dask="parallelized",
            output_dtypes=[np.float32],
        )

    return xr.Dataset(
        {
            "ndvi": apply(ndvi, "red", "nir"),
            "evi": apply(evi, "blue", "red", "nir"),
            "ndmi": apply(ndmi, "nir08", "swir16"),
            "nbr": apply(nbr, "nir08", "swir22"),
        }
    )


def out_of_range_share(index: ArrayLike, lo: float = -1.0, hi: float = 1.0) -> float:
    """Return the share (0-1) of finite values outside [lo, hi]; NaN if there are none."""
    a = np.asarray(index, dtype=np.float32)
    finite = np.isfinite(a)
    n = int(finite.sum())
    if n == 0:
        return float("nan")
    return float(((a < lo) | (a > hi))[finite].sum() / n)
