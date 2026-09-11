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


def add_metrics(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Attach derived columns: courtyards and footprint area in m^2.

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
    return "\n".join(lines)
