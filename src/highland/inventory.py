"""Per-acquisition availability audit: clear-pixel shares of the AOI from SCL and QA_PIXEL.

Inventory schema notes: one row per acquisition (platform + local solar day). The columns
``collection_category``, ``correction`` and ``relative_orbit`` are the sorted unique values of the
fused items joined with ``+`` (a plain string such as ``"T1"`` or ``"T1+T2"``, never a list).
The 12 ``scl_share_*`` columns are mutually exclusive and sum to 1; the Landsat ``qa_*_share``
columns are overlapping flags (a pixel can be cloud, dilated and shadow) and must not be summed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pystac
from numpy.typing import NDArray
from odc.geo.geobox import GeoBox
from rasterio.features import geometry_mask

from highland.aoi import WORK_CRS
from highland.masking import (
    SCL_CLASSES,
    qa_clear,
    qa_flags,
    scl_class_shares,
    scl_clear,
)
from highland.stac import (
    GRID_ASSET,
    acquisition_groups,
    aoi_geobox,
    local_date,
    parse_baseline,
)

log = logging.getLogger(__name__)

CACHE_SCHEMA_VERSION = 2  # bump when the cached row or mask layout changes
SLC_OFF_START = datetime(2003, 5, 31, tzinfo=UTC)  # Landsat 7 scan-line corrector failure
S2_VARIANTS: tuple[str, ...] = ("clear_base", "clear_keep2", "clear_keep7", "clear_keep27")
LANDSAT_VARIANTS: tuple[str, ...] = ("clear", "clear_nocirrus")
VARIANTS = {"s2": S2_VARIANTS, "landsat": LANDSAT_VARIANTS}
QA_SHARE_NAMES = ("fill", "dilated", "cloud", "shadow", "snow", "cirrus")

_ID_COLS = [
    "sensor",
    "platform",
    "acq_id",
    "datetime_utc",
    "date_local",
    "year",
    "month",
    "item_ids",
    "tiles",
    "baselines",
    "baseline_min",
    "baseline_max",
    "mixed_baseline",
    "collection_category",
    "correction",
    "mixed_tier",
    "relative_orbit",
    "eo_cloud_cover_mean",
    "sun_azimuth_deg",
    "sun_elevation_deg",
]
_TAIL_COLS = ["slc_off", "status", "error"]


def columns(sensor: str) -> list[str]:
    """Return the ordered scene_inventory column names for a sensor ('s2' or 'landsat')."""
    if sensor == "s2":
        px = ["aoi_pixels", "valid_frac"] + [f"scl_share_{c}" for c in SCL_CLASSES]
    else:
        px = ["aoi_pixels", "valid_frac"] + [f"qa_{n}_share" for n in QA_SHARE_NAMES]
    return _ID_COLS + px + list(VARIANTS[sensor]) + _TAIL_COLS


def aoi_inside_mask(aoi: gpd.GeoDataFrame, geobox: GeoBox) -> NDArray[np.bool_]:
    """Rasterise the AOI polygon (pixel centres) onto a GeoBox; True inside the AOI."""
    geom = aoi.to_crs(WORK_CRS).geometry.iloc[0]
    return ~geometry_mask([geom], out_shape=tuple(geobox.shape), transform=geobox.transform)


def read_layer(items: Sequence[pystac.Item], sensor: str, geobox: GeoBox) -> NDArray[np.integer]:
    """Read SCL (uint8, nodata 0) or QA_PIXEL (uint16, nodata 1) of one acquisition.

    Where tiles or rows overlap, the first valid (non-nodata) pixel in item order wins.
    """
    import odc.stac
    import planetary_computer

    asset = GRID_ASSET[sensor]
    dtype, nodata = ("uint8", 0) if sensor == "s2" else ("uint16", 1)
    signed = [planetary_computer.sign(i) for i in items]  # sign just before reading (SAS expiry)
    cfg = {"*": {"assets": {asset: {"data_type": dtype, "nodata": nodata}}}}
    ds = odc.stac.load(
        signed,
        bands=[asset],
        geobox=geobox,
        groupby="solar_day",
        resampling="nearest",
        stac_cfg=cfg,
        fail_on_error=True,
    )
    if ds.sizes["time"] != 1:
        raise RuntimeError(f"Expected 1 time slice for one acquisition, got {ds.sizes['time']}.")
    arr = np.asarray(ds[asset].isel(time=0).values)
    if arr.dtype != np.dtype(dtype):
        raise RuntimeError(f"{asset} loaded as {arr.dtype}, expected {dtype}.")
    return arr


def _mean(items: Sequence[pystac.Item], fn: Callable[[pystac.Item], float | None]) -> float:
    vals = [v for v in (fn(i) for i in items) if v is not None]
    return float(np.mean(vals)) if vals else float("nan")


def _unique(items: Sequence[pystac.Item], prop: str) -> list[str]:
    return sorted({str(i.properties[prop]) for i in items if i.properties.get(prop) is not None})


def _joined(items: Sequence[pystac.Item], prop: str) -> str | None:
    vals = _unique(items, prop)
    return "+".join(vals) if vals else None


def _meta_row(sensor: str, key: str, items: Sequence[pystac.Item]) -> dict[str, object]:
    first = items[0]
    dts = sorted(i.datetime for i in items if i.datetime is not None)
    dt = dts[0]
    p = first.properties
    if sensor == "s2":
        bls = [str(i.properties["s2:processing_baseline"]) for i in items]
        tiles = [str(i.properties["s2:mgrs_tile"]) for i in items]
        az = _mean(items, lambda i: i.properties.get("s2:mean_solar_azimuth"))
        zen = _mean(items, lambda i: i.properties.get("s2:mean_solar_zenith"))
        elev = 90.0 - zen
        bl_max = max(bls, key=parse_baseline)
        bl_min = min(bls, key=parse_baseline)
    else:
        bls = []
        tiles = [
            f"{i.properties.get('landsat:wrs_path')}/{i.properties.get('landsat:wrs_row')}"
            for i in items
        ]
        az = _mean(items, lambda i: i.properties.get("view:sun_azimuth"))
        elev = _mean(items, lambda i: i.properties.get("view:sun_elevation"))
        bl_max = bl_min = None
    local = local_date(dt)
    platform = str(p["platform"]).lower()
    return {
        "sensor": sensor,
        "platform": platform,
        "acq_id": key,
        "datetime_utc": pd.Timestamp(dt).tz_convert("UTC"),
        "date_local": local,
        "year": int(local[:4]),
        "month": int(local[5:7]),
        "item_ids": [i.id for i in items],
        "tiles": tiles,
        "baselines": bls,
        "baseline_min": bl_min,
        "baseline_max": bl_max,
        "mixed_baseline": bool(
            bl_min is not None and parse_baseline(bl_min) != parse_baseline(bl_max)
        ),
        "collection_category": _joined(items, "landsat:collection_category"),
        "correction": _joined(items, "landsat:correction"),
        "mixed_tier": len(_unique(items, "landsat:collection_category")) > 1,
        "relative_orbit": _joined(items, "sat:relative_orbit"),
        "eo_cloud_cover_mean": _mean(items, lambda i: i.properties.get("eo:cloud_cover")),
        "sun_azimuth_deg": az,
        "sun_elevation_deg": elev,
        "slc_off": bool(platform == "landsat-7" and dt >= SLC_OFF_START),
        "status": "ok",
        "error": None,
    }


def audit_acquisition(
    items: Sequence[pystac.Item],
    sensor: str,
    key: str,
    geobox: GeoBox,
    inside: NDArray[np.bool_],
    reader: Callable[..., NDArray[np.integer]] | None = None,
) -> tuple[dict[str, object], dict[str, NDArray[np.bool_]]]:
    """Return one inventory row (shares 0-1 of AOI pixels) and per-pixel clear masks by variant.

    The 12 SCL shares are mutually exclusive and sum to 1. The Landsat qa_*_share flags overlap
    (a pixel can be cloud and dilated and shadow), so they must not be summed.
    """
    reader = reader or read_layer
    row = _meta_row(sensor, key, items)
    layer = reader(items, sensor, geobox)
    if layer.shape != inside.shape:
        raise RuntimeError(f"Layer shape {layer.shape} != AOI mask shape {inside.shape}.")
    n = int(inside.sum())
    row["aoi_pixels"] = n
    clears: dict[str, NDArray[np.bool_]] = {}
    if sensor == "s2":
        shares = scl_class_shares(layer, inside)
        for c, v in shares.items():
            row[f"scl_share_{c}"] = v
        row["valid_frac"] = 1.0 - shares[0]
        variants = {
            "clear_base": (False, False),
            "clear_keep2": (True, False),
            "clear_keep7": (False, True),
            "clear_keep27": (True, True),
        }
        for name, (k2, k7) in variants.items():
            clears[name] = scl_clear(layer, keep_cast_shadow=k2, keep_unclassified=k7)
    else:
        platform = str(row["platform"])
        flags = qa_flags(layer, platform)
        for name in QA_SHARE_NAMES:
            f = flags.get(name)
            row[f"qa_{name}_share"] = float(f[inside].mean()) if f is not None else float("nan")
        row["valid_frac"] = 1.0 - float(row["qa_fill_share"])
        clears["clear"] = qa_clear(layer, platform, mask_cirrus=True)
        clears["clear_nocirrus"] = qa_clear(layer, platform, mask_cirrus=False)
    for name, mask in clears.items():
        row[name] = float(mask[inside].mean())
    return row, clears


def _cache_path(cache_dir: Path, sensor: str, key: str) -> Path:
    return cache_dir / sensor / f"{key}.npz"


def aoi_hash(aoi: gpd.GeoDataFrame) -> str:
    """Return the sha256 hex digest of the AOI polygon's WKB in EPSG:32644."""
    return hashlib.sha256(aoi.to_crs(WORK_CRS).geometry.iloc[0].wkb).hexdigest()


def cache_meta(items: Sequence[pystac.Item], geobox: GeoBox, aoi_sha: str) -> dict[str, object]:
    """Return the identity of a cached acquisition: schema, sorted item ids, grid and AOI hash."""
    t = geobox.transform
    return {
        "schema": CACHE_SCHEMA_VERSION,
        "item_ids": sorted(i.id for i in items),
        "shape": [int(v) for v in geobox.shape],
        "transform": [float(t.a), float(t.b), float(t.c), float(t.d), float(t.e), float(t.f)],
        "crs": str(geobox.crs),
        "aoi_sha256": aoi_sha,
    }


def _write_cache(
    path: Path, row: Mapping[str, object], clears: Mapping[str, NDArray], meta: Mapping[str, object]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    shape = next(iter(clears.values())).shape
    payload = {k: np.packbits(v.ravel()) for k, v in clears.items()}
    row_json = json.dumps(
        row, default=lambda o: o.isoformat() if hasattr(o, "isoformat") else str(o)
    )
    tmp = path.with_suffix(".tmp.npz")
    np.savez_compressed(
        tmp,
        _row=np.array(row_json),
        _shape=np.array(shape),
        _meta=np.array(json.dumps(meta)),
        **payload,
    )
    os.replace(tmp, path)  # atomic: a half-written file is never seen as done


def _read_cache(
    path: Path,
) -> tuple[dict[str, object], dict[str, NDArray[np.bool_]], dict[str, object]]:
    with np.load(path) as z:
        row = json.loads(str(z["_row"]))
        meta = json.loads(str(z["_meta"])) if "_meta" in z.files else {}
        shape = tuple(int(s) for s in z["_shape"])
        n = int(np.prod(shape))
        clears = {
            k: np.unpackbits(z[k], count=n).astype(bool).reshape(shape)
            for k in z.files
            if not k.startswith("_")
        }
    row["datetime_utc"] = pd.Timestamp(row["datetime_utc"])
    return row, clears, meta


def run_audit(
    sensor: str,
    items: Sequence[pystac.Item],
    aoi: gpd.GeoDataFrame,
    out_dir: str | Path,
    resume: bool = True,
    retries: int = 3,
    backoff_s: float = 2.0,
    reader: Callable[..., NDArray[np.integer]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> pd.DataFrame:
    """Audit every acquisition (one at a time); return the inventory with error rows kept."""
    out_dir = Path(out_dir)
    cache_dir = out_dir / "cache"
    if not items:
        log.warning("%s: no items to audit", sensor)
        empty = pd.DataFrame(columns=columns(sensor))
        empty.attrs["n_error"] = 0
        return empty
    geobox = aoi_geobox(aoi, items, sensor)
    inside = aoi_inside_mask(aoi, geobox)
    groups = acquisition_groups(items, sensor)
    aoi_sha = aoi_hash(aoi)
    rows: list[dict[str, object]] = []
    for n, (key, members) in enumerate(groups.items(), 1):
        path = _cache_path(cache_dir, sensor, key)
        meta = cache_meta(members, geobox, aoi_sha)
        if resume and path.exists():
            try:
                cached_row, _, cached_meta = _read_cache(path)
            except Exception as exc:  # noqa: BLE001 - unreadable cache file is a miss
                log.info("%s: cache unreadable (%s); recomputing", key, exc)
            else:
                if cached_meta == meta:
                    rows.append(cached_row)
                    continue
                log.info(
                    "%s: cache is stale (items, grid, AOI or schema changed); recomputing", key
                )
        err: Exception | None = None
        for attempt in range(retries):
            try:
                row, clears = audit_acquisition(members, sensor, key, geobox, inside, reader)
                _write_cache(path, row, clears, meta)
                rows.append(row)
                err = None
                break
            except Exception as exc:  # noqa: BLE001 - any read failure becomes an error row
                err = exc
                log.warning("%s attempt %d/%d failed: %s", key, attempt + 1, retries, exc)
                if attempt < retries - 1:
                    sleep(backoff_s * 2**attempt)
        if err is not None:
            row = _meta_row(sensor, key, members)
            row["status"] = "error"
            row["error"] = f"{type(err).__name__}: {err}"
            rows.append(row)
        if n % 25 == 0:
            log.info("%s: %d/%d acquisitions", sensor, n, len(groups))
    df = pd.DataFrame(rows).reindex(columns=columns(sensor))
    df = df.sort_values("datetime_utc", ignore_index=True)
    df.attrs["n_error"] = int((df["status"] == "error").sum())
    return df


def write_inventory(df: pd.DataFrame, path: str | Path) -> Path:
    """Write the inventory to parquet (pyarrow); return the path."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p, index=False)
    return p


def clear_counts(
    out_dir: str | Path, sensor: str, df: pd.DataFrame, geobox: GeoBox
) -> tuple[list[tuple[int, int]], dict[str, NDArray[np.uint16]]]:
    """Return sorted (year, month) keys and per-pixel clear-observation counts per variant."""
    want = cache_meta([], geobox, "")
    ids_by_key = {k: sorted(v) for k, v in zip(df["acq_id"], df["item_ids"], strict=True)}
    cache_dir = Path(out_dir) / "cache"
    ok = df[df["status"] == "ok"]
    yms = sorted({(int(y), int(m)) for y, m in zip(ok["year"], ok["month"], strict=True)})
    idx = {ym: i for i, ym in enumerate(yms)}
    counts: dict[str, NDArray[np.uint16]] = {}
    for key, y, m in zip(ok["acq_id"], ok["year"], ok["month"], strict=True):
        _, clears, meta = _read_cache(_cache_path(cache_dir, sensor, key))
        if meta.get("item_ids") != ids_by_key[key]:
            raise ValueError(
                f"{key}: cached item_ids {meta.get('item_ids')} differ from the inventory row "
                f"{ids_by_key[key]}; rerun run_audit (stale cache) before counting."
            )
        for k in ("shape", "transform", "crs"):
            if meta.get(k) != want[k]:
                raise ValueError(
                    f"{key}: cached {k} {meta.get(k)} differs from target {want[k]}; "
                    "rerun run_audit (stale cache) before counting."
                )
        for name, mask in clears.items():
            if name not in counts:
                counts[name] = np.zeros((len(yms), *mask.shape), dtype=np.uint16)
            counts[name][idx[(int(y), int(m))]] += mask.astype(np.uint16)
    return yms, counts


def month_year_table(df: pd.DataFrame, value: str, agg: str = "sum") -> pd.DataFrame:
    """Return a year x month (1-12) table of the sum or count of a column over ok acquisitions."""
    if agg not in ("sum", "count"):
        raise ValueError("agg must be 'sum' or 'count'")
    ok = df[df["status"] == "ok"]
    grouped = ok.groupby(["year", "month"])[value]
    t = (grouped.sum() if agg == "sum" else grouped.count()).unstack("month")
    return t.reindex(columns=range(1, 13)).fillna(0.0)


def baseline_era(df: pd.DataFrame) -> pd.Series:
    """Label S2 rows 'pre-04.00', 'post-04.00' or 'mixed' from baseline_min/max (04.00 = (4, 0))."""
    lo = df["baseline_min"].map(lambda b: parse_baseline(b) >= (4, 0))
    hi = df["baseline_max"].map(lambda b: parse_baseline(b) >= (4, 0))
    return pd.Series(
        np.where(lo & hi, "post-04.00", np.where(~lo & ~hi, "pre-04.00", "mixed")),
        index=df.index,
        name="baseline_era",
    )


COUNT_NODATA = 65535  # uint16 nodata for pixels outside the AOI polygon in the count rasters


def clear_rate_by_window(
    df: pd.DataFrame, variant: str, windows: Mapping[str, Sequence[int]]
) -> pd.DataFrame:
    """Return per-window mean/median clear fraction (0-1) of acquisitions with data, all years."""
    d = df[(df["status"] == "ok") & (df["valid_frac"] > 0)]
    rows = []
    for name, months in windows.items():
        v = d.loc[d["month"].isin(months), variant]
        rows.append(
            {
                "window": name,
                "months": len(months),
                "n_acq": len(v),
                "mean_clear": float(v.mean()),
                "median_clear": float(v.median()),
            }
        )
    return pd.DataFrame(rows)


def clear_rate_by_month(df: pd.DataFrame, variant: str) -> pd.DataFrame:
    """Return per calendar month the mean/median clear fraction (0-1) of acquisitions with data."""
    d = df[(df["status"] == "ok") & (df["valid_frac"] > 0)]
    g = d.groupby("month")[variant]
    return pd.DataFrame(
        {"n_acq": g.size(), "mean_clear": g.mean(), "median_clear": g.median()}
    ).reindex(range(1, 13))


def write_clear_counts(
    path: str | Path,
    yms: Sequence[tuple[int, int]],
    counts: NDArray[np.uint16],
    geobox: GeoBox,
    inside: NDArray[np.bool_] | None = None,
) -> Path:
    """Write clear counts (uint16, band per year-month); pixels outside the AOI are 65535 nodata."""
    import rasterio

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if counts.shape[0] != len(yms):
        raise ValueError("counts must have one band per (year, month).")
    with rasterio.open(
        p,
        "w",
        driver="GTiff",
        height=counts.shape[1],
        width=counts.shape[2],
        count=counts.shape[0],
        dtype="uint16",
        crs=str(geobox.crs),
        transform=geobox.transform,
        compress="deflate",
        nodata=COUNT_NODATA if inside is not None else None,
    ) as dst:
        for i, (y, m) in enumerate(yms, 1):
            band = counts[i - 1]
            if inside is not None:
                band = np.where(inside, band, np.uint16(COUNT_NODATA))
            dst.write(band, i)
            dst.set_band_description(i, f"{y}-{m:02d}")
    return p


def season_year(year: int, month: int, months: Sequence[int]) -> int | None:
    """Return the season year of a month for a window of chronological months, else None."""
    # A window that wraps the new year (first month > last month, e.g. 12, 1, 2) assigns its
    # months from the first month onward to the following year.
    if month not in months:
        return None
    wraps = months[0] > months[-1]
    return year + 1 if wraps and month >= months[0] else year


def _window_span(months: Sequence[int], sy: int) -> tuple[date, date]:
    wraps = months[0] > months[-1]
    start = date(sy - 1 if wraps else sy, months[0], 1)
    last = months[-1]
    end = date(sy + (last == 12), last % 12 + 1, 1) - timedelta(days=1)
    return start, end


def window_summary(
    df: pd.DataFrame,
    yms: Sequence[tuple[int, int]],
    counts: NDArray[np.uint16],
    inside: NDArray[np.bool_],
    windows: Mapping[str, Sequence[int]],
    years: Sequence[int],
    today: date | None = None,
    search_range: tuple[date, date] | None = None,
) -> pd.DataFrame:
    """Return one row per window and season-year: scene counts, zero-obs share, count tail, partial.

    n_ok counts ok rows; n_with_data counts those with valid_frac > 0 (no-data swaths excluded).
    share_le1/le2_obs and p10_count describe the lower tail of the per-pixel count (AOI pixels).

    partial_window is True when the window is not fully inside the searched date range
    (search_range, end capped at today); without it, the first and last acquisition dates are used.
    """
    today = today or date.today()
    first = last = None
    if search_range is not None:
        first, last = search_range[0], min(search_range[1], today)
    elif len(df):
        d = pd.to_datetime(df["date_local"]).dt.date
        first, last = d.min(), min(d.max(), today)
    out: list[dict[str, object]] = []
    for name, months in windows.items():
        for sy in years:
            idxs = [i for i, (y, m) in enumerate(yms) if season_year(y, m, months) == sy]
            if idxs:
                tot = counts[idxs].sum(axis=0, dtype=np.int64)[inside]
            else:
                tot = np.zeros(int(inside.sum()), dtype=np.int64)
            sel = [
                (st, season_year(int(y), int(m), months) == sy)
                for st, y, m in zip(df["status"], df["year"], df["month"], strict=True)
            ]
            n_ok = sum(1 for st, hit in sel if hit and st == "ok")
            n_data = sum(
                1
                for (st, hit), vf in zip(sel, df["valid_frac"], strict=True)
                if hit and st == "ok" and vf > 0
            )
            n_err = sum(1 for st, hit in sel if hit and st == "error")
            w_start, w_end = _window_span(months, sy)
            partial = bool(first is None or w_start < first or w_end > last)
            out.append(
                {
                    "window": name,
                    "season_year": sy,
                    "n_ok": n_ok,
                    "n_with_data": n_data,
                    "n_error": n_err,
                    "share_zero_obs": float((tot == 0).mean()),
                    "share_ge1_obs": float((tot >= 1).mean()),
                    "share_le1_obs": float((tot <= 1).mean()),
                    "share_le2_obs": float((tot <= 2).mean()),
                    "p10_count": float(np.percentile(tot, 10)),
                    "median_count": float(np.median(tot)),
                    "partial_window": partial,
                }
            )
    return pd.DataFrame(out)
