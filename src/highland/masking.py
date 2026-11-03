"""Sentinel-2 SCL and Landsat QA_PIXEL cloud and shadow masks."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from highland.stac import parse_baseline

# Sentinel-2 L2A Scene Classification Layer (SCL) classes, as listed in CLAUDE.md.
SCL_CLASSES: dict[int, str] = {
    0: "no_data",
    1: "saturated_defective",
    2: "cast_shadow",  # topographic shadow; "dark features" before baseline 04.00
    3: "cloud_shadow",
    4: "vegetation",
    5: "not_vegetated",
    6: "water",
    7: "unclassified",
    8: "cloud_medium_prob",
    9: "cloud_high_prob",
    10: "thin_cirrus",
    11: "snow_ice",
}
# Always kept as clear. Classes 2 and 7 are configurable (decided in the M1.2 ADR).
SCL_KEEP_BASE: frozenset[int] = frozenset({4, 5, 6})
# M1.2 keep-set (ADR 0012): class 2 (cast shadow) kept, class 7 (unclassified) masked.
M12_KEEP_CAST_SHADOW = True
M12_KEEP_UNCLASSIFIED = False
SCL_KEEP_M12: frozenset[int] = SCL_KEEP_BASE | {2}

# Sentinel-2 L2A reflectance = (DN + BOA_ADD_OFFSET) / QUANTIFICATION_VALUE for baseline >= 04.00.
S2_BOA_ADD_OFFSET = -1000.0
S2_QUANTIFICATION = 10000.0
S2_OFFSET_BASELINE = (4, 0)

# Landsat Collection 2 QA_PIXEL bit positions (USGS LSDS-1618 v4.0 for L4-7, LSDS-1619 v6 for L8-9).
# Bit 6 ("clear") is deliberately never used: it is unreliable for L4-7 (LSDS-1618 5.2.1).
QA_BITS_L47: dict[str, int] = {"fill": 0, "dilated": 1, "cloud": 3, "shadow": 4, "snow": 5}
QA_BITS_L89: dict[str, int] = {**QA_BITS_L47, "cirrus": 2}  # bit 2 is cirrus on L8/9 only

_L89_PLATFORMS = frozenset({"landsat-8", "landsat-9"})
_L47_PLATFORMS = frozenset({"landsat-4", "landsat-5", "landsat-7"})


def scl_class_shares(scl: NDArray[np.integer], inside: NDArray[np.bool_]) -> dict[int, float]:
    """Return the share (0-1) of the inside-AOI pixels in each SCL class 0-11; sums to 1."""
    scl = np.asarray(scl)
    inside = np.asarray(inside, dtype=bool)
    if scl.shape != inside.shape:
        raise ValueError(f"scl shape {scl.shape} != inside shape {inside.shape}")
    n = int(inside.sum())
    if n == 0:
        raise ValueError("The inside-AOI mask has no pixels.")
    vals = scl[inside]
    if vals.size and (vals.min() < 0 or vals.max() > 11):
        raise ValueError(f"SCL values outside 0-11: min {vals.min()}, max {vals.max()}")
    counts = np.bincount(vals.astype(np.int64), minlength=12)
    return {c: float(counts[c]) / n for c in SCL_CLASSES}


def scl_clear(
    scl: NDArray[np.integer], keep_cast_shadow: bool = False, keep_unclassified: bool = False
) -> NDArray[np.bool_]:
    """Return a boolean clear mask from SCL codes 0-11; classes 2 and 7 kept only if requested."""
    keep = set(SCL_KEEP_BASE)
    if keep_cast_shadow:
        keep.add(2)
    if keep_unclassified:
        keep.add(7)
    return np.isin(np.asarray(scl), sorted(keep))


def s2_dn_to_reflectance(dn: NDArray[np.integer], baseline: str) -> NDArray[np.float32]:
    """Convert S2 L2A uint16 DN to float32 reflectance (0-1); DN 0 -> NaN, negatives kept."""
    arr = np.asarray(dn)
    if arr.dtype.kind not in "ui":
        raise TypeError(f"DN must be an integer array, got {arr.dtype}")
    # Cast to float32 BEFORE subtracting: uint16 - 1000 would wrap around under NumPy 2 (NEP 50).
    x = arr.astype(np.float32)
    offset = S2_BOA_ADD_OFFSET if parse_baseline(baseline) >= S2_OFFSET_BASELINE else 0.0
    out = ((x + np.float32(offset)) / np.float32(S2_QUANTIFICATION)).astype(np.float32)
    out[arr == 0] = np.nan
    return out


def _bits_for(platform: str) -> dict[str, int]:
    if platform in _L89_PLATFORMS:
        return QA_BITS_L89
    if platform in _L47_PLATFORMS:
        return QA_BITS_L47
    raise ValueError(f"Unknown Landsat platform {platform!r}; expected landsat-4/5/7/8/9.")


def qa_flags(qa: NDArray[np.integer], platform: str) -> dict[str, NDArray[np.bool_]]:
    """Return boolean flags from QA_PIXEL (uint16 bit field); cirrus only for landsat-8/9."""
    qa = np.asarray(qa)
    if qa.dtype.kind not in "ui":
        raise TypeError(f"QA_PIXEL must be an integer array, got {qa.dtype}")
    # Widen first so bit shifts are safe and never wrap, whatever the input integer width.
    q = qa.astype(np.uint32)
    return {name: ((q >> bit) & 1).astype(bool) for name, bit in _bits_for(platform).items()}


def qa_clear(qa: NDArray[np.integer], platform: str, mask_cirrus: bool = True) -> NDArray[np.bool_]:
    """Return True where none of QA_PIXEL bits 0, 1, 3, 4, 5 (and 2 on L8/9 if asked) are set."""
    flags = qa_flags(qa, platform)
    bad = np.zeros(np.asarray(qa).shape, dtype=bool)
    for name, flag in flags.items():
        if name == "cirrus" and not mask_cirrus:
            continue
        bad |= flag
    return ~bad
