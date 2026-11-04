"""DEM derivatives (Horn slope, aspect, hillshade, cos i) and the per-scene terrain-shadow flag.

Conventions: rows run north to south, columns west to east, UTM zone 44N (EPSG:32644), metres.
Aspect is the downslope compass bearing (0-360 deg clockwise from north); sun azimuth is clockwise
from north and sun elevation is measured from the horizon (STAC view extension; ADR 0015/0016).
UTM grid north is taken as true north (meridian convergence <= 0.012 deg across the AOI).
"""

from __future__ import annotations

import hashlib
import json
import math
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import xarray as xr
from affine import Affine
from numpy.typing import NDArray
from odc.geo.geobox import GeoBox

TERRAIN_RES_M = 30.0  # Copernicus GLO-30 posting; derivatives are computed at this spacing
DEM_COLLECTION = "cop-dem-glo-30"
HIGHEST_POINT_M = (
    2527.0  # Pidurutalagala, Survey Department 8292 ft (ADR 0015); upper bound for the buffer
)
HILLSHADE_AZIMUTH_DEG = 315.0  # GDAL gdaldem default (verified on GDAL 3.13.3)
HILLSHADE_ALTITUDE_DEG = 45.0  # GDAL gdaldem default (verified on GDAL 3.13.3)
SHADOW_SHARE_CUT = (
    0.5  # ADR 0016 (D6): flag when at least half of the valid observations are shadowed
)

F32 = NDArray[np.float32]
Bool = NDArray[np.bool_]


# --------------------------------------------------------------------------------------------
# Grids
# --------------------------------------------------------------------------------------------


def nested_geobox(fine: GeoBox, factor: int = 3, buffer_m: float = 0.0) -> GeoBox:
    """Return the coarse GeoBox (cell = factor x fine, m) with the same top-left corner as `fine`.

    The grid covers `fine` completely (shape rounded up) and is padded on all four sides by
    `buffer_m` rounded up to whole coarse cells (buffer_m >= 0 m).
    """
    if buffer_m < 0 or factor < 1:
        raise ValueError("buffer_m must be >= 0 and factor >= 1.")
    t = fine.transform
    if abs(t.a) != abs(t.e) or t.b != 0 or t.d != 0:
        raise ValueError("fine grid must be north-up with square pixels.")
    res = abs(t.a) * factor
    nb = math.ceil(buffer_m / res - 1e-9)
    ny, nx = fine.shape
    cy, cx = math.ceil(ny / factor), math.ceil(nx / factor)
    tr = Affine(res, 0.0, t.c - nb * res, 0.0, -res, t.f + nb * res)
    return GeoBox((cy + 2 * nb, cx + 2 * nb), tr, fine.crs)


def z_min_aoi(dem30: NDArray[np.floating], inside_fine: Bool, factor: int = 3) -> float:
    """Return the minimum height (m) over every 30 m cell that contains any AOI pixel at 10 m.

    `dem30` is the (ny30, nx30) AOI-grid DEM nested in `inside_fine` (the fine bool AOI mask).
    Edge cells that are only partly covered by the fine grid or the polygon count, because
    they feed AOI pixels (their centres may lie outside the polygon).
    """
    ny, nx = inside_fine.shape
    ny30, nx30 = dem30.shape
    if ny30 != -(-ny // factor) or nx30 != -(-nx // factor):
        raise ValueError("dem30 does not nest in the fine grid.")
    pad = np.zeros((ny30 * factor, nx30 * factor), bool)
    pad[:ny, :nx] = inside_fine
    any_in = pad.reshape(ny30, factor, nx30, factor).any(axis=(1, 3))
    return float(np.min(dem30[any_in]))


def buffer_cells(buffer_m: float, res: float = TERRAIN_RES_M) -> int:
    """Return the buffer width in whole cells (ceil of buffer_m / res)."""
    return math.ceil(buffer_m / res - 1e-9)


def load_cop_dem(aoi: gpd.GeoDataFrame, geobox: GeoBox) -> xr.DataArray:
    """Load Copernicus GLO-30 (m above EGM2008, float32) bilinearly resampled onto `geobox`.

    Searches Planetary Computer `cop-dem-glo-30` for the geobox's bounds (the AOI must lie inside),
    signs the items and mosaics them with odc-stac. Raises on any NaN.
    """
    import odc.stac
    import planetary_computer
    import pystac_client

    from highland.stac import PC_STAC_URL

    w, s, e, n = geobox.geographic_extent.boundingbox.bbox
    aw, as_, ae, an = aoi.to_crs("EPSG:4326").total_bounds
    if aw < w or as_ < s or ae > e or an > n:
        raise ValueError("The AOI is not inside the requested geobox.")
    client = pystac_client.Client.open(PC_STAC_URL)
    items = list(client.search(collections=[DEM_COLLECTION], bbox=[w, s, e, n]).items())
    if not items:
        raise RuntimeError("No cop-dem-glo-30 items for the requested box.")
    signed = [planetary_computer.sign(i) for i in items]
    cfg = {"*": {"assets": {"data": {"data_type": "float32", "nodata": float("nan")}}}}
    ds = odc.stac.load(
        signed,
        bands=["data"],
        geobox=geobox,
        groupby="id" if len({i.datetime for i in items}) > 1 else "solar_day",
        resampling="bilinear",
        stac_cfg=cfg,
        fail_on_error=True,
    )
    da = ds["data"]
    if "time" in da.dims:
        da = da.max("time", skipna=True) if da.sizes["time"] > 1 else da.isel(time=0, drop=True)
    da = da.astype(np.float32).compute()
    n_nan = int(np.isnan(da.values).sum())
    if n_nan:
        raise RuntimeError(f"{n_nan} NaN cells in the DEM over the buffered box.")
    da.attrs.update(
        {
            "units": "m above EGM2008",
            "items": ",".join(sorted(i.id for i in items)),
            "source": "Copernicus GLO-30 DSM (cop-dem-glo-30), bilinear",
        }
    )
    return da.rename("dem")


# --------------------------------------------------------------------------------------------
# Derivatives
# --------------------------------------------------------------------------------------------


def horn_gradient(dem: NDArray[np.floating], res: float) -> tuple[F32, F32]:
    """Return (dz/dx east, dz/dy north) in m/m from Horn's 3x3 weights; outer ring is NaN.

    `dem` is (rows, cols) in m with rows north to south; `res` is the cell size in m.
    """
    z = np.asarray(dem, dtype=np.float64)
    if z.ndim != 2 or min(z.shape) < 3:
        raise ValueError("dem must be 2-D with at least 3 rows and columns.")
    a, b, c = z[:-2, :-2], z[:-2, 1:-1], z[:-2, 2:]
    d, f = z[1:-1, :-2], z[1:-1, 2:]
    g, h, i = z[2:, :-2], z[2:, 1:-1], z[2:, 2:]
    dzdx = np.full(z.shape, np.nan, dtype=np.float32)
    dzdy = np.full(z.shape, np.nan, dtype=np.float32)
    dzdx[1:-1, 1:-1] = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * res)
    dzdy[1:-1, 1:-1] = ((a + 2 * b + c) - (g + 2 * h + i)) / (8 * res)
    return dzdx, dzdy


def slope_deg(dem: NDArray[np.floating], res: float) -> F32:
    """Return slope in degrees (0-90) from Horn's method; NaN on the outer ring."""
    dzdx, dzdy = horn_gradient(dem, res)
    return np.degrees(np.arctan(np.hypot(dzdx.astype(np.float64), dzdy))).astype(np.float32)


def aspect_deg(dem: NDArray[np.floating], res: float) -> F32:
    """Return the downslope compass bearing in degrees (0-360, clockwise from north).

    NaN on the outer ring and where the gradient is exactly zero (flat).
    """
    dzdx, dzdy = horn_gradient(dem, res)
    asp = np.degrees(np.arctan2(-dzdx.astype(np.float64), -dzdy.astype(np.float64))) % 360.0
    asp[(dzdx == 0) & (dzdy == 0)] = np.nan
    return asp.astype(np.float32)


def sun_vector(az_deg: float, elev_deg: float) -> tuple[float, float, float]:
    """Return the unit vector to the sun as (East, North, Up); az 0-360 cw from N, elev 0-90 deg."""
    az, el = math.radians(az_deg), math.radians(elev_deg)
    return math.cos(el) * math.sin(az), math.cos(el) * math.cos(az), math.sin(el)


def cos_incidence(dem: NDArray[np.floating], res: float, az_deg: float, elev_deg: float) -> F32:
    """Return cos of the angle between surface normal and sun, in [-1, 1]; NaN on the outer ring.

    Negative values mean the facet faces away from the sun (self-shadow). Equal to
    cos z cos s + sin z sin s cos(az - aspect) (Riano et al. 2003).
    """
    dzdx, dzdy = horn_gradient(dem, res)
    e, n, u = sun_vector(az_deg, elev_deg)
    gx, gy = dzdx.astype(np.float64), dzdy.astype(np.float64)
    cos_i = (-gx * e - gy * n + u) / np.sqrt(gx * gx + gy * gy + 1.0)
    return np.clip(cos_i, -1.0, 1.0).astype(np.float32)


def hillshade(
    dem: NDArray[np.floating],
    res: float,
    az_deg: float = HILLSHADE_AZIMUTH_DEG,
    elev_deg: float = HILLSHADE_ALTITUDE_DEG,
) -> NDArray[np.uint8]:
    """Return display hillshade 0-255 (uint8) = 255 * max(cos i, 0); the NaN ring becomes 0."""
    ci = cos_incidence(dem, res, az_deg, elev_deg)
    return np.rint(255.0 * np.clip(np.nan_to_num(ci, nan=0.0), 0.0, 1.0)).astype(np.uint8)


def terrain_stack(dem: NDArray[np.floating], res: float = TERRAIN_RES_M) -> F32:
    """Return (4, y, x) float32: dem (m), slope, aspect (deg), hillshade (0-255); NaN ring."""
    hs = hillshade(dem, res).astype(np.float32)
    slope = slope_deg(dem, res)
    hs[np.isnan(slope)] = np.nan
    return np.stack([np.asarray(dem, np.float32), slope, aspect_deg(dem, res), hs])


# --------------------------------------------------------------------------------------------
# Cast shadow (horizon-angle ray march) and per-scene shadow
# --------------------------------------------------------------------------------------------


def horizon_buffer_m(
    z_min_aoi: float, elev_min_deg: float, z_max_bound: float = HIGHEST_POINT_M
) -> float:
    """Return the DEM buffer width in m: (z_max_bound - z_min_aoi) / tan(elev_min).

    Heights in m, elev_min_deg in (0, 90).

    Terrain farther than this from the AOI cannot shade it at any sun elevation >= elev_min.
    """
    if not 0 < elev_min_deg < 90:
        raise ValueError("elev_min_deg must be in (0, 90).")
    return max(z_max_bound - z_min_aoi, 0.0) / math.tan(math.radians(elev_min_deg))


def _bilinear(z: NDArray[np.float64], r: NDArray[np.float64], c: NDArray[np.float64]) -> NDArray:
    """Sample z at fractional (row, col) positions that lie inside the array."""
    r0 = np.minimum(np.floor(r).astype(np.intp), z.shape[0] - 2)
    c0 = np.minimum(np.floor(c).astype(np.intp), z.shape[1] - 2)
    fr, fc = r - r0, c - c0
    return (
        z[r0, c0] * (1 - fr) * (1 - fc)
        + z[r0, c0 + 1] * (1 - fr) * fc
        + z[r0 + 1, c0] * fr * (1 - fc)
        + z[r0 + 1, c0 + 1] * fr * fc
    )


def cast_shadow(
    dem_buf: NDArray[np.floating],
    res: float,
    az_deg: float,
    elev_deg: float,
    out_slice: tuple[slice, slice] | None = None,
    step_frac: float = 0.5,
    z_cap: float | None = None,
) -> Bool:
    """Return True where terrain between a cell and the sun rises above the sun's elevation angle.

    Brute-force horizon-angle march (Dozier & Frew 1990) from each target cell towards the sun
    in steps of step_frac * res (m), bilinear on the buffered DEM (m); samples beyond the array
    are ignored (no wrap). The march stops where it passes z_cap (m), the highest possible
    terrain; z_cap=None marches to the array edge. Targets are `out_slice` of the array.
    """
    if not 0 < step_frac <= 1:
        raise ValueError("step_frac must be in (0, 1].")
    z = np.asarray(dem_buf, dtype=np.float64)
    ny, nx = z.shape
    sl = out_slice or (slice(0, ny), slice(0, nx))
    rr, cc = np.mgrid[sl]
    out_shape = rr.shape
    rr, cc = rr.ravel().astype(np.float64), cc.ravel().astype(np.float64)
    z0 = z[rr.astype(np.intp), cc.astype(np.intp)]
    tan_e = math.tan(math.radians(elev_deg))
    ex, ny_ = math.sin(math.radians(az_deg)), math.cos(math.radians(az_deg))
    far = math.hypot(ny, nx) * res
    d_lim = np.full(z0.shape, far) if z_cap is None else np.clip((z_cap - z0) / tan_e, 0.0, far)
    best = np.full(z0.shape, -np.inf)
    step = step_frac * res
    for k in range(1, int(math.ceil(d_lim.max() / step)) + 1):
        d = k * step
        r, c = rr - ny_ * d / res, cc + ex * d / res
        ok = (d <= d_lim) & (r >= 0) & (r <= ny - 1) & (c >= 0) & (c <= nx - 1)
        idx = np.flatnonzero(ok)
        if idx.size == 0:
            if not (d <= d_lim).any():
                break
            continue
        zs = _bilinear(z, r[idx], c[idx])
        ratio = (zs - z0[idx]) / d
        ratio = np.where(np.isfinite(ratio), ratio, -np.inf)
        best[idx] = np.maximum(best[idx], ratio)
    return (best > tan_e).reshape(out_shape)


def scene_shadow(
    dem_buf: NDArray[np.floating],
    res: float,
    az_deg: float,
    elev_deg: float,
    buf_cells: int,
    step_frac: float = 0.5,
    z_cap: float | None = HIGHEST_POINT_M,
    check_buffer: bool = True,
    z_min: float | None = None,
) -> dict[str, NDArray]:
    """Return the AOI-grid shadow masks and cos i for one sun position (cells cropped by buf_cells).

    Keys: 'self' (cos i <= 0), 'cast' (horizon above the sun), 'any' (union), all bool, and
    'cos_i' float32 in [-1, 1]. `dem_buf` is the buffered DEM in m; az 0-360, elev 0-90 deg.
    Raises if the buffer is too narrow for this elevation: horizon_buffer_m(z_min of the AOI
    cells or `z_min` (m, e.g. the minimum inside the AOI polygon, which can be higher than the
    bounding box's), elev, z_cap) must not exceed buf_cells * res (z_cap=None: the DEM maximum);
    check_buffer=False disables this, for synthetic geometry tests only.
    """
    ny, nx = dem_buf.shape
    b = buf_cells
    if b < 1:
        raise ValueError("buf_cells must be >= 1 (the Horn ring must stay outside the AOI).")
    inner = (slice(b, ny - b), slice(b, nx - b))
    z_top = float(np.nanmax(dem_buf)) if z_cap is None else z_cap
    z_lo = float(np.nanmin(dem_buf[inner])) if z_min is None else z_min
    need = horizon_buffer_m(z_lo, elev_deg, z_top)
    if check_buffer and need > b * res:
        raise ValueError(
            f"DEM buffer {b * res:g} m is narrower than the {need:.0f} m needed at sun elevation "
            f"{elev_deg:.2f} deg; load a wider buffer."
        )
    cos_i = cos_incidence(dem_buf, res, az_deg, elev_deg)[inner]
    self_sh = cos_i <= 0
    cast = cast_shadow(dem_buf, res, az_deg, elev_deg, inner, step_frac, z_cap)
    return {"self": self_sh, "cast": cast, "any": self_sh | cast, "cos_i": cos_i}


# --------------------------------------------------------------------------------------------
# Sentinel-2 MTD_TL sun-angle grid (D5 check)
# --------------------------------------------------------------------------------------------


def _grid_values(el: ET.Element) -> NDArray[np.float64]:
    rows = [[float(v) for v in r.text.split()] for r in el.iter("VALUES") if r.text]
    return np.asarray(rows, dtype=np.float64)


def parse_sun_grid(xml: bytes | str) -> dict[str, Any]:
    """Parse a Sentinel-2 MTD_TL.xml into the 5 km sun-angle grids (deg) and tile geocoding (m).

    Returns ulx, uly (tile 10 m grid corner, EPSG code in 'crs'), step (m), 'zenith' and
    'azimuth' (rows x cols, row 0 at uly), and the tile-mean angles 'mean_zenith'/'mean_azimuth'.
    """
    root = ET.fromstring(xml)
    for el in root.iter():  # drop XML namespaces so that tags can be matched by name
        el.tag = el.tag.rsplit("}", 1)[-1]
    sun = next(root.iter("Sun_Angles_Grid"))
    zen = sun.find("Zenith")
    azi = sun.find("Azimuth")
    mean = next(root.iter("Mean_Sun_Angle"))
    geo = next(g for g in root.iter("Geoposition") if g.get("resolution") == "10")
    step = float(zen.find("COL_STEP").text)  # type: ignore[union-attr,arg-type]
    return {
        "crs": next(root.iter("HORIZONTAL_CS_CODE")).text,
        "ulx": float(geo.find("ULX").text),  # type: ignore[union-attr,arg-type]
        "uly": float(geo.find("ULY").text),  # type: ignore[union-attr,arg-type]
        "step": step,
        "zenith": _grid_values(zen),  # type: ignore[arg-type]
        "azimuth": _grid_values(azi),  # type: ignore[arg-type]
        "mean_zenith": float(mean.find("ZENITH_ANGLE").text),  # type: ignore[union-attr,arg-type]
        "mean_azimuth": float(mean.find("AZIMUTH_ANGLE").text),  # type: ignore[union-attr,arg-type]
    }


def sun_angles_at(grid: dict[str, Any], x: float, y: float) -> tuple[float, float]:
    """Return (azimuth, zenith) in deg, bilinear at UTM (x, y) in m inside the grid."""
    col = (x - grid["ulx"]) / grid["step"]
    row = (grid["uly"] - y) / grid["step"]
    zen = grid["zenith"]
    if not (0 <= row <= zen.shape[0] - 1 and 0 <= col <= zen.shape[1] - 1):
        raise ValueError("Point is outside the sun-angle grid.")
    r, c = np.array([row]), np.array([col])
    return float(_bilinear(grid["azimuth"], r, c)[0]), float(_bilinear(zen, r, c)[0])


def sun_grid_delta(
    grid: dict[str, Any], points_xy: Sequence[tuple[float, float]]
) -> dict[str, float]:
    """Return tile-mean vs AOI-point sun-angle differences (deg) over several UTM points (m).

    Keys: d_az_max, d_zen_max (max |point - tile mean|), spread_az, spread_zen (max - min across
    the points, i.e. the variation within the AOI), and slope_equiv_deg = max(|dz|, |daz| sin z).
    """
    pts = np.array([sun_angles_at(grid, x, y) for x, y in points_xy])
    d_az = pts[:, 0] - grid["mean_azimuth"]
    d_zen = pts[:, 1] - grid["mean_zenith"]
    sin_z = math.sin(math.radians(grid["mean_zenith"]))
    return {
        "d_az_max": float(np.abs(d_az).max()),
        "d_zen_max": float(np.abs(d_zen).max()),
        "spread_az": float(np.ptp(pts[:, 0])),
        "spread_zen": float(np.ptp(pts[:, 1])),
        "slope_equiv_deg": float(max(np.abs(d_zen).max(), np.abs(d_az).max() * sin_z)),
    }


# --------------------------------------------------------------------------------------------
# Aggregation per composite
# --------------------------------------------------------------------------------------------


def to_fine(a30: NDArray, factor: int = 3, shape: tuple[int, int] | None = None) -> NDArray:
    """Replicate each coarse cell into a factor x factor block (last two axes), cropped to `shape`.

    Exact when the grids nest (same top-left corner); no interpolation, so it is safe for flags.
    """
    out = np.repeat(np.repeat(a30, factor, axis=-2), factor, axis=-1)
    if shape is not None:
        out = out[..., : shape[0], : shape[1]]
    return out


def shadow_share(valid: Bool, shadow: Bool) -> tuple[NDArray[np.uint16], F32]:
    """Return (n_shadow uint16, share float32 0-1) over axis 0 of (t, y, x) bool stacks.

    n_shadow counts valid & shadowed observations; share = n_shadow / n_obs, NaN where n_obs = 0.
    """
    n_obs = valid.sum(axis=0, dtype=np.int32)
    n_sh = (valid & shadow).sum(axis=0, dtype=np.int32)
    share = np.full(n_obs.shape, np.nan, dtype=np.float32)
    np.divide(n_sh, n_obs, out=share, where=n_obs > 0, casting="unsafe")
    return n_sh.astype(np.uint16), share


def shadow_flag(share: F32, cut: float = SHADOW_SHARE_CUT) -> Bool:
    """Return True where at least `cut` (0-1, default 0.5) of the valid observations were shadowed.

    NaN share (no valid observation) gives False. ADR 0016 (D6): at s >= n/2 the median takes
    (or averages in) a shadowed value.
    """
    with np.errstate(invalid="ignore"):
        return np.nan_to_num(share, nan=-1.0) >= cut


def mean_over_valid(valid: Bool, x: F32) -> F32:
    """Return the mean of x over valid observations (axis 0), NaN where there are none."""
    n = valid.sum(axis=0)
    tot = np.where(valid, x, 0.0).sum(axis=0, dtype=np.float64)
    out = np.full(n.shape, np.nan, dtype=np.float32)
    np.divide(tot, n, out=out, where=n > 0, casting="unsafe")
    return out


def min_over_valid(valid: Bool, x: F32) -> F32:
    """Return the minimum of x over valid observations (axis 0), NaN where there are none."""
    out = np.where(valid, x, np.inf).min(axis=0).astype(np.float32)
    out[~valid.any(axis=0)] = np.nan
    return out


SHADOW_BANDS = ("shadow_share", "n_shadow", "cos_i_mean", "cos_i_min")


def load_clear_masks(path: str | Path) -> dict[str, Any]:
    """Load an M1.2 clear-mask npz: bool 'valid' (t, y, x), 'acq_ids', sun 'az'/'elev' (deg)."""
    z = np.load(path)
    shape = tuple(int(v) for v in z["shape"])
    valid = np.unpackbits(z["packed"], axis=-1, count=shape[-1]).astype(bool)
    return {
        "valid": valid,
        "acq_ids": [str(a) for a in z["acq_ids"]],
        "az": z["sun_azimuth_deg"].astype(np.float64),
        "elev": z["sun_elevation_deg"].astype(np.float64),
    }


def _sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def composite_shadow(
    npz_path: str | Path,
    dem_buf: NDArray[np.floating],
    buf_cells: int,
    fine: GeoBox,
    inside: Bool,
    out_dir: str | Path,
    step_frac: float = 0.5,
    res: float = TERRAIN_RES_M,
    force: bool = False,
    z_cap: float | None = HIGHEST_POINT_M,
    check_buffer: bool = True,
    z_min: float | None = None,
) -> Path:
    """Write <stem>_shadow.tif (4 float32 bands, 10 m) and <stem>_shadow.json for one window-year.

    Bands: shadow_share (0-1), n_shadow (count), cos_i_mean, cos_i_min ([-1, 1]), each over the
    window's valid observations, NaN where there is none or outside the AOI. Shadow is computed
    per scene from that scene's own sun angles (deg) in the npz, then aggregated. Resumable: skipped
    when the JSON records the same npz sha256, DEM sha256 and parameters.
    """
    from highland.io import write_cog

    npz_path, out_dir = Path(npz_path), Path(out_dir)
    stem = npz_path.stem
    tif, meta = out_dir / f"{stem}_shadow.tif", out_dir / f"{stem}_shadow.json"
    params = {
        "schema": 1,
        "npz_sha256": _sha256_file(npz_path),
        "dem_sha256": hashlib.sha256(
            np.ascontiguousarray(dem_buf, np.float32).tobytes()
        ).hexdigest(),
        "buf_cells": buf_cells,
        "step_frac": step_frac,
        "res": res,
        "z_cap": z_cap,
        "shape": list(fine.shape),
    }
    if not force and tif.exists() and meta.exists():
        if json.loads(meta.read_text()).get("params") == params:
            return tif
    m = load_clear_masks(npz_path)
    valid = m["valid"]
    if valid.shape[1:] != tuple(fine.shape):
        raise ValueError(f"mask shape {valid.shape[1:]} differs from the grid {tuple(fine.shape)}.")
    nt = valid.shape[0]
    shadow = np.zeros(valid.shape, dtype=bool)
    cos_i = np.zeros(valid.shape, dtype=np.float32)
    scenes = []
    n_inside = int(inside.sum())
    for t in range(nt):
        sc = scene_shadow(
            dem_buf,
            res,
            float(m["az"][t]),
            float(m["elev"][t]),
            buf_cells,
            step_frac,
            z_cap,
            check_buffer,
            z_min,
        )
        shape = tuple(fine.shape)
        f = {k: to_fine(sc[k], 3, shape) for k in ("self", "cast", "any", "cos_i")}
        shadow[t], cos_i[t] = f["any"], f["cos_i"]
        v = valid[t] & inside
        scenes.append(
            {
                "acq_id": m["acq_ids"][t],
                "sun_azimuth_deg": float(m["az"][t]),
                "sun_elevation_deg": float(m["elev"][t]),
                "share_self": float((f["self"] & inside).sum() / n_inside),
                "share_cast": float((f["cast"] & inside).sum() / n_inside),
                "share_any": float((f["any"] & inside).sum() / n_inside),
                "n_valid": int(v.sum()),
                "valid_shadowed_share": float((v & f["any"]).sum() / v.sum()) if v.any() else None,
            }
        )
    valid &= inside
    n_sh, share = shadow_share(valid, shadow)
    bands = np.stack(
        [
            share,
            n_sh.astype(np.float32),
            mean_over_valid(valid, cos_i),
            min_over_valid(valid, cos_i),
        ]
    ).astype(np.float32)
    bands[:, ~inside] = np.nan
    write_cog(
        bands,
        tif,
        fine.transform,
        "EPSG:32644",
        np.nan,
        descriptions=list(SHADOW_BANDS),
        tags={
            "terrain_flag": "per-scene self+cast shadow, share of valid obs (ADR 0016)",
            "source_masks": npz_path.name,
            "npz_sha256": params["npz_sha256"],
        },
    )
    flagged = shadow_flag(share) & inside
    meta.write_text(
        json.dumps(
            {
                "params": params,
                "n_scenes": nt,
                "share_flag_ge_half": float(flagged.sum() / n_inside),
                "share_any_shadow": float(((n_sh > 0) & inside).sum() / n_inside),
                "scenes": scenes,
            },
            indent=1,
        )
    )
    return tif
