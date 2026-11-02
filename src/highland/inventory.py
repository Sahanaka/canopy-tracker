"""Per-acquisition availability audit: clear-pixel shares of the AOI from SCL and QA_PIXEL."""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
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
    "baseline_max",
    "collection_category",
    "correction",
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
    """Read SCL (uint8, nodata 0) or QA_PIXEL (uint16, nodata 1) of one acquisition."""
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
    else:
        bls = []
        tiles = [
            f"{i.properties.get('landsat:wrs_path')}/{i.properties.get('landsat:wrs_row')}"
            for i in items
        ]
        az = _mean(items, lambda i: i.properties.get("view:sun_azimuth"))
        elev = _mean(items, lambda i: i.properties.get("view:sun_elevation"))
        bl_max = None
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
        "baseline_max": bl_max,
        "collection_category": p.get("landsat:collection_category"),
        "correction": p.get("landsat:correction"),
        "relative_orbit": p.get("sat:relative_orbit"),
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
    """Return one inventory row (shares 0-1 of AOI pixels) and per-pixel clear masks by variant."""
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


def _write_cache(path: Path, row: Mapping[str, object], clears: Mapping[str, NDArray]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    shape = next(iter(clears.values())).shape
    payload = {k: np.packbits(v.ravel()) for k, v in clears.items()}
    row_json = json.dumps(
        row, default=lambda o: o.isoformat() if hasattr(o, "isoformat") else str(o)
    )
    tmp = path.with_suffix(".tmp.npz")
    np.savez_compressed(tmp, _row=np.array(row_json), _shape=np.array(shape), **payload)
    os.replace(tmp, path)  # atomic: a half-written file is never seen as done


def _read_cache(path: Path) -> tuple[dict[str, object], dict[str, NDArray[np.bool_]]]:
    with np.load(path) as z:
        row = json.loads(str(z["_row"]))
        shape = tuple(int(s) for s in z["_shape"])
        n = int(np.prod(shape))
        clears = {
            k: np.unpackbits(z[k], count=n).astype(bool).reshape(shape)
            for k in z.files
            if not k.startswith("_")
        }
    row["datetime_utc"] = pd.Timestamp(row["datetime_utc"])
    return row, clears


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
    geobox = aoi_geobox(aoi, items, sensor)
    inside = aoi_inside_mask(aoi, geobox)
    groups = acquisition_groups(items, sensor)
    rows: list[dict[str, object]] = []
    for n, (key, members) in enumerate(groups.items(), 1):
        path = _cache_path(cache_dir, sensor, key)
        if resume and path.exists():
            rows.append(_read_cache(path)[0])
            continue
        err: Exception | None = None
        for attempt in range(retries):
            try:
                row, clears = audit_acquisition(members, sensor, key, geobox, inside, reader)
                _write_cache(path, row, clears)
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
    out_dir: str | Path, sensor: str, df: pd.DataFrame
) -> tuple[list[tuple[int, int]], dict[str, NDArray[np.uint16]]]:
    """Return sorted (year, month) keys and per-pixel counts of clear observations per variant."""
    cache_dir = Path(out_dir) / "cache"
    ok = df[df["status"] == "ok"]
    yms = sorted({(int(y), int(m)) for y, m in zip(ok["year"], ok["month"], strict=True)})
    idx = {ym: i for i, ym in enumerate(yms)}
    counts: dict[str, NDArray[np.uint16]] = {}
    for key, y, m in zip(ok["acq_id"], ok["year"], ok["month"], strict=True):
        _, clears = _read_cache(_cache_path(cache_dir, sensor, key))
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


def season_year(year: int, month: int, months: Sequence[int]) -> int | None:
    """Return the season year of a month for a window (chronological months), or None if outside.

    A window that wraps the new year (first month > last month, e.g. 12, 1, 2) assigns the
    months from the first month onward to the following year.
    """
    if month not in months:
        return None
    wraps = months[0] > months[-1]
    return year + 1 if wraps and month >= months[0] else year


def window_summary(
    df: pd.DataFrame,
    yms: Sequence[tuple[int, int]],
    counts: NDArray[np.uint16],
    inside: NDArray[np.bool_],
    windows: Mapping[str, Sequence[int]],
) -> pd.DataFrame:
    """Return per window and season-year: acquisitions, AOI share with 0 clear obs, median count."""
    ok = df[df["status"] == "ok"]
    out: list[dict[str, object]] = []
    for name, months in windows.items():
        by_year: dict[int, list[int]] = {}
        for i, (y, m) in enumerate(yms):
            sy = season_year(y, m, months)
            if sy is not None:
                by_year.setdefault(sy, []).append(i)
        for sy, idxs in sorted(by_year.items()):
            tot = counts[idxs].sum(axis=0, dtype=np.int64)[inside]
            n_acq = sum(
                1
                for y, m in zip(ok["year"], ok["month"], strict=True)
                if season_year(int(y), int(m), months) == sy
            )
            out.append(
                {
                    "window": name,
                    "season_year": sy,
                    "n_acquisitions": n_acq,
                    "share_zero_obs": float((tot == 0).mean()),
                    "share_ge1_obs": float((tot >= 1).mean()),
                    "median_count": float(np.median(tot)),
                }
            )
    return pd.DataFrame(out)
