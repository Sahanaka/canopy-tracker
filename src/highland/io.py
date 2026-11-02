"""GeoTIFF and GeoJSON export; load and validate QField field points (docs/field/form.md)."""

from __future__ import annotations

import argparse
import logging
import math
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import pyogrio
import shapely
from pyproj import CRS
from shapely.geometry import Point

from highland.aoi import AOI_CRS, SRI_LANKA_BBOX, default_aoi_path, load_aoi

log = logging.getLogger(__name__)

# Vocabulary of the QField form (codes are stored; labels live in the QGIS Value Map). See ADR 0008.
FIELD_CLASSES: tuple[str, ...] = (
    "pine",
    "turpentine",
    "eucalyptus",
    "native_broadleaf",
    "alstonia",
    "tea",
    "grassland",
    "settlement",
    "other",
    "unsure",
)
# Codes that collapse into the single Module B class "other" when M2.3 wants the coarse scheme.
OTHER_GROUP: frozenset[str] = frozenset({"tea", "grassland", "settlement", "other"})
OPENED_VALUES: tuple[str, ...] = ("yes", "no", "unsure")
OPENNESS_METHODS: tuple[str, ...] = ("visual", "glama", "not_measured")
# Classes usable as reference labels; `unsure` is excluded from training/accuracy, counted apart.
REFERENCE_CLASSES: tuple[str, ...] = tuple(c for c in FIELD_CLASSES if c != "unsure")
REQUIRED_FIELDS: tuple[str, ...] = (
    "point_id",
    "observed_at",
    "observer",
    "class",
    "opened",
    "openness_method",
)
# canopy_open_pct must exist as a column but is required per row only unless openness_method is
# "not_measured" (then it must be empty).
OPENNESS_COLUMN = "canopy_open_pct"
PHOTO_FIELDS: tuple[str, ...] = ("photo_1", "photo_2", "photo_3")
PHOTO_EXTENSIONS: tuple[str, ...] = (".jpg", ".jpeg", ".png")
_KNOWN_FIELDS = frozenset(
    REQUIRED_FIELDS
    + PHOTO_FIELDS
    + (
        "canopy_open_pct",
        "secondary_class",
        "patch_id",
        "opened_years_ago",
        "gps_accuracy_m",
        "gps_time_utc",
        "position_source",
        "notes",
        "geometry",
    )
)
_FORM_HINT = "see docs/field/form.md"


class FieldDataError(ValueError):
    """Field files break the schema; `problems` lists every broken rule, one line each."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        shown = "\n".join(f"  - {p}" for p in self.problems)
        super().__init__(
            f"{len(self.problems)} problem(s) found in the field data ({_FORM_HINT}):\n{shown}"
        )


def default_field_dir() -> Path:
    """Return the absolute path of data/field/ in the repository (editable install)."""
    return default_aoi_path().parents[1] / "field"


def _is_null(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def _resolve_paths(paths: str | Path | Sequence[str | Path] | None) -> list[Path]:
    """Expand files/directories into a sorted list of GeoJSON files."""
    if paths is None:
        base = default_field_dir()
        found = sorted(base.glob("*.geojson")) + sorted((base / "private").glob("*.geojson"))
        if not found:
            raise FileNotFoundError(
                f"No field files found in {base} (or {base / 'private'}). Export your QField layer "
                f"as GeoJSON into data/field/ ({_FORM_HINT}), or pass the path explicitly."
            )
        return found
    items = [paths] if isinstance(paths, str | Path) else list(paths)
    out: list[Path] = []
    for item in items:
        p = Path(item)
        if p.is_dir():
            out.extend(sorted(p.glob("*.geojson")))
        elif p.exists():
            out.append(p)
        else:
            raise FileNotFoundError(f"Field file not found: {p}")
    return out


def _in_bbox(lon: float, lat: float) -> bool:
    w, s, e, n = SRI_LANKA_BBOX
    return bool(w <= lon <= e and s <= lat <= n)


def _photo_problem(value: str) -> str | None:
    """Return a rule text if a photo path is malformed, else None."""
    v = value.strip()
    if "\\" in v:
        return "use '/' as the folder separator, not '\\'"
    if v.startswith("/") or (len(v) > 1 and v[1] == ":"):
        return "must be a path relative to the project folder, not an absolute path"
    if ".." in v.split("/"):
        return "must not contain '..'"
    if not v.lower().endswith(PHOTO_EXTENSIONS):
        return f"must end with one of {', '.join(PHOTO_EXTENSIONS)}"
    return None


def _check_row(
    row: pd.Series,
    label: str,
    now: datetime,
    inside: Any,
    problems: list[str],
    photo_root: Path | None,
) -> dict[str, Any] | None:
    """Validate one feature; append problems; return parsed values (None if unusable)."""
    n0 = len(problems)

    def bad(rule: str) -> None:
        problems.append(f"{label}: {rule}")

    for f in REQUIRED_FIELDS:
        if f in row.index and _is_null(row[f]):
            bad(f"required field '{f}' is empty")

    def text(f: str) -> str | None:
        v = row.get(f)
        return None if _is_null(v) else str(v).strip()

    geom = row.geometry
    pt: Point | None = None
    if geom is None or geom.is_empty:
        bad("geometry is empty; every field record needs a point")
    elif geom.geom_type != "Point":
        bad(f"geometry is {geom.geom_type}; expected a single Point")
    else:
        pt = shapely.force_2d(geom)
        lon, lat = pt.x, pt.y
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            bad(f"coordinates ({lon:.6g}, {lat:.6g}) are not degrees (look projected)")
            pt = None
        elif inside is not None and not inside(pt):
            hint = " (lon/lat look swapped: GeoJSON order is longitude, then latitude)"
            swapped = inside(Point(lat, lon)) if -180 <= lat <= 180 and -90 <= lon <= 90 else False
            bad(
                f"point ({lon:.6f}, {lat:.6f}) is outside the AOI polygon"
                + (hint if swapped else "")
            )
            pt = None
        elif inside is None and not _in_bbox(lon, lat):
            hint = " (lon/lat look swapped: GeoJSON order is longitude, then latitude)"
            bad(
                f"point ({lon:.6f}, {lat:.6f}) is outside Sri Lanka's bounding box"
                + (hint if _in_bbox(lat, lon) else "")
            )
            pt = None

    vocab = {
        "class": FIELD_CLASSES,
        "secondary_class": FIELD_CLASSES,
        "opened": OPENED_VALUES,
        "openness_method": OPENNESS_METHODS,
    }
    clean: dict[str, Any] = {}
    for f in ("point_id", "observer", "patch_id", "position_source", "notes"):
        clean[f] = text(f)
    for f, allowed in vocab.items():
        v = text(f)
        clean[f] = v
        if v is not None and v not in allowed:
            bad(f"'{f}' value '{row.get(f)}' is not allowed; use one of {', '.join(allowed)}")
            clean[f] = None
    cls, sec = clean["class"], clean["secondary_class"]
    if sec is not None and sec == cls:
        bad(
            f"'secondary_class' repeats 'class' ('{sec}'); leave it empty or name another component"
        )
    if sec == "unsure":
        bad("'secondary_class' cannot be 'unsure'; leave it empty")

    def whole(f: str, v: Any) -> int | None:
        try:
            x = float(v)
            if not x.is_integer():
                raise ValueError
            return int(x)
        except (TypeError, ValueError):
            bad(f"'{f}' value '{v}' is not a whole number")
            return None

    pct = row.get(OPENNESS_COLUMN)
    pct_val: int | None = None
    if clean["openness_method"] == "not_measured":
        if not _is_null(pct):
            bad("'canopy_open_pct' must be empty when 'openness_method' is not_measured")
    elif clean["openness_method"] in OPENNESS_METHODS:
        if _is_null(pct):
            bad("'canopy_open_pct' is empty; enter 0-100 or set openness_method to not_measured")
        else:
            pct_val = whole(OPENNESS_COLUMN, pct)
            if pct_val is not None and not 0 <= pct_val <= 100:
                bad(f"'canopy_open_pct' value {pct_val} is outside 0-100")
                pct_val = None

    yrs = row.get("opened_years_ago")
    yrs_val: int | None = None
    if not _is_null(yrs):
        yrs_val = whole("opened_years_ago", yrs)
        if yrs_val is not None and yrs_val < 0:
            bad(f"'opened_years_ago' value {yrs_val} must be 0 or more (whole years)")
            yrs_val = None
        if clean["opened"] != "yes":
            bad("'opened_years_ago' is only allowed when 'opened' is yes")
            yrs_val = None

    gps_time: pd.Timestamp | None = None
    graw = row.get("gps_time_utc")
    if not _is_null(graw):
        try:
            g = datetime.fromisoformat(str(graw).strip())
            if g.tzinfo is None:
                raise ValueError
            gps_time = pd.Timestamp(g).tz_convert("UTC")
        except ValueError:
            bad(f"'gps_time_utc' value '{graw}' is not an ISO 8601 date-time with offset (or Z)")

    acc = row.get("gps_accuracy_m")
    acc_val = float("nan")
    if not _is_null(acc):
        try:
            acc_val = float(acc)
            if acc_val < 0 or math.isinf(acc_val):
                raise ValueError
        except (TypeError, ValueError):
            bad(f"'gps_accuracy_m' value '{acc}' must be a number >= 0 (metres) or empty")
            acc_val = float("nan")

    when: pd.Timestamp | None = None
    raw = row.get("observed_at")
    if not _is_null(raw):
        try:
            if not isinstance(raw, str):
                raise ValueError("not text")
            parsed = datetime.fromisoformat(raw.strip())
        except ValueError:
            bad(
                f"'observed_at' value '{raw}' is not an ISO 8601 date-time such as "
                "2026-05-01T10:15:00+05:30"
            )
        else:
            if parsed.tzinfo is None:
                bad(
                    f"'observed_at' value '{raw}' has no UTC offset; add +05:30 for Sri Lanka "
                    "local time (naive times are not guessed)"
                )
            elif parsed > now:
                bad(f"'observed_at' value '{raw}' is in the future; check the phone clock")
            else:
                when = pd.Timestamp(parsed).tz_convert("UTC")

    photos: list[str] = []
    for f in PHOTO_FIELDS:
        v = row.get(f)
        if f in row.index and not _is_null(v):
            v = str(v).strip()
            why = _photo_problem(v)
            if why:
                bad(f"'{f}' value '{v}' {why}")
            elif photo_root is not None and not (photo_root / v).is_file():
                bad(f"'{f}' file '{v}' not found under {photo_root}")
            else:
                photos.append(v)

    if len(problems) > n0 or pt is None or when is None:
        return None
    return {
        **clean,
        "geometry": pt,
        "observed_at": when,
        "pct": pct_val,
        "yrs": yrs_val,
        "gps_time": gps_time,
        "acc": acc_val,
        "photos": photos,
    }


def load_field_points(
    paths: str | Path | Sequence[str | Path] | None = None,
    *,
    aoi: gpd.GeoDataFrame | None = None,
    photo_root: str | Path | None = None,
    now: datetime | None = None,
) -> gpd.GeoDataFrame:
    """Load and validate QField GeoJSON points; return EPSG:4326 (deg), UTC times, openness in %."""
    files = _resolve_paths(paths)
    if aoi is None and default_aoi_path().exists():
        aoi = load_aoi()
    inside = None
    if aoi is not None:
        area = shapely.union_all(aoi.to_crs(AOI_CRS).geometry.values)
        inside = area.covers
    else:
        log.warning(
            "No AOI file found; checking field points against Sri Lanka's bounding box only. "
            "Add data/aoi/aoi.geojson for a stricter check."
        )
    now = now or datetime.now(UTC)
    root = Path(photo_root) if photo_root is not None else None
    wgs84 = CRS.from_user_input(AOI_CRS).datum

    problems: list[str] = []
    rows: list[dict[str, Any]] = []
    seen_ids: dict[str, str] = {}
    field_dir = default_field_dir().resolve()
    seen_geo: dict[tuple[bytes, pd.Timestamp], str] = {}
    for p in files:
        try:
            gdf = pyogrio.read_dataframe(p, datetime_as_string=True)
        except Exception as exc:  # GDAL raises several types for unreadable files
            problems.append(
                f"{p.name}: cannot read as GeoJSON ({type(exc).__name__}); it may be empty or "
                "malformed. Re-export it from QGIS as GeoJSON."
            )
            continue
        if gdf.crs is None or not gdf.crs.is_geographic or gdf.crs.datum != wgs84:
            problems.append(
                f"{p.name}: CRS is {gdf.crs.to_string() if gdf.crs is not None else 'missing'}, "
                f"expected {AOI_CRS}. Re-export with CRS EPSG:4326; it is not reprojected silently."
            )
            continue
        private = p.resolve().parent.name == "private"
        try:
            source = p.resolve().relative_to(field_dir).as_posix()
        except ValueError:
            source = f"private/{p.name}" if private else p.name
        extra = sorted(set(gdf.columns) - _KNOWN_FIELDS)
        if extra:
            log.info("%s: keeping extra properties %s", p.name, extra)
        if len(gdf) == 0:
            log.info("%s: no features", p.name)
            continue
        missing = [f for f in (*REQUIRED_FIELDS, OPENNESS_COLUMN) if f not in gdf.columns]
        if missing:
            problems.append(
                f"{p.name}: required column(s) missing: {', '.join(missing)}. "
                "Check the layer fields against docs/field/form.md."
            )
            continue
        for i, (_, row) in enumerate(gdf.iterrows(), start=1):
            pid = None if _is_null(row.get("point_id")) else str(row["point_id"]).strip()
            label = f"{p.name}, {pid if pid else f'row {i}'}"
            parsed = _check_row(row, label, now, inside, problems, root)
            if pid:
                if pid in seen_ids:
                    problems.append(
                        f"{label}: point_id is not unique (already used in {seen_ids[pid]})"
                    )
                    continue
                seen_ids[pid] = p.name
            if parsed is None:
                continue
            key = (shapely.to_wkb(parsed["geometry"]), parsed["observed_at"])
            if key in seen_geo:
                problems.append(
                    f"{label}: same location and time as {seen_geo[key]}; remove the duplicate"
                )
                continue
            seen_geo[key] = label
            rows.append(
                {
                    **row.drop(labels=["geometry", *PHOTO_FIELDS], errors="ignore").to_dict(),
                    **parsed,
                    "source_file": source,
                    "private": private,
                }
            )

    if problems:
        raise FieldDataError(problems)

    cols = [
        *REQUIRED_FIELDS,
        "secondary_class",
        "patch_id",
        "position_source",
        "notes",
    ]
    handled = {"geometry", "pct", "yrs", "gps_time", "acc", "photos", "observed_at"}
    handled |= {"source_file", "private", OPENNESS_COLUMN, "opened_years_ago", "gps_accuracy_m"}
    handled |= {"gps_time_utc"}
    extras = sorted({k for r in rows for k in r} - set(cols) - handled)
    data: dict[str, Any] = {}
    for c in cols + extras:
        if c == "observed_at":
            continue
        data[c] = [None if _is_null(r.get(c)) else r.get(c) for r in rows]
    out = gpd.GeoDataFrame(data, geometry=[r["geometry"] for r in rows], crs=AOI_CRS)
    out["observed_at"] = pd.to_datetime([r["observed_at"] for r in rows], utc=True)
    out["canopy_open_pct"] = pd.array([r["pct"] for r in rows], dtype="Int16")
    out["gps_accuracy_m"] = pd.array([r["acc"] for r in rows], dtype="Float32")
    out["photos"] = [r["photos"] for r in rows]
    out["opened_years_ago"] = pd.array([r["yrs"] for r in rows], dtype="Int16")
    out["gps_time_utc"] = pd.to_datetime([r["gps_time"] for r in rows], utc=True)
    out["source_file"] = [r["source_file"] for r in rows]
    out["private"] = pd.array([r["private"] for r in rows], dtype=bool)
    for c, cats in (
        ("class", FIELD_CLASSES),
        ("secondary_class", FIELD_CLASSES),
        ("opened", OPENED_VALUES),
        ("openness_method", OPENNESS_METHODS),
    ):
        out[c] = pd.Categorical(out[c], categories=list(cats))
    for c in ("class", "opened", "openness_method"):
        if out[c].isna().any():  # safety net: a required code must never become NaN silently
            raise FieldDataError([f"'{c}' has values that could not be matched to the vocabulary"])
    return out


def main(argv: list[str] | None = None) -> int:
    """Validate field GeoJSON files from the command line; return exit code 0 (OK) or 1."""
    ap = argparse.ArgumentParser(prog="python -m highland.io", description=__doc__)
    ap.add_argument(
        "paths", nargs="*", help="field GeoJSON files or folders (default: data/field/)"
    )
    ap.add_argument("--photo-root", default=None, help="folder the photo paths are relative to")
    args = ap.parse_args(argv)
    try:
        pts = load_field_points(args.paths or None, photo_root=args.photo_root)
    except FieldDataError as exc:
        print(f"ERROR: {exc}")
        return 1
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        return 1
    print(f"{len(pts)} valid point(s) from {pts['source_file'].nunique()} file(s)")
    print("by class:")
    for k, v in pts["class"].value_counts().items():
        if v:
            print(f"  {k}: {v}")
    print("opened:")
    for k, v in pts["opened"].value_counts().items():
        if v:
            print(f"  {k}: {v}")
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
