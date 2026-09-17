"""Per-feature metrics derived from geometry and tags."""

from __future__ import annotations

import geopandas as gpd
from shapely.geometry.base import BaseGeometry

from .sources.base import OSM_CRS


def courtyard_count(geom: BaseGeometry) -> int:
    """Number of interior rings -- holes enclosed by the footprint.

    geom_type does not reveal these: a palace around a courtyard and a solid
    block are both `Polygon`. The difference is in `.interiors`.
    """
    if geom is None or geom.is_empty:
        return 0
    if geom.geom_type == "Polygon":
        return len(geom.interiors)
    if geom.geom_type == "MultiPolygon":
        return sum(len(p.interiors) for p in geom.geoms)
    return 0


# building=* values that mean "people live here". Used to say how many of the
# *residential* buildings carry a flat count -- the only denominator that
# matters for occupancy questions; a church without building:flats is not a gap.
RESIDENTIAL = frozenset({
    "residential", "apartments", "house", "detached", "semidetached_house",
    "terrace", "dormitory", "houseboat",
})


def parse_flats(value) -> int | None:
    """building:flats as an int, or None when absent or unparseable.

    OSM is free text: "12", "12;14", "~10", "12 flats" all occur. Anything that
    is not a plain non-negative integer is treated as missing rather than
    guessed -- a wrong count is worse than no count.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("nan", "none"):
        return None
    if text.isdigit():
        return int(text)
    return None


def add_metrics(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Attach derived columns: courtyards, footprint area in m^2, flats.

    Area is computed on an equal-area reprojection. Computing it on raw
    EPSG:4326 coordinates yields square degrees, which are not a unit of area
    and vary with latitude.
    """
    out = gdf.copy()
    out["courtyards"] = out.geometry.apply(courtyard_count)

    if len(out):
        metric = out.to_crs(out.estimate_utm_crs())
        out["area_m2"] = metric.geometry.area.round(1)
    else:
        out["area_m2"] = []

    # building:flats -> integer column `flats` (None = not tagged / unparseable)
    src = out["building:flats"] if "building:flats" in out.columns else None
    out["flats"] = (src.map(parse_flats) if src is not None else None)
    out["flats"] = out["flats"].astype("Int64")

    return out


def flats_summary(gdf: gpd.GeoDataFrame) -> dict:
    """Coverage of building:flats -- how much of the stock we can actually count.

    Returns counts, not prose, so export can write the same numbers to meta.json.
    """
    n = int(len(gdf))
    if n == 0 or "flats" not in gdf.columns:
        return {"buildings": n, "with_flats_tag": 0, "flats_sum": 0}
    has = gdf["flats"].notna()
    is_res = gdf["building"].astype(str).str.lower().isin(RESIDENTIAL) if "building" in gdf.columns else has & False
    out = {
        "buildings": n,
        "with_flats_tag": int(has.sum()),
        "flats_sum": int(gdf.loc[has, "flats"].sum()),
        "residential_buildings": int(is_res.sum()),
        "residential_with_flats_tag": int((has & is_res).sum()),
        "residential_flats_sum": int(gdf.loc[has & is_res, "flats"].sum()),
    }
    if "building:flats" in gdf.columns:
        raw_present = gdf["building:flats"].notna() & (gdf["building:flats"].astype(str).str.strip() != "")
        out["unparseable_flats_tag"] = int((raw_present & ~has).sum())
    return out


def summarize(gdf: gpd.GeoDataFrame, tag: str = "building", top: int = 10) -> str:
    """Human-readable breakdown of a feature set."""
    if gdf.empty:
        return "  (no features)"

    lines = []
    if tag in gdf.columns:
        lines.append(f"  by {tag}:")
        for value, n in gdf[tag].value_counts().head(top).items():
            lines.append(f"      {str(value):20s} {n}")

    if "courtyards" in gdf.columns:
        with_yards = int((gdf["courtyards"] > 0).sum())
        total = int(gdf["courtyards"].sum())
        pct = 100 * with_yards / len(gdf)
        lines.append("")
        lines.append(f"  with courtyards : {with_yards} ({pct:.1f}%), {total} in total")

    if "area_m2" in gdf.columns and len(gdf):
        lines.append(f"  footprint m^2   : median {gdf['area_m2'].median():.0f}, "
                     f"max {gdf['area_m2'].max():.0f}")

    if "flats" in gdf.columns and len(gdf):
        f = flats_summary(gdf)
        pct_all = 100 * f["with_flats_tag"] / f["buildings"]
        lines.append("")
        lines.append(f"  building:flats  : tagged on {f['with_flats_tag']} of {f['buildings']} "
                     f"buildings ({pct_all:.1f}%), {f['flats_sum']} flats in total")
        if f.get("residential_buildings"):
            pct_res = 100 * f["residential_with_flats_tag"] / f["residential_buildings"]
            lines.append(f"                    residential only: {f['residential_with_flats_tag']} of "
                         f"{f['residential_buildings']} ({pct_res:.1f}%), "
                         f"{f['residential_flats_sum']} flats")
        if f.get("unparseable_flats_tag"):
            lines.append(f"                    unparseable values ignored: {f['unparseable_flats_tag']}")
    return "\n".join(lines)
