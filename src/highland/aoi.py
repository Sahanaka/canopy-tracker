"""Load and validate the area of interest; reproject it (stored EPSG:4326, work EPSG:32644)."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import geopandas as gpd
import shapely
from pyproj import CRS
from shapely.geometry import MultiPolygon, Polygon
from shapely.validation import explain_validity

log = logging.getLogger(__name__)

# Sri Lanka bounding box (west, south, east, north), degrees lon/lat. Source: EPSG area of use
# "Sri Lanka - onshore" of EPSG:5235 (SLD99 / Sri Lanka Grid 1999); see ADR 0007.
SRI_LANKA_BBOX: tuple[float, float, float, float] = (79.64, 5.86, 81.95, 9.88)
# Allowed AOI area in km^2 (owner's numbers from docs/ROADMAP.md M0.3).
AREA_KM2_RANGE: tuple[float, float] = (50.0, 200.0)
WORK_CRS = "EPSG:32644"
AOI_CRS = "EPSG:4326"

_README_HINT = "see data/aoi/README.md for how to draw and export the AOI"


class AOIError(ValueError):
    """The AOI file breaks a validation rule; the message names the rule and the measured value."""


def default_aoi_path() -> Path:
    """Return the absolute path of data/aoi/aoi.geojson in the repository (editable install)."""
    return Path(__file__).resolve().parents[2] / "data" / "aoi" / "aoi.geojson"


def to_utm(aoi: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Reproject an AOI GeoDataFrame to EPSG:32644 (UTM 44N, metres)."""
    return aoi.to_crs(WORK_CRS)


def aoi_area_km2(aoi: gpd.GeoDataFrame) -> float:
    """Return the planar AOI area in EPSG:32644, in km^2 (0.03-0.08 % below ellipsoidal area)."""
    return float(to_utm(aoi).area.sum() / 1e6)


def _in_bbox(bounds: tuple[float, float, float, float]) -> bool:
    w, s, e, n = SRI_LANKA_BBOX
    return bool(bounds[0] >= w and bounds[1] >= s and bounds[2] <= e and bounds[3] <= n)


def load_aoi(path: str | Path | None = None) -> gpd.GeoDataFrame:
    """Load and validate the AOI; return one CCW Polygon row in EPSG:4326 (degrees)."""
    p = Path(path) if path is not None else default_aoi_path()
    if not p.exists():
        raise FileNotFoundError(
            f"AOI file not found: {p}. Draw it and save it there ({_README_HINT}). "
            "If highland is not an editable install, pass the path explicitly: load_aoi(path=...)."
        )
    try:
        gdf = gpd.read_file(p)
    except Exception as exc:  # GDAL/pyogrio raise several types for unreadable files
        raise AOIError(
            f"Cannot read {p} as GeoJSON ({type(exc).__name__}: {exc}). The file may be empty or "
            f"malformed; {_README_HINT}."
        ) from exc
    if len(gdf) != 1:
        raise AOIError(
            f"Expected exactly 1 feature, found {len(gdf)}. Draw a single polygon (features are "
            "never merged automatically)."
        )

    # GDAL labels 3-D GeoJSON as EPSG:4979, so compare the datum (WGS84), not the code.
    if (
        gdf.crs is None
        or not gdf.crs.is_geographic
        or gdf.crs.datum != CRS.from_user_input(AOI_CRS).datum
    ):
        raise AOIError(
            f"CRS is {gdf.crs.to_string() if gdf.crs is not None else 'missing'}, expected "
            f"{AOI_CRS}. Re-export as EPSG:4326 (longitude, latitude in degrees); it is not "
            "reprojected silently."
        )

    geom = shapely.force_2d(gdf.geometry.iloc[0])
    if geom is None or geom.is_empty:
        raise AOIError("The geometry is empty. Draw a polygon.")
    if isinstance(geom, MultiPolygon) and len(geom.geoms) == 1:
        geom = geom.geoms[0]
    if not isinstance(geom, Polygon):
        parts = f" with {len(geom.geoms)} parts" if isinstance(geom, MultiPolygon) else ""
        raise AOIError(
            f"Geometry type is {geom.geom_type}{parts}; expected a single Polygon. "
            "Draw one polygon only."
        )
    if not geom.is_valid:
        raise AOIError(
            f"Invalid polygon: {explain_validity(geom)}. Fix the outline (for example a "
            "self-crossing edge); it is not repaired automatically."
        )
    if len(geom.interiors) > 0:
        raise AOIError(
            f"The polygon has {len(geom.interiors)} hole(s); the AOI must be a solid polygon "
            "without holes."
        )

    minx, miny, maxx, maxy = geom.bounds
    if not (-180 <= minx and maxx <= 180 and -90 <= miny and maxy <= 90):
        raise AOIError(
            f"Coordinates ({minx:.4g}, {miny:.4g}) to ({maxx:.4g}, {maxy:.4g}) are not degrees "
            "(they exceed +/-180 / +/-90); they look projected. Re-export as EPSG:4326."
        )

    bounds = (minx, miny, maxx, maxy)
    if not _in_bbox(bounds):
        if _in_bbox((miny, minx, maxy, maxx)):
            raise AOIError(
                f"lon/lat look swapped: bounds {tuple(round(b, 5) for b in bounds)} only fit "
                "Sri Lanka as (lat, lon). "
                "GeoJSON order is longitude, then latitude."
            )
        w, s, e, n = SRI_LANKA_BBOX
        raise AOIError(
            f"AOI bounds (W {minx:.4f}, S {miny:.4f}, E {maxx:.4f}, N {maxy:.4f}) are not inside "
            f"Sri Lanka's bounding box (W {w}, S {s}, E {e}, N {n})."
        )

    # Normalise ring orientation silently (RFC 7946 sec. 3.1.6): CCW exterior.
    geom = shapely.orient_polygons(geom, exterior_cw=False)
    out = gdf.copy()
    out["geometry"] = [geom]
    out = out.set_geometry("geometry").set_crs(AOI_CRS, allow_override=True)

    area = aoi_area_km2(out)
    lo, hi = AREA_KM2_RANGE
    if not (lo <= area <= hi):
        raise AOIError(
            f"AOI area is {area:.1f} km^2 (planar, EPSG:32644); it must be between {lo:g} and "
            f"{hi:g} km^2. Redraw it at roughly 10 x 10 km."
        )
    log.info("AOI %s: area %.1f km^2, bounds %s", p, area, bounds)
    return out


def main(argv: list[str] | None = None) -> int:
    """Validate an AOI file from the command line; return exit code 0 (OK) or 1 (error)."""
    ap = argparse.ArgumentParser(prog="python -m highland.aoi", description=__doc__)
    ap.add_argument(
        "path", nargs="?", default=None, help="AOI GeoJSON (default: data/aoi/aoi.geojson)"
    )
    args = ap.parse_args(argv)
    try:
        aoi = load_aoi(args.path)
    except (AOIError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}")
        return 1
    b = aoi.total_bounds
    print(f"area: {aoi_area_km2(aoi):.1f} km^2")
    print(f"bounds (W, S, E, N): {b[0]:.5f}, {b[1]:.5f}, {b[2]:.5f}, {b[3]:.5f}")
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
