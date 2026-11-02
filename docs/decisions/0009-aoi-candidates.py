"""Score candidate 10x10 km AOIs (UTM 44N) around the Bandarawela-Haputale midpoint (ADR 0009).

Usage: python docs/decisions/0009-aoi-candidates.py OUT_DIR
Needs network; writes OUT_DIR/candidates.gpkg and prints the score table.
Town coordinates are OpenStreetMap Nominatim results from 2026-11-02, recorded in ADR 0009.
"""

import sys

import geopandas as gpd
import numpy as np
import odc.stac
import planetary_computer
import pystac_client
from pyproj import Transformer
from shapely.geometry import Point, box

PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
TOWNS = {"Bandarawela": (80.9888204, 6.8304821), "Haputale": (80.9602296, 6.7681528)}
OFFSETS_M = [-3000, -1500, 0, 1500, 3000]
HALF_SIDE_M = 5000
WORLDCOVER = {
    10: "tree",
    20: "shrub",
    30: "grass",
    40: "crop",
    50: "built",
    60: "bare",
    80: "water",
    90: "wetland",
    95: "mangrove",
    100: "moss",
}


def _inside(xs: np.ndarray, ys: np.ndarray, bounds: tuple) -> np.ndarray:
    """Return a boolean grid of pixel centres inside the square bounds (metres, EPSG:32644)."""
    gx, gy = np.meshgrid(xs, ys)
    x0, y0, x1, y1 = bounds
    return (gx >= x0) & (gx < x1) & (gy >= y0) & (gy < y1)


def main(out: str) -> None:
    """Build, score and save the candidate squares."""
    to_utm = Transformer.from_crs(4326, 32644, always_xy=True)
    tx = {k: to_utm.transform(*v) for k, v in TOWNS.items()}
    mx = (tx["Bandarawela"][0] + tx["Haputale"][0]) / 2
    my = (tx["Bandarawela"][1] + tx["Haputale"][1]) / 2
    rows = []
    for dx in OFFSETS_M:
        for dy in OFFSETS_M:
            cx, cy = mx + dx, my + dy
            sq = box(cx - HALF_SIDE_M, cy - HALF_SIDE_M, cx + HALF_SIDE_M, cy + HALF_SIDE_M)
            rows.append({"cand": f"E{dx // 100:+d}_N{dy // 100:+d}", "geometry": sq})
    c = gpd.GeoDataFrame(rows, crs=32644)
    for k, (x, y) in tx.items():
        c[f"has_{k}"] = c.contains(Point(x, y))
    bb = gpd.GeoSeries([box(*c.total_bounds)], crs=32644).to_crs(4326).total_bounds

    cat = pystac_client.Client.open(PC_STAC, modifier=planetary_computer.sign_inplace)
    wc_items = [i for i in cat.search(collections=["esa-worldcover"], bbox=bb).items()]
    wc_items = [i for i in wc_items if "2021" in i.id]
    wc = odc.stac.load(
        wc_items, bands=["map"], crs="EPSG:32644", resolution=10, bbox=bb, resampling="nearest"
    ).isel(time=0)["map"]
    for i, g in enumerate(c.geometry):
        v = wc.values[_inside(wc.x.values, wc.y.values, g.bounds)]
        for code, name in WORLDCOVER.items():
            c.loc[i, f"wc_{name}"] = round(100 * np.count_nonzero(v == code) / v.size, 1)

    dem_items = list(cat.search(collections=["cop-dem-glo-30"], bbox=bb).items())
    dem = odc.stac.load(
        dem_items, bands=["data"], crs="EPSG:32644", resolution=30, bbox=bb, resampling="bilinear"
    ).isel(time=0)["data"]
    z = dem.values.astype("float64")
    gy, gx = np.gradient(z, 30.0)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    for i, g in enumerate(c.geometry):
        m = _inside(dem.x.values, dem.y.values, g.bounds)
        c.loc[i, "slope_median_deg"] = round(float(np.median(slope[m])), 1)
        c.loc[i, "elev_min_m"] = int(np.min(z[m]))
        c.loc[i, "elev_max_m"] = int(np.max(z[m]))

    c.to_file(f"{out}/candidates.gpkg")
    print(c.drop(columns="geometry").sort_values("wc_tree", ascending=False).to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1])
