"""Load a study-area boundary from a local GeoJSON file.

Use when the area is not an OSM relation (e.g. a hand-drawn polygon for
Westminster / St James's). Returns the same Boundary object that
PbfSource.boundary(relation_id) returns, so the rest of the pipeline
(fetch / add_metrics / write_result) is unchanged.

Expectations on the file:
  * WGS84 (EPSG:4326) -- the default of geojson.io and of a QGIS export
  * Polygon or MultiPolygon features; several features are unioned
"""

from __future__ import annotations

import json
from pathlib import Path

from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.validation import make_valid

from urb_inspect.sources import Boundary


def load_geojson_geometry(path: str | Path) -> tuple[BaseGeometry, str]:
    """Return (geometry, name) from a GeoJSON Feature / FeatureCollection / Geometry."""
    gj = json.loads(Path(path).read_text(encoding="utf-8"))
    kind = gj.get("type")
    if kind == "FeatureCollection":
        feats = gj["features"]
    elif kind == "Feature":
        feats = [gj]
    else:  # bare geometry
        feats = [{"type": "Feature", "geometry": gj, "properties": {}}]

    geoms = [shape(f["geometry"]) for f in feats if f.get("geometry")]
    if not geoms:
        raise LookupError(f"{path}: no geometry found")
    bad = [g.geom_type for g in geoms if g.geom_type not in ("Polygon", "MultiPolygon")]
    if bad:
        raise LookupError(f"{path}: boundary must be Polygon/MultiPolygon, got {bad}")

    geom = unary_union([make_valid(g) for g in geoms])
    minx, miny, maxx, maxy = geom.bounds
    if not (-180 <= minx <= maxx <= 180 and -90 <= miny <= maxy <= 90):
        raise LookupError(f"{path}: coordinates are not lon/lat (EPSG:4326): {geom.bounds}")

    name = (feats[0].get("properties") or {}).get("name") or Path(path).stem
    return geom, name


def boundary_from_geojson(path: str | Path) -> Boundary:
    geom, name = load_geojson_geometry(path)
    return Boundary(
        name=name,
        geometry=geom,
        osm_relation_id=None,
        provenance=f"geojson:{Path(path).resolve()}",
    )
