"""Offline tests for highland.terrain_checks (pure helpers, synthetic arrays)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from affine import Affine
from odc.geo.geobox import GeoBox

from highland import terrain as T
from highland import terrain_checks as C

FIXTURE = Path(__file__).parent / "fixtures" / "terrain" / "MTD_TL_44NMN_20230601.xml"


def test_coarse_index_20m_in_30m() -> None:
    coarse = GeoBox((334, 334), Affine(30, 0, 489180.0, 0, -30, 758070.0), "EPSG:32644")
    gb20 = GeoBox((501, 501), Affine(20, 0, 489180.0, 0, -20, 758080.0), "EPSG:32644")
    rows, cols = C.coarse_index(coarse, gb20)
    # 20 m col j has centre x = 489180 + 20 j + 10; the 30 m col is floor((20 j + 10) / 30)
    assert cols[:4].tolist() == [0, 1, 1, 2] and cols[-1] == 333
    # 20 m row i has centre y = 758070 - 20 i; the 30 m row is floor(20 i / 30)
    assert rows[0] == 0 and rows[1] == 0 and rows[2] == 1 and rows[3] == 2 and rows[-1] == 333
    a = np.arange(334 * 334).reshape(334, 334)
    assert C.sample_coarse(a, rows, cols).shape == (501, 501)
    small = GeoBox((10, 10), Affine(30, 0, 489180.0, 0, -30, 758070.0), "EPSG:32644")
    with pytest.raises(ValueError):
        C.coarse_index(small, gb20)


def test_shift_correlation_finds_known_shift() -> None:
    rng = np.random.default_rng(0)
    base = rng.random((60, 60))
    b = np.roll(base, shift=(2, -1), axis=(0, 1))  # b[y+2, x-1] = base[y, x]
    t = C.shift_correlation(base, b, 3)
    best = t.loc[t["r"].idxmax()]
    assert (best.dy, best.dx) == (2, -1) and best.r == pytest.approx(1.0)
    assert t["n"].nunique() == 1 and len(t) == 49


def test_binned_medians_uses_quantiles() -> None:
    x = np.linspace(0, 1, 1000)
    t = C.binned_medians(x, {"y": 2 * x}, n_bins=10)
    assert len(t) == 10 and t["n"].sum() == 1000
    assert t["y_median"].is_monotonic_increasing
    assert t.loc[0, "x_lo"] == 0.0 and t.loc[9, "x_hi"] == 1.0


def test_with_without_drops_flagged() -> None:
    idx = np.stack([np.full((4, 4), v, np.float32) for v in (0.8, 0.0, 0.0, 0.0)])
    idx[0, 0, 0] = 0.1  # one dark pixel
    share = np.zeros((4, 4), np.float32)
    share[0, 0] = 0.75
    n_sh = np.zeros((4, 4), np.float32)
    n_sh[0, 0] = 3
    cos_m = np.linspace(0.3, 0.9, 16, dtype=np.float32).reshape(4, 4)
    sh = np.stack([share, n_sh, cos_m, cos_m])
    rows = C.with_without(idx, sh, np.ones((4, 4), bool), names=("ndvi",))
    by = {r["subset"]: r for r in rows}
    assert by["all"]["n_pixels"] == 16 and by["unflagged"]["n_pixels"] == 15
    assert by["no_shadow_obs"]["n_pixels"] == 15
    assert by["unflagged"]["median"] == pytest.approx(0.8) and by["all"]["flagged_share"] == 1 / 16
    assert by["cos_i_above_aoi_p10"]["n_pixels"] < 16


def test_scl2_overlap() -> None:
    scl2 = np.zeros((4, 4), bool)
    scl2[0, :2] = True
    model = np.zeros_like(scl2)
    model[0, 0] = True
    model[3, 3] = True
    clear = np.ones_like(scl2)
    cos_i = np.full((4, 4), 0.8, np.float32)
    cos_i[0, :2] = 0.3
    r = C.scl2_overlap(scl2, model, cos_i, clear, np.ones_like(scl2))
    assert r["p_model_given_scl2"] == 0.5 and r["p_scl2_given_model"] == 0.5
    assert r["share_scl2"] == 2 / 16 and r["share_model"] == 2 / 16
    assert r["cos_i_mean_scl2"] == pytest.approx(0.3) and r["cos_i_mean_clear"] == pytest.approx(
        0.8
    )
    empty = C.scl2_overlap(np.zeros_like(scl2), model, cos_i, clear, np.ones_like(scl2))
    assert np.isnan(empty["p_model_given_scl2"])


def test_sun_grid_table_with_fake_fetch() -> None:
    grid = T.parse_sun_grid(FIXTURE.read_bytes())
    scenes = pd.DataFrame(
        {
            "acq_id": ["a"],
            "item_ids": ["item-a"],
            "sun_azimuth_deg": [grid["mean_azimuth"]],
            "sun_elevation_deg": [90 - grid["mean_zenith"]],
        }
    )
    pts = [(494185.0, 753065.0), (489180.0, 758070.0), (499190.0, 748060.0)]
    t = C.sun_grid_table(
        scenes, {"item-a": SimpleNamespace()}, pts, workers=1, fetch=lambda _i: grid
    )  # type: ignore[arg-type]
    assert len(t) == 1 and t.loc[0, "csv_zenith_deg"] == pytest.approx(grid["mean_zenith"])
    assert t.loc[0, "slope_equiv_deg"] >= 0 and t.loc[0, "spread_zen"] >= 0
