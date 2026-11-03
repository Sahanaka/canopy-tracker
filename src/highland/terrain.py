"""DEM derivatives (Horn slope, aspect, hillshade, cos i) and the per-scene terrain-shadow flag.

Conventions: rows run north to south, columns west to east, UTM zone 44N (EPSG:32644), metres.
Aspect is the downslope compass bearing (0-360 deg clockwise from north); sun azimuth is clockwise
from north and sun elevation is measured from the horizon (STAC view extension; ADR 0015/0016).
UTM grid north is taken as true north (meridian convergence <= 0.012 deg across the AOI).
"""

from __future__ import annotations

import math

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
