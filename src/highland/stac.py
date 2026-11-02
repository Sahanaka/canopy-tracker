"""STAC catalog search, signing and lazy loading (Planetary Computer, AWS Earth Search)."""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import geopandas as gpd
import pandas as pd
import pystac
import pystac_client
from affine import Affine
from odc.geo.geobox import GeoBox

from highland.aoi import WORK_CRS

log = logging.getLogger(__name__)

PC_STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
S2_COLLECTION = "sentinel-2-l2a"
LANDSAT_COLLECTION = "landsat-c2-l2"
LOCAL_TZ = ZoneInfo("Asia/Colombo")  # Sri Lanka local time, for the solar day of an overpass

# Native grid asset per sensor: SCL (20 m) and QA_PIXEL (30 m).
GRID_ASSET = {"s2": "SCL", "landsat": "qa_pixel"}
NATIVE_RESOLUTION_M = {"s2": 20.0, "landsat": 30.0}


def open_client(url: str = PC_STAC_URL) -> pystac_client.Client:
    """Open the Planetary Computer STAC API (no signing; sign assets just before reading)."""
    return pystac_client.Client.open(url)


def search_items(
    collection: str,
    aoi: gpd.GeoDataFrame,
    start: str,
    end: str,
    client: pystac_client.Client | None = None,
) -> list[pystac.Item]:
    """Return all unsigned items intersecting the AOI between two ISO dates (UTC)."""
    client = client or open_client()
    geom = aoi.to_crs("EPSG:4326").geometry.iloc[0]
    search = client.search(
        collections=[collection],
        intersects=geom.__geo_interface__,
        datetime=f"{start}/{end}",
        limit=500,
    )
    items = list(search.items())
    log.info("%s %s..%s: %d items", collection, start, end, len(items))
    return items


def save_items(items: Sequence[pystac.Item], path: str | Path) -> Path:
    """Cache items as an ItemCollection JSON file so reruns skip the search; return the path."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    pystac.ItemCollection(list(items)).save_object(str(p))
    return p


def load_items(path: str | Path) -> list[pystac.Item]:
    """Load items cached by save_items."""
    return list(pystac.ItemCollection.from_file(str(path)))


def parse_baseline(s: str) -> tuple[int, int]:
    """Parse a Sentinel-2 processing baseline such as '05.10' to (5, 10); never as a float."""
    parts = str(s).strip().split(".")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError(f"Cannot parse processing baseline {s!r}; expected 'NN.NN'.")
    return int(parts[0]), int(parts[1])


def s2_processing_time(item: pystac.Item) -> str:
    """Return the processing time (YYYYMMDDTHHMMSS, UTC) from the last field of a S2 item id."""
    last = item.id.split("_")[-1]
    if len(last) != 15 or last[8] != "T":
        gen = item.properties.get("s2:generation_time")
        if gen:  # fall back to the STAC property when the id suffix cannot be parsed
            return datetime.fromisoformat(str(gen).replace("Z", "+00:00")).strftime("%Y%m%dT%H%M%S")
        raise ValueError(f"Item id {item.id!r} does not end in a YYYYMMDDTHHMMSS processing time.")
    return last


def _dt_key(item: pystac.Item) -> str:
    if item.datetime is None:
        raise ValueError(f"Item {item.id} has no datetime.")
    return item.datetime.astimezone(UTC).isoformat()


def dedupe_s2(items: Sequence[pystac.Item]) -> tuple[list[pystac.Item], pd.DataFrame]:
    """Drop reprocessed S2 duplicates per (datetime, MGRS tile); return kept, dropped table."""
    groups: dict[tuple[str, str], list[pystac.Item]] = defaultdict(list)
    for it in items:
        groups[(_dt_key(it), str(it.properties["s2:mgrs_tile"]))].append(it)

    def rank(it: pystac.Item) -> tuple[tuple[int, int], str]:
        return parse_baseline(it.properties["s2:processing_baseline"]), s2_processing_time(it)

    kept: list[pystac.Item] = []
    dropped: list[dict[str, object]] = []
    for (dt, tile), members in groups.items():
        members = sorted(members, key=rank, reverse=True)
        best = members[0]
        kept.append(best)
        for other in members[1:]:
            same_bl = rank(other)[0] == rank(best)[0]
            dropped.append(
                {
                    "dropped_id": other.id,
                    "kept_id": best.id,
                    "datetime": dt,
                    "mgrs_tile": tile,
                    "dropped_baseline": other.properties["s2:processing_baseline"],
                    "kept_baseline": best.properties["s2:processing_baseline"],
                    "reason": "older_processing_time" if same_bl else "lower_baseline",
                }
            )
    kept.sort(key=lambda i: (_dt_key(i), str(i.properties["s2:mgrs_tile"])))

    # Same tile, orbit and platform on one day but different datetimes: logged, never merged.
    by_day: dict[tuple[object, ...], set[str]] = defaultdict(set)
    for it in kept:
        day = _dt_key(it)[:10]
        k = (
            day,
            it.properties["s2:mgrs_tile"],
            it.properties.get("sat:relative_orbit"),
            it.properties.get("platform"),
        )
        by_day[k].add(_dt_key(it))
    near = [k for k, v in by_day.items() if len(v) > 1]
    if near:
        log.warning(
            "%d same-day S2 tile/orbit/platform groups with differing datetimes: %s",
            len(near),
            near[:5],
        )

    cols = [
        "dropped_id",
        "kept_id",
        "datetime",
        "mgrs_tile",
        "dropped_baseline",
        "kept_baseline",
        "reason",
    ]
    return kept, pd.DataFrame(dropped, columns=cols)


def local_date(dt: datetime) -> str:
    """Return the Sri Lanka local calendar date (YYYY-MM-DD) of a timezone-aware datetime."""
    return dt.astimezone(LOCAL_TZ).date().isoformat()


def acquisition_groups(items: Sequence[pystac.Item], sensor: str) -> dict[str, list[pystac.Item]]:
    """Group items of one pass by (platform, local solar day); key '<platform>_<YYYY-MM-DD>'."""
    if sensor not in GRID_ASSET:
        raise ValueError(f"sensor must be 's2' or 'landsat', got {sensor!r}")
    groups: dict[str, list[pystac.Item]] = defaultdict(list)
    for it in items:
        if it.datetime is None:
            raise ValueError(f"Item {it.id} has no datetime.")
        platform = str(it.properties["platform"]).lower()
        groups[f"{platform}_{local_date(it.datetime)}"].append(it)
    return dict(sorted(groups.items(), key=lambda kv: kv[0].split("_", 1)[1]))


def native_grid(item: pystac.Item, asset: str) -> tuple[str, Affine]:
    """Return (CRS string, affine transform) of an item's asset grid from STAC metadata."""
    a = item.assets[asset].extra_fields
    props = item.properties
    tr = a.get("proj:transform") or props.get("proj:transform")
    code = a.get("proj:code") or props.get("proj:code")
    if code is None:
        epsg = a.get("proj:epsg") or props.get("proj:epsg")
        code = f"EPSG:{epsg}" if epsg else None
    if tr is None or code is None:
        raise ValueError(f"Item {item.id} asset {asset} lacks proj:transform or proj:code/epsg.")
    return str(code), Affine(*tr[:6])


def aoi_geobox(aoi: gpd.GeoDataFrame, items: Sequence[pystac.Item], sensor: str) -> GeoBox:
    """Return the EPSG:32644 GeoBox of the AOI bbox snapped outward to the native grid (m)."""
    if not items:
        raise ValueError("No items to take a native grid from.")
    asset = GRID_ASSET[sensor]
    res = NATIVE_RESOLUTION_M[sensor]
    grids = [native_grid(i, asset) for i in items]
    crs0, t0 = grids[0]
    for it, (_, t) in zip(items, grids, strict=True):
        if abs(t.a) != res or abs(t.e) != res:
            raise ValueError(
                f"{sensor}: item {it.id} {asset} grid is {abs(t.a)} x {abs(t.e)} m, "
                f"expected {res} m; refusing to resample silently."
            )
    if crs0 != WORK_CRS:
        log.warning("First item CRS is %s, not %s; reads will resample (nearest).", crs0, WORK_CRS)
    offsets = {(round(t.c % res, 6), round(t.f % res, 6)) for _, t in grids}
    if len(offsets) > 1 or any(c != crs0 for c, _ in grids):
        log.warning(
            "Items differ in CRS or grid origin (%s); odc-stac will resample with nearest.", offsets
        )
    x0, y0 = t0.c, t0.f
    minx, miny, maxx, maxy = aoi.to_crs(WORK_CRS).total_bounds
    left = x0 + math.floor((minx - x0) / res) * res
    right = x0 + math.ceil((maxx - x0) / res) * res
    bottom = y0 + math.floor((miny - y0) / res) * res
    top = y0 + math.ceil((maxy - y0) / res) * res
    nx = round((right - left) / res)
    ny = round((top - bottom) / res)
    return GeoBox((ny, nx), Affine(res, 0.0, left, 0.0, -res, top), WORK_CRS)
