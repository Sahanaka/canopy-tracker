"""M1.3 run, sun-grid check and diagnostics around highland.terrain (orchestration + tables).

Everything here reads M1.2 outputs (scenes_used.csv, clear_masks/*.npz, composites) and the
Copernicus DEM, and writes tables under outputs/M1.3/. Pure functions are unit-tested offline;
`run_all` is resumable and is called by notebooks/03_terrain.ipynb.
"""

from __future__ import annotations

import json
import logging
import resource
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pystac
from affine import Affine
from numpy.typing import NDArray
from odc.geo.geobox import GeoBox

from highland import terrain as T

log = logging.getLogger(__name__)

Bool = NDArray[np.bool_]
INDEX_BANDS = ("ndvi", "evi", "ndmi", "nbr")


# --------------------------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------------------------


def geobox_from_meta(path: str | Path) -> GeoBox:
    """Return the 10 m EPSG:32644 GeoBox recorded in an M1.2 meta JSON (shape, affine transform)."""
    p = json.loads(Path(path).read_text())["params"]
    return GeoBox(tuple(p["shape"]), Affine(*p["transform"][:6]), "EPSG:32644")


def coarse_index(coarse: GeoBox, target: GeoBox) -> tuple[NDArray[np.intp], NDArray[np.intp]]:
    """Return (row, col) index vectors of the coarse cell containing each target pixel centre."""
    tc, tt = coarse.transform, target.transform
    ny, nx = target.shape
    xc = tt.c + tt.a * (np.arange(nx) + 0.5)
    yc = tt.f + tt.e * (np.arange(ny) + 0.5)
    cols = np.floor((xc - tc.c) / tc.a).astype(np.intp)
    rows = np.floor((yc - tc.f) / tc.e).astype(np.intp)
    if (
        rows.min() < 0
        or cols.min() < 0
        or rows.max() >= coarse.shape[0]
        or cols.max() >= coarse.shape[1]
    ):
        raise ValueError("Target pixel centres fall outside the coarse grid.")
    return rows, cols


def sample_coarse(a: NDArray, rows: NDArray[np.intp], cols: NDArray[np.intp]) -> NDArray:
    """Return a[rows][:, cols] (nearest: each target pixel takes its containing coarse cell)."""
    return a[np.ix_(rows, cols)]


def shift_correlation(a: NDArray, b: NDArray, max_shift: int = 3) -> pd.DataFrame:
    """Return Pearson r between a[y, x] and b[y + dy, x + dx] for |dy|, |dx| <= max_shift pixels.

    Both arrays are (y, x) float with NaN for no data; the overlap is the same interior window
    for every shift, so r values are comparable. Columns: dy, dx, r, n.
    """
    m = max_shift
    ref = a[m : a.shape[0] - m, m : a.shape[1] - m]
    rows = []
    for dy in range(-m, m + 1):
        for dx in range(-m, m + 1):
            sh = b[m + dy : b.shape[0] - m + dy, m + dx : b.shape[1] - m + dx]
            ok = np.isfinite(ref) & np.isfinite(sh)
            n = int(ok.sum())
            r = float(np.corrcoef(ref[ok], sh[ok])[0, 1]) if n > 2 else float("nan")
            rows.append({"dy": dy, "dx": dx, "r": r, "n": n})
    return pd.DataFrame(rows)


def binned_medians(x: NDArray, ys: Mapping[str, NDArray], n_bins: int = 10) -> pd.DataFrame:
    """Return per-quantile-bin medians of each y over finite x (bins are x quantiles)."""
    ok = np.isfinite(x)
    for y in ys.values():
        ok &= np.isfinite(y)
    xs = x[ok]
    edges = np.unique(np.quantile(xs, np.linspace(0, 1, n_bins + 1)))
    which = np.clip(np.searchsorted(edges, xs, side="right") - 1, 0, len(edges) - 2)
    rows = []
    for k in range(len(edges) - 1):
        sel = which == k
        row: dict[str, float] = {
            "bin": k,
            "x_lo": float(edges[k]),
            "x_hi": float(edges[k + 1]),
            "n": int(sel.sum()),
        }
        for name, y in ys.items():
            row[f"{name}_median"] = float(np.median(y[ok][sel])) if sel.any() else float("nan")
        rows.append(row)
    return pd.DataFrame(rows)


def stats_row(a: NDArray) -> dict[str, float]:
    """Return median, p01 and p99 of the finite values of a (NaN if none)."""
    v = a[np.isfinite(a)]
    if v.size == 0:
        return {"median": np.nan, "p01": np.nan, "p99": np.nan}
    p01, med, p99 = np.percentile(v, [1, 50, 99])
    return {"median": float(med), "p01": float(p01), "p99": float(p99)}


def with_without(
    indices: NDArray, shadow: NDArray, inside: Bool, names: Sequence[str] = ("ndvi", "nbr", "ndmi")
) -> list[dict[str, Any]]:
    """Return AOI median/p01/p99 of indices for all pixels and for D6-unflagged pixels.

    `indices` is (4, y, x) in INDEX_BANDS order, `shadow` the (4, y, x) shadow bands. 'unflagged'
    drops pixels with shadow_share >= 0.5 (ADR 0016); 'no_shadow_obs' keeps only n_shadow == 0;
    'cos_i_above_aoi_p10/p25' drop the least-lit 10/25 % of pixels (sensitivity only, no cut).
    """
    flag = T.shadow_flag(shadow[0])
    none = shadow[1] == 0
    cos_m = np.where(inside, shadow[2], np.nan)
    q10, q25 = (
        np.nanpercentile(cos_m, q) if np.isfinite(cos_m).any() else np.nan for q in (10, 25)
    )
    subsets = {
        "all": np.ones_like(inside),
        "unflagged": ~flag,
        "no_shadow_obs": none,
        # Sensitivity only (not a recommended cut): drop the least-lit 10 / 25 % of AOI pixels.
        "cos_i_above_aoi_p10": cos_m >= q10,
        "cos_i_above_aoi_p25": cos_m >= q25,
    }
    rows = []
    for name in names:
        a = np.where(inside, indices[INDEX_BANDS.index(name)], np.nan)
        finite = np.isfinite(a)
        n = int(finite.sum())
        for label, keep in subsets.items():
            sel = np.where(keep, a, np.nan)
            rows.append(
                {
                    "index": name,
                    "subset": label,
                    "n_pixels": int(np.isfinite(sel).sum()),
                    "flagged_share": float((flag & finite).sum() / n) if n else np.nan,
                    **stats_row(sel),
                }
            )
    return rows


def scl2_overlap(
    scl2: Bool, model: Bool, cos_i: NDArray, clear: Bool, inside: Bool
) -> dict[str, float]:
    """Return how modelled shadow relates to SCL class 2 on one grid (inside the AOI only).

    Keys: share_scl2, share_model (chance levels), p_model_given_scl2, p_scl2_given_model (NaN
    if the conditioning set is empty), cos_i_mean_scl2 and cos_i_mean_clear (SCL 4/5/6 pixels).
    """
    s, m = scl2 & inside, model & inside
    n = int(inside.sum())
    c = clear & inside & ~scl2

    def ratio(num: int, den: int) -> float:
        return num / den if den else float("nan")

    return {
        "share_scl2": float(s.sum() / n),
        "share_model": float(m.sum() / n),
        "p_model_given_scl2": ratio(int((s & m).sum()), int(s.sum())),
        "p_scl2_given_model": ratio(int((s & m).sum()), int(m.sum())),
        "cos_i_mean_scl2": float(cos_i[s].mean()) if s.any() else float("nan"),
        "cos_i_mean_clear": float(cos_i[c].mean()) if c.any() else float("nan"),
    }


# --------------------------------------------------------------------------------------------
# MTD_TL sun-angle grid (D5)
# --------------------------------------------------------------------------------------------


def fetch_sun_grid(item: pystac.Item, timeout: float = 120.0) -> dict[str, Any]:
    """Download and parse the granule-metadata (MTD_TL.xml) of a Planetary Computer S2 item."""
    import planetary_computer
    import requests

    href = planetary_computer.sign(item).assets["granule-metadata"].href
    resp = requests.get(href, timeout=timeout)
    resp.raise_for_status()
    return T.parse_sun_grid(resp.content)


def sun_grid_table(
    scenes: pd.DataFrame,
    items_by_id: Mapping[str, pystac.Item],
    points_xy: Sequence[tuple[float, float]],
    workers: int = 8,
    fetch: Callable[[pystac.Item], dict[str, Any]] = fetch_sun_grid,
) -> pd.DataFrame:
    """Return per-scene tile-mean vs AOI sun angles (deg) from the MTD_TL grids at `points_xy` (m).

    One row per scene in `scenes` (needs acq_id, item_ids, sun_azimuth_deg, sun_elevation_deg).
    The first point should be the AOI centre. Columns include the CSV angles, the XML tile mean,
    the AOI-centre values and T.sun_grid_delta's keys.
    """

    def one(row: pd.Series) -> dict[str, Any]:
        item = items_by_id[str(row["item_ids"])]
        g = fetch(item)
        d = T.sun_grid_delta(g, points_xy)
        az_c, zen_c = T.sun_angles_at(g, *points_xy[0])
        return {
            "acq_id": row["acq_id"],
            "csv_azimuth_deg": row["sun_azimuth_deg"],
            "csv_zenith_deg": 90.0 - row["sun_elevation_deg"],
            "xml_mean_azimuth_deg": g["mean_azimuth"],
            "xml_mean_zenith_deg": g["mean_zenith"],
            "aoi_centre_azimuth_deg": az_c,
            "aoi_centre_zenith_deg": zen_c,
            **d,
        }

    with ThreadPoolExecutor(workers) as ex:
        rows = list(ex.map(one, [r for _, r in scenes.iterrows()]))
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------------------------
# Summaries from the per-window sidecars
# --------------------------------------------------------------------------------------------


def collect_scene_table(out_dir: str | Path) -> pd.DataFrame:
    """Return the per-scene shadow table (one row per scene) from <stem>_shadow.json sidecars."""
    rows = []
    for p in sorted(Path(out_dir).glob("s2_*_shadow.json")):
        d = json.loads(p.read_text())
        stem = p.name.removesuffix("_shadow.json")
        _, sy, win = stem.split("_")
        rows.extend({"season": win, "season_year": int(sy), **sc} for sc in d["scenes"])
    return pd.DataFrame(rows)


def window_summary(out_dir: str | Path, inside: Bool) -> pd.DataFrame:
    """Return the per window-year shadow summary from the shadow COGs and JSON (shares 0-1)."""
    import rasterio

    rows = []
    n_in = int(inside.sum())
    for p in sorted(Path(out_dir).glob("s2_*_shadow.tif")):
        stem = p.name.removesuffix("_shadow.tif")
        _, sy, win = stem.split("_")
        meta = json.loads(p.with_suffix(".json").read_text())
        with rasterio.open(p) as ds:
            a = ds.read()
        share, n_sh, cos_mean, cos_min = a
        obs = np.isfinite(share) & inside
        cm = cos_mean[obs]
        rows.append(
            {
                "season": win,
                "season_year": int(sy),
                "n_scenes": meta["n_scenes"],
                "aoi_share_with_obs": float(obs.sum() / n_in),
                "share_flag_ge_half": meta["share_flag_ge_half"],
                "share_any_shadow": meta["share_any_shadow"],
                "mean_shadow_share": float(np.nanmean(share[obs])) if obs.any() else np.nan,
                "max_shadow_share": float(np.nanmax(share[obs])) if obs.any() else np.nan,
                "cos_i_mean_p01": float(np.percentile(cm, 1)) if cm.size else np.nan,
                "cos_i_mean_p50": float(np.percentile(cm, 50)) if cm.size else np.nan,
                "cos_i_mean_p99": float(np.percentile(cm, 99)) if cm.size else np.nan,
                "cos_i_min_min": float(np.nanmin(cos_min[obs])) if obs.any() else np.nan,
            }
        )
    return pd.DataFrame(rows)


def _rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


# --------------------------------------------------------------------------------------------
# Full run
# --------------------------------------------------------------------------------------------

M11_ITEMS = "outputs/M1.1/cache/items_s2_2015-06-01_2026-11-02.json"


def _timed(timings: dict[str, Any], key: str, t0: float) -> None:
    timings[key] = {"seconds": round(time.time() - t0, 2), "maxrss_mb": round(_rss_mb(), 1)}


def gdal_hillshade_check(
    dem_tif: str | Path, work_dir: str | Path, mine: NDArray
) -> dict[str, Any]:
    """Compare T.hillshade (uint8) with `gdaldem hillshade` (defaults 315/45), interior cells."""
    import shutil
    import subprocess

    import rasterio

    exe = shutil.which("gdaldem")
    if exe is None:
        return {"available": False}
    dst = Path(work_dir) / "gdal_hillshade.tif"
    subprocess.run([exe, "hillshade", str(dem_tif), str(dst), "-q"], check=True)
    with rasterio.open(dst) as ds:
        g = ds.read(1).astype(np.int16)
    dst.unlink()
    d = np.abs(g[1:-1, 1:-1] - mine[1:-1, 1:-1].astype(np.int16))
    return {
        "available": True,
        "max_abs_diff_dn": int(d.max()),
        "share_diff_gt_1": float((d > 1).mean()),
        "mean_abs_diff_dn": float(d.mean()),
    }


def run_all(
    root: str | Path,
    out_dir: str | Path | None = None,
    fetch_mtd: bool = True,
    workers: int = 8,
    force: bool = False,
    only: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Run the whole M1.3 computation from M1.2 outputs; return timings, memory and key numbers.

    Resumable: per-window shadow COGs are skipped when their inputs are unchanged. `only` limits
    the window-years (stems like 's2_2023_SWM') for pilots. Writes DEM and terrain COGs, the
    per-window shadow COGs, and the CSV tables listed in docs/plans/M1.3.md under out_dir.
    """
    import rasterio

    from highland.aoi import load_aoi
    from highland.inventory import aoi_inside_mask
    from highland.io import write_cog

    root = Path(root)
    out = Path(out_dir) if out_dir else root / "outputs" / "M1.3"
    out.mkdir(parents=True, exist_ok=True)
    m12 = root / "outputs" / "M1.2"
    timings: dict[str, Any] = {}
    result: dict[str, Any] = {}

    t0 = time.time()
    aoi = load_aoi()
    metas = sorted((m12 / "meta").glob("s2_*.json"))
    fine = geobox_from_meta(metas[0])
    for m in metas[1:]:
        g = geobox_from_meta(m)
        if g.transform != fine.transform or tuple(g.shape) != tuple(fine.shape):
            raise RuntimeError(f"{m.name}: grid differs from {metas[0].name}.")
    scenes = pd.read_csv(m12 / "scenes_used.csv")
    elev_min = float(scenes["sun_elevation_deg"].min())
    elev_max = float(scenes["sun_elevation_deg"].max())

    # AOI-only DEM first (data-derived z_min), then the horizon buffer from it.
    aoi_box = T.nested_geobox(fine, 3, 0.0)
    dem0 = T.load_cop_dem(aoi, aoi_box)
    inside30 = aoi_inside_mask(aoi, aoi_box)
    z_min = float(dem0.values[inside30].min())
    z_max_aoi = float(dem0.values[inside30].max())
    w_m = T.horizon_buffer_m(z_min, elev_min)
    nb = T.buffer_cells(w_m)
    box = T.nested_geobox(fine, 3, w_m)
    dem = T.load_cop_dem(aoi, box)
    z = dem.values
    inner = (slice(nb, z.shape[0] - nb), slice(nb, z.shape[1] - nb))
    # GDAL's warp approximates the transform per output window, so the same cell can differ by
    # a fraction of a metre between the AOI-only and the buffered load (<< DEM accuracy, 2-4 m).
    crop_diff = float(np.abs(z[inner] - dem0.values).max())
    if crop_diff > 1.0:
        raise RuntimeError(f"AOI crop of the buffered DEM differs by {crop_diff} m (> 1 m).")
    z_min_buf = float(z[inner][inside30].min())
    w_check = T.horizon_buffer_m(z_min_buf, elev_min)
    if w_check > nb * T.TERRAIN_RES_M:
        raise RuntimeError(f"Buffer {nb * 30} m is smaller than the {w_check} m required.")
    result.update(
        {
            "dem_items": dem.attrs["items"],
            "elev_min_deg": elev_min,
            "elev_max_deg": elev_max,
            "z_min_aoi_m": z_min,
            "z_min_aoi_buffered_load_m": z_min_buf,
            "buffer_m_required_buffered_load": w_check,
            "aoi_only_vs_buffered_load_max_diff_m": crop_diff,
            "z_max_aoi_m": z_max_aoi,
            "z_max_buffer_m": float(z.max()),
            "buffer_m_required": w_m,
            "buffer_cells": nb,
            "buffer_m_used": nb * T.TERRAIN_RES_M,
            "grid_fine": list(fine.shape),
            "grid_coarse_aoi": list(aoi_box.shape),
            "grid_coarse_buffered": list(box.shape),
        }
    )
    write_cog(z[None], out / "dem_30m_buffer.tif", box.transform, "EPSG:32644", np.nan,
              descriptions=["dem_m_EGM2008"],
              tags={"source": "Copernicus GLO-30 DSM (cop-dem-glo-30), bilinear to 30 m UTM",
                    "items": dem.attrs["items"], "vertical_datum": "EGM2008 (m)"})  # fmt: skip
    st = T.terrain_stack(z)[:, inner[0], inner[1]]
    write_cog(st, out / "terrain_30m.tif", aoi_box.transform, "EPSG:32644", np.nan,
              descriptions=["dem_m_EGM2008", "slope_deg", "aspect_deg", "hillshade_az315_alt45"],
              tags={"note": "DSM-derived; Horn 3x3 at 30 m; hillshade display only"})  # fmt: skip
    slope = st[1][inside30]
    result["slope_deg"] = {
        "p01": float(np.nanpercentile(slope, 1)),
        "median": float(np.nanmedian(slope)),
        "p99": float(np.nanpercentile(slope, 99)),
        "max": float(np.nanmax(slope)),
        "share_gt_elev_min": float((slope > elev_min).mean()),
    }
    hs = T.hillshade(z, T.TERRAIN_RES_M)
    result["gdal_hillshade"] = gdal_hillshade_check(out / "dem_30m_buffer.tif", out, hs)
    _timed(timings, "dem_and_terrain", t0)

    # Per-window shadow COGs.
    t0 = time.time()
    inside = aoi_inside_mask(aoi, fine)
    masks = sorted((m12 / "clear_masks").glob("s2_*.npz"))
    if only:
        masks = [m for m in masks if m.stem in only]
    per_window = {}
    for npz in masks:
        t1 = time.time()
        T.composite_shadow(npz, z, nb, fine, inside, out, force=force)
        per_window[npz.stem] = round(time.time() - t1, 2)
        log.info("%s done in %.1fs (rss %.0f MB)", npz.stem, per_window[npz.stem], _rss_mb())
    timings["per_window_seconds"] = per_window
    _timed(timings, "composite_shadow", t0)

    collect_scene_table(out).to_csv(out / "scene_shadow.csv", index=False)
    summ = window_summary(out, inside)
    m12_summ = pd.read_csv(m12 / "composite_summary.csv")
    keep = [
        c
        for c in ("season", "season_year", "n_obs_median", "share_thin_1_2", "thin_season_year")
        if c in m12_summ
    ]
    summ = summ.merge(m12_summ[keep], on=["season", "season_year"], how="left")
    summ.to_csv(out / "shadow_summary.csv", index=False)

    # With / without the flag.
    ww = []
    for p in sorted(out.glob("s2_*_shadow.tif")):
        stem = p.name.removesuffix("_shadow.tif")
        if only and stem not in only:
            continue
        _, sy, win = stem.split("_")
        with rasterio.open(p) as ds:
            sh = ds.read()
        with rasterio.open(m12 / f"{stem}_indices.tif") as ds:
            idx = ds.read()
        ww.extend(
            {"season": win, "season_year": int(sy), **r} for r in with_without(idx, sh, inside)
        )
    pd.DataFrame(ww).to_csv(out / "with_without_flag.csv", index=False)

    result["timings"] = timings
    (out / "run_timings.json").write_text(
        json.dumps({"timings": timings, "result": result}, indent=1)
    )
    return result


def run_diagnostics(
    root: str | Path,
    out_dir: str | Path | None = None,
    fetch_mtd: bool = True,
    workers: int = 8,
    windows: Sequence[str] = ("s2_2023_SWM", "s2_2023_NEM"),
) -> dict[str, Any]:
    """Write sun_grid_check.csv, scl2_vs_model.csv, illumination_bins.csv, alignment_shift.csv.

    Needs run_all's outputs. The sun-grid check downloads one MTD_TL.xml per scene (network);
    the SCL-2 check reads the M1.1 per-acquisition cache (20 m); the others read M1.2/M1.3 COGs.
    """
    import rasterio

    from highland import stac
    from highland.aoi import load_aoi
    from highland.inventory import aoi_inside_mask

    root = Path(root)
    out = Path(out_dir) if out_dir else root / "outputs" / "M1.3"
    m12, m11 = root / "outputs" / "M1.2", root / "outputs" / "M1.1"
    aoi = load_aoi()
    fine = geobox_from_meta(sorted((m12 / "meta").glob("s2_*.json"))[0])
    inside = aoi_inside_mask(aoi, fine)
    scenes = pd.read_csv(m12 / "scenes_used.csv")
    timings: dict[str, Any] = {}
    result: dict[str, Any] = {}

    # D5: tile-mean angles versus the MTD_TL grid at the AOI.
    if fetch_mtd:
        t0 = time.time()
        raw = stac.load_items(root / M11_ITEMS)
        items, _ = stac.dedupe_s2(raw)
        by_id = {i.id: i for i in items}
        tr = fine.transform
        x0, y0 = tr.c, tr.f
        x1, y1 = tr.c + tr.a * fine.shape[1], tr.f + tr.e * fine.shape[0]
        pts = [((x0 + x1) / 2, (y0 + y1) / 2), (x0, y0), (x1, y0), (x0, y1), (x1, y1)]
        tab = sun_grid_table(scenes, by_id, pts, workers=workers)
        tab.to_csv(out / "sun_grid_check.csv", index=False)
        _timed(timings, "sun_grid_check", t0)

    # Class-2 diagnostic: modelled shadow vs SCL class 2 at the 20 m SCL grid.
    t0 = time.time()
    with rasterio.open(out / "dem_30m_buffer.tif") as ds:
        z = ds.read(1).astype(np.float32)
        tr_buf = ds.transform
    ny30 = int(np.ceil(fine.shape[0] / 3))
    nb = (z.shape[0] - ny30) // 2
    box30 = GeoBox(
        (ny30, int(np.ceil(fine.shape[1] / 3))),
        Affine(30, 0, fine.transform.c, 0, -30, fine.transform.f),
        "EPSG:32644",
    )
    if (tr_buf.c, tr_buf.f) != (box30.transform.c - nb * 30, box30.transform.f + nb * 30):
        raise RuntimeError("dem_30m_buffer.tif is not nested in the M1.2 grid.")
    rows_out = []
    gb20 = None
    for _, sc in scenes[scenes["scl_share_2"] > 0].iterrows():
        cache = m11 / "cache" / "s2" / f"{sc['acq_id']}.npz"
        with np.load(cache) as zc:
            meta = json.loads(str(zc["_meta"]))
            shape = tuple(int(v) for v in zc["_shape"])
            n = int(np.prod(shape))
            base = np.unpackbits(zc["clear_base"], count=n).astype(bool).reshape(shape)
            keep2 = np.unpackbits(zc["clear_keep2"], count=n).astype(bool).reshape(shape)
        if gb20 is None:
            gb20 = GeoBox(shape, Affine(*meta["transform"][:6]), "EPSG:32644")
            ins20 = aoi_inside_mask(aoi, gb20)
            ri, ci = coarse_index(box30, gb20)
        sh = T.scene_shadow(
            z, 30.0, float(sc["sun_azimuth_deg"]), float(sc["sun_elevation_deg"]), nb
        )
        model20 = sample_coarse(sh["any"], ri, ci)
        cos20 = sample_coarse(sh["cos_i"], ri, ci)
        r = scl2_overlap(keep2 & ~base, model20, cos20, base, ins20)
        bl = stac.parse_baseline(str(sc["baseline"]))
        rows_out.append(
            {
                "acq_id": sc["acq_id"],
                "baseline": sc["baseline"],
                "era": "<04.00" if bl < (4, 0) else ">=04.00",
                **r,
            }
        )
    pd.DataFrame(rows_out).to_csv(out / "scl2_vs_model.csv", index=False)
    _timed(timings, "scl2_vs_model", t0)

    # Illumination bins and alignment shift for two windows.
    bins, shifts = [], []
    for stem in windows:
        with rasterio.open(out / f"{stem}_shadow.tif") as ds:
            cosm = ds.read(3)
        with rasterio.open(m12 / f"{stem}_reflectance.tif") as ds:
            nir = ds.read(4)
            assert ds.descriptions[3] == "nir"
        with rasterio.open(m12 / f"{stem}_indices.tif") as ds:
            ndvi = ds.read(1)
        cosm = np.where(inside, cosm, np.nan)
        b = binned_medians(
            cosm, {"nir_b08": np.where(inside, nir, np.nan), "ndvi": np.where(inside, ndvi, np.nan)}
        )
        b.insert(0, "window", stem)
        bins.append(b)
        s = shift_correlation(np.where(inside, nir, np.nan), cosm, 3)
        s.insert(0, "window", stem)
        shifts.append(s)
        best = s.loc[s["r"].idxmax()]
        result[f"best_shift_{stem}"] = {
            "dy": int(best.dy),
            "dx": int(best.dx),
            "r": float(best.r),
            "r_at_0": float(s[(s.dy == 0) & (s.dx == 0)].r.iloc[0]),
        }
    pd.concat(bins).to_csv(out / "illumination_bins.csv", index=False)
    pd.concat(shifts).to_csv(out / "alignment_shift.csv", index=False)
    result["timings"] = timings
    return result
