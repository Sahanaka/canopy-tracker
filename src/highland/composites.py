"""Seasonal and annual median composites (Sentinel-2 L2A, 10 m, EPSG:32644)."""

from __future__ import annotations

import hashlib
import json
import logging
import warnings
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path

import dask
import geopandas as gpd
import numpy as np
import pandas as pd
import pystac
import xarray as xr
from numpy.typing import NDArray
from odc.geo.geobox import GeoBox

from highland import stac
from highland.indices import INDEX_NAMES, all_indices, out_of_range_share
from highland.inventory import _meta_row, _window_span, aoi_inside_mask, season_year
from highland.io import write_cog
from highland.masking import (
    M12_KEEP_CAST_SHADOW,
    M12_KEEP_UNCLASSIFIED,
    s2_dn_to_reflectance,
    scl_class_shares,
    scl_clear,
)

log = logging.getLogger(__name__)

# Met Department seasons (CLAUDE.md, ADR 0011); December counts toward the following season-year.
SEASONS: dict[str, tuple[int, ...]] = {
    "FIM": (3, 4),
    "SWM": (5, 6, 7, 8, 9),
    "SIM": (10, 11),
    "NEM": (12, 1, 2),
}
REFLECTANCE_BANDS: tuple[str, ...] = tuple(stac.S2_BANDS)  # blue, green, red, nir, nir08, ...
OBS_BANDS: tuple[str, ...] = ("n_obs", "thin", "window_day_median")
OBS_NODATA = 65535
THIN_MAX_OBS = 2  # ADR 0013 (D3): a median rejects a contaminated value only when n_obs >= 3
SCHEMA_VERSION = 1


def season_years(first: int = 2016, last: int = 2026) -> list[tuple[str, int]]:
    """Return (season, season-year) pairs 2016-2026 without the partial SIM 2026 (ADR 0011)."""
    return [(s, y) for y in range(first, last + 1) for s in SEASONS if (s, y) != ("SIM", 2026)]


def window_groups(
    items: Sequence[pystac.Item], season: str, sy: int
) -> dict[str, list[pystac.Item]]:
    """Return acquisitions (key '<platform>_<local date>') of one season-year, in date order."""
    months = SEASONS[season]
    out: dict[str, list[pystac.Item]] = {}
    for key, group in stac.acquisition_groups(items, "s2").items():
        d = stac.local_date(min(i.datetime for i in group if i.datetime is not None))
        if season_year(int(d[:4]), int(d[5:7]), months) == sy:
            out[key] = group
    return out


def items_hash(items: Sequence[pystac.Item]) -> str:
    """Return the sha256 hex digest of the sorted item ids."""
    return hashlib.sha256("\n".join(sorted(i.id for i in items)).encode()).hexdigest()


def scene_stack(
    groups: Mapping[str, Sequence[pystac.Item]],
    geobox: GeoBox,
    keep: tuple[bool, bool] = (M12_KEEP_CAST_SHADOW, M12_KEEP_UNCLASSIFIED),
    season: str | None = None,
    sy: int | None = None,
) -> xr.Dataset:
    """Return lazy (time, y, x) float32 reflectance 0-1, indices, bool 'valid' and uint8 'scl'.

    Reflectance and indices are NaN where the SCL keep-set (keep = keep class 2, keep class 7)
    or any DN-0 band removes the pixel. Coordinates 'window_day' (1 = first day of the window)
    and 'acq_id' are attached on time when season and sy are given.
    """
    if not groups:
        raise ValueError("No acquisitions in this window.")
    scenes = []
    for key, items in groups.items():
        ds = stac.load_s2_acquisition(list(items), geobox)
        bl = str(ds.attrs["baseline"])
        refl = xr.Dataset(
            {
                b: xr.apply_ufunc(
                    s2_dn_to_reflectance,
                    ds[b],
                    kwargs={"baseline": bl},
                    dask="parallelized",
                    output_dtypes=[np.float32],
                )
                for b in REFLECTANCE_BANDS
            }
        )
        clear = xr.apply_ufunc(
            scl_clear,
            ds["scl"],
            kwargs={"keep_cast_shadow": keep[0], "keep_unclassified": keep[1]},
            dask="parallelized",
            output_dtypes=[bool],
        )
        finite = xr.concat([refl[b].notnull() for b in REFLECTANCE_BANDS], dim="b").all("b")
        valid = clear & finite
        refl = refl.where(valid)
        idx = all_indices(refl)
        scene = xr.merge([refl, idx, valid.rename("valid"), ds["scl"]])
        coords: dict[str, tuple[str, list[object]]] = {"acq_id": ("time", [key])}
        if season is not None and sy is not None:
            d = stac.local_date(min(i.datetime for i in items if i.datetime is not None))
            day = (date.fromisoformat(d) - _window_span(SEASONS[season], sy)[0]).days + 1
            coords["window_day"] = ("time", [day])
        scenes.append(scene.assign_coords(coords))
    stack = xr.concat(scenes, dim="time")
    return stack.chunk({"time": -1})


def median_composite(stack: xr.Dataset) -> xr.Dataset:
    """Return per-pixel median reflectance, median of per-scene indices, n_obs, thin, window_day.

    n_obs (uint16) counts valid observations; thin (uint8) is 0 for none, 1 for 1-2, 2 for >= 3.
    window_day_median (uint16, 0 = none) is the median day of the window of the valid observations.
    """
    valid = stack["valid"]
    n_obs = valid.sum("time").astype(np.uint16)
    out: dict[str, xr.DataArray] = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN slices -> NaN, by design
        for name in (*REFLECTANCE_BANDS, *INDEX_NAMES):
            out[name] = stack[name].median("time", skipna=True).astype(np.float32)
        if "window_day" in stack.coords:
            day = xr.where(valid, stack["window_day"].astype(np.float32), np.nan)
            med = day.median("time", skipna=True)
            out["window_day_median"] = med.fillna(0).round().astype(np.uint16)
    out["n_obs"] = n_obs
    out["thin"] = xr.where(n_obs == 0, 0, xr.where(n_obs <= THIN_MAX_OBS, 1, 2)).astype(np.uint8)
    return xr.Dataset(out)


def _paths(out_dir: Path, season: str, sy: int) -> dict[str, Path]:
    stem = f"s2_{sy}_{season}"
    return {
        "indices": out_dir / f"{stem}_indices.tif",
        "reflectance": out_dir / f"{stem}_reflectance.tif",
        "obs": out_dir / f"{stem}_obs.tif",
        "masks": out_dir / "clear_masks" / f"{stem}.npz",
        "meta": out_dir / "meta" / f"{stem}.json",
    }


def _params(
    items: Sequence[pystac.Item], geobox: GeoBox, keep: tuple[bool, bool], aoi_sha: str
) -> dict[str, object]:
    t = geobox.transform
    return {
        "schema": SCHEMA_VERSION,
        "items_sha256": items_hash(items),
        "keep_cast_shadow": keep[0],
        "keep_unclassified": keep[1],
        "thin_max_obs": THIN_MAX_OBS,
        "shape": [int(v) for v in geobox.shape],
        "transform": [float(t.a), float(t.b), float(t.c), float(t.d), float(t.e), float(t.f)],
        "aoi_sha256": aoi_sha,
    }


def is_done(out_dir: Path, season: str, sy: int, params: Mapping[str, object]) -> bool:
    """Return True if all outputs of a window-year exist and carry the same parameters."""
    paths = _paths(out_dir, season, sy)
    if not all(p.exists() for p in paths.values()):
        return False
    try:
        meta = json.loads(paths["meta"].read_text())
    except (OSError, ValueError):
        return False
    return meta.get("params") == json.loads(json.dumps(params))


def _plain(x: object) -> object:
    if isinstance(x, (np.floating, np.integer)):
        return x.item()
    return x


def _summarise(
    season: str,
    sy: int,
    n_obs: NDArray[np.integer],
    idx: Mapping[str, NDArray[np.floating]],
    inside: NDArray[np.bool_],
    scenes: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    n = n_obs[inside].astype(np.int64)
    row: dict[str, object] = {
        "season": season,
        "season_year": sy,
        "n_scenes": len(scenes),
        "n_scenes_with_data": sum(1 for s in scenes if float(s["scl_share_0"]) < 1.0),  # type: ignore[arg-type]
        "baselines": ",".join(sorted({str(s["baseline"]) for s in scenes})),
        "n_obs_median": float(np.median(n)),
        "n_obs_p10": float(np.percentile(n, 10)),
        "n_obs_max": int(n.max()),
        "share_zero_obs": float((n == 0).mean()),
        "share_thin_1_2": float(((n >= 1) & (n <= THIN_MAX_OBS)).mean()),
        "share_le2_obs": float((n <= THIN_MAX_OBS).mean()),
        "share_ge3_obs": float((n >= 3).mean()),
        "scl2_share_mean": float(np.mean([s["scl_share_2"] for s in scenes])),  # type: ignore[arg-type]
        "scl7_share_mean": float(np.mean([s["scl_share_7"] for s in scenes])),  # type: ignore[arg-type]
        "thin_season_year": bool(np.median(n) <= THIN_MAX_OBS),
    }
    for name, a in idx.items():
        v = a[inside]
        f = v[np.isfinite(v)]
        row[f"{name}_finite_share"] = float(f.size / v.size)
        row[f"{name}_out_of_range_share"] = out_of_range_share(v)
        if f.size:
            q = np.percentile(f, [1, 50, 99])
            row.update({f"{name}_p01": float(q[0]), f"{name}_p50": float(q[1])})
            row.update({f"{name}_p99": float(q[2]), f"{name}_min": float(f.min())})
            row[f"{name}_max"] = float(f.max())
    return row


def composite_window(
    items: Sequence[pystac.Item],
    aoi: gpd.GeoDataFrame,
    season: str,
    sy: int,
    out_dir: str | Path,
    keep: tuple[bool, bool] = (M12_KEEP_CAST_SHADOW, M12_KEEP_UNCLASSIFIED),
    geobox: GeoBox | None = None,
    num_workers: int = 4,
    force: bool = False,
) -> Path:
    """Write the COGs, clear masks and metadata of one season-year; resumable; return obs path."""
    out = Path(out_dir)
    groups = window_groups(items, season, sy)
    sel = [i for g in groups.values() for i in g]
    geobox = geobox or stac.aoi_geobox(aoi, items, "s2", asset="B02", resolution=10.0)
    from highland.inventory import aoi_hash

    params = _params(sel, geobox, keep, aoi_hash(aoi))
    paths = _paths(out, season, sy)
    if not force and is_done(out, season, sy, params):
        log.info("%s %d: up to date, skipped", season, sy)
        return paths["obs"]

    stack = scene_stack(groups, geobox, keep, season, sy)
    comp = median_composite(stack)
    bundle = xr.merge([comp, stack["valid"], stack["scl"]])
    with dask.config.set(scheduler="threads", num_workers=num_workers):
        res = bundle.compute()
    inside = aoi_inside_mask(aoi, geobox)

    valid = res["valid"].values  # (time, y, x) bool
    scl = res["scl"].values
    acq_ids = [str(a) for a in res["acq_id"].values]
    scenes: list[dict[str, object]] = []
    for t, (key, items_t) in enumerate(groups.items()):
        row = _meta_row("s2", key, items_t)
        shares = scl_class_shares(scl[t], inside)
        scenes.append(
            {
                "acq_id": key,
                "season": season,
                "season_year": sy,
                "item_ids": ";".join(i.id for i in items_t),
                "date_local": row["date_local"],
                "baseline": row["baseline_max"],
                "sun_azimuth_deg": row["sun_azimuth_deg"],
                "sun_elevation_deg": row["sun_elevation_deg"],
                "window_day": int(res["window_day"].values[t]),
                "clear_fraction": float(valid[t][inside].mean()),
                **{f"scl_share_{c}": shares[c] for c in (0, 2, 7)},
            }
        )
    assert acq_ids == list(groups)

    tags = {
        "season": season,
        "season_year": str(sy),
        "n_scenes": str(len(groups)),
        "items_sha256": str(params["items_sha256"]),
        "scl_keep_cast_shadow": str(keep[0]),
        "scl_keep_unclassified": str(keep[1]),
        "terrain_flag": "not applied (M1.3)",
        "reflectance": "float32 0-1; per-band median; offset by baseline (DN-1000 if >= 04.00)",
        "indices": "median of per-scene indices; zero denominator -> NaN; no clipping",
    }
    t_, crs = geobox.transform, str(geobox.crs)

    def masked(name: str) -> NDArray[np.float32]:
        a = res[name].values.astype(np.float32)
        a[~inside] = np.nan
        return a

    idx = {n: masked(n) for n in INDEX_NAMES}
    refl = {n: masked(n) for n in REFLECTANCE_BANDS}
    write_cog(
        np.stack([idx[n] for n in INDEX_NAMES]),
        paths["indices"],
        t_,
        crs,
        np.nan,
        INDEX_NAMES,
        tags,
    )
    write_cog(
        np.stack([refl[n] for n in REFLECTANCE_BANDS]),
        paths["reflectance"],
        t_,
        crs,
        np.nan,
        REFLECTANCE_BANDS,
        tags,
    )
    obs = np.stack([res[n].values.astype(np.uint16) for n in OBS_BANDS])
    obs[:, ~inside] = OBS_NODATA
    write_cog(obs, paths["obs"], t_, crs, OBS_NODATA, OBS_BANDS, tags)
    paths["masks"].parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        paths["masks"],
        packed=np.packbits(valid, axis=-1),
        shape=np.array(valid.shape),
        acq_ids=np.array(acq_ids),
        sun_azimuth_deg=np.array([s["sun_azimuth_deg"] for s in scenes], dtype=np.float32),
        sun_elevation_deg=np.array([s["sun_elevation_deg"] for s in scenes], dtype=np.float32),
    )
    summary = _summarise(season, sy, res["n_obs"].values, idx, inside, scenes)
    paths["meta"].parent.mkdir(parents=True, exist_ok=True)
    paths["meta"].write_text(
        json.dumps(
            {"params": params, "summary": summary, "scenes": scenes}, default=_plain, indent=1
        )
    )
    return paths["obs"]


def unpack_clear_masks(path: str | Path) -> tuple[NDArray[np.bool_], list[str]]:
    """Return the (time, y, x) bool clear masks and acquisition ids saved by composite_window."""
    z = np.load(path)
    shape = tuple(int(v) for v in z["shape"])
    bits = np.unpackbits(z["packed"], axis=-1, count=shape[-1]).astype(bool)
    return bits, [str(a) for a in z["acq_ids"]]


def collect_summaries(out_dir: str | Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read the per-window meta files; return (composite_summary, scenes_used) tables."""
    metas = sorted((Path(out_dir) / "meta").glob("s2_*.json"))
    summ, scenes = [], []
    for m in metas:
        d = json.loads(m.read_text())
        summ.append(d["summary"])
        scenes.extend(d["scenes"])
    order = {s: i for i, s in enumerate(SEASONS)}
    s_df = pd.DataFrame(summ)
    if len(s_df):
        s_df = s_df.sort_values(
            ["season_year", "season"], key=lambda c: c.map(order) if c.name == "season" else c
        )
    return s_df.reset_index(drop=True), pd.DataFrame(scenes)
