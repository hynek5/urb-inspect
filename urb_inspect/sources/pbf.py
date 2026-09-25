"""Offline feature source: reads a local .osm.pbf extract with pyosmium.

No server, no database, no network. Extracts come from
https://download.geofabrik.de/.

This is the primary source. Area assembly is done by libosmium -- the same
code that runs OpenStreetMap's own infrastructure -- so closed ways and
multipolygon relations are handled correctly by construction rather than by
filtering geometries after the fact.
"""

from __future__ import annotations

from typing import Iterator, Sequence

import osmium
import geopandas as gpd
from shapely.geometry import Polygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.prepared import prep
from shapely.strtree import STRtree

from .base import (
    DEFAULT_POI_KEYS,
    NON_BUILDING,
    OSM_CRS,
    Boundary,
    FetchResult,
    Predicate,
    empty_frame,
    first_poi_key,
    tags_match,
)


class PbfSource:
    """Feature source backed by a local PBF extract."""

    name = "pbf/pyosmium"

    def __init__(self, path: str):
        self.path = str(path)
        self._snapshot: str | None = None

    @property
    def snapshot(self) -> str:
        """When the extract was cut, from the file header."""
        if self._snapshot is None:
            header = osmium.FileProcessor(self.path, osmium.osm.NOTHING).header
            self._snapshot = header.get("osmosis_replication_timestamp", "") or "unknown"
        return self._snapshot

    # ---------------------------------------------------------------- boundaries

    def find_boundaries(self, name: str) -> list[tuple[int, dict[str, str]]]:
        """Relations matching `name` that look like a boundary.

        Returned rather than auto-selected: a name can match several features
        (a cadastral area and a suburb are not the same polygon), and picking
        silently would hide that choice.
        """
        found = []
        for obj in osmium.FileProcessor(self.path, osmium.osm.RELATION):
            tags = obj.tags
            if tags.get("name") != name:
                continue
            if "boundary" in tags or tags.get("type") == "boundary":
                found.append((obj.id, dict(tags)))
        return found

    def boundary(self, relation_id: int) -> Boundary:
        """Assemble a boundary relation into a polygon."""
        fp = (
            osmium.FileProcessor(self.path)
            .with_areas(osmium.filter.KeyFilter("boundary"))
            .with_filter(osmium.filter.EntityFilter(osmium.osm.AREA))
            .with_filter(osmium.filter.GeoInterfaceFilter())
        )
        for obj in fp:
            # Area ids are derived (way*2, relation*2+1); orig_id undoes that.
            if not obj.from_way() and obj.orig_id() == relation_id:
                if not hasattr(obj, "__geo_interface__"):
                    raise LookupError(
                        f"relation {relation_id} assembled, but no geometry could be "
                        "built from it (self-intersecting or malformed rings)."
                    )
                return Boundary(
                    geometry=shape(obj.__geo_interface__),
                    osm_relation_id=relation_id,
                    name=obj.tags.get("name"),
                    provenance=f"pbf:{self.path}",
                )
        raise LookupError(
            f"relation {relation_id} was not assembled into an area. Either it is "
            "outside this extract's coverage, it is not tagged type=multipolygon "
            "or type=boundary, or its rings do not close."
        )

    # ---------------------------------------------------------------- features

    def fetch(
        self,
        boundary: Boundary,
        tags: dict[str, object],
        predicate: Predicate = "within",
    ) -> FetchResult:
        keys = list(tags)
        inside = prep(boundary.geometry)
        rows: list[dict] = []
        discarded = {
            "outside boundary": 0,
            "building=no": 0,
            "invalid geometry repaired": 0,
            "geometry not buildable": 0,
        }
        self.unbuildable: list[tuple[str, int]] = []

        fp = (
            osmium.FileProcessor(self.path)
            .with_areas(osmium.filter.KeyFilter(*keys))
            .with_filter(osmium.filter.EntityFilter(osmium.osm.AREA))
            .with_filter(osmium.filter.KeyFilter(*keys))
            .with_filter(osmium.filter.GeoInterfaceFilter())
        )

        for obj in fp:
            obj_tags = dict(obj.tags)
            if not tags_match(obj_tags, tags):
                continue
            if obj_tags.get("building", "").lower() in NON_BUILDING:
                discarded["building=no"] += 1
                continue

            # GeoInterfaceFilter passes objects it could not build a geometry
            # for (e.g. self-intersecting rings) *without* the attribute,
            # rather than dropping them. Count them; do not crash on them.
            gi = getattr(obj, "__geo_interface__", None)
            if gi is None:
                discarded["geometry not buildable"] += 1
                self.unbuildable.append(
                    ("way" if obj.from_way() else "relation", obj.orig_id())
                )
                continue
            geom = shape(gi)
            if not geom.is_valid:
                geom = geom.buffer(0)
                discarded["invalid geometry repaired"] += 1

            if predicate == "within":
                # representative_point() always lies inside the polygon, unlike
                # centroid() for concave footprints (courtyards, L-shaped blocks).
                hit = inside.contains(geom.representative_point())
            else:
                hit = inside.intersects(geom)
            if not hit:
                discarded["outside boundary"] += 1
                continue

            rows.append(
                {
                    **obj_tags,
                    "element": "way" if obj.from_way() else "relation",
                    "osm_id": obj.orig_id(),
                    "geometry": geom,
                }
            )

        rows, duplicated = _drop_ways_duplicated_by_relations(rows)
        discarded["way duplicated by relation"] = duplicated

        features = (
            gpd.GeoDataFrame(rows, geometry="geometry", crs=OSM_CRS)
            if rows
            else empty_frame()
        )
        return FetchResult(
            features=features,
            boundary=boundary,
            snapshot=self.snapshot,
            predicate=predicate,
            source=self.name,
            discarded=discarded,
        )

    # ---------------------------------------------------------------- diagnostics

    def unclosed_ways(self, boundary: Boundary, key: str = "building") -> list[int]:
        """Ways tagged `key` that are not closed rings.

        These are errors in the source data. The area assembler skips them, so
        without this pass they leave the count with no trace.
        """
        touching = prep(boundary.geometry)
        broken: list[int] = []
        fp = (
            osmium.FileProcessor(self.path, osmium.osm.WAY)
            .with_locations()
            .with_filter(osmium.filter.KeyFilter(key))
            .with_filter(osmium.filter.GeoInterfaceFilter())
        )
        for obj in fp:
            if obj.tags.get(key, "").lower() in NON_BUILDING:
                continue
            if obj.is_closed():
                continue
            gi = getattr(obj, "__geo_interface__", None)
            if gi is not None and touching.intersects(shape(gi)):
                broken.append(obj.id)
        return broken

    # ---------------------------------------------------------------- POIs

    def fetch_pois(
        self,
        boundary: Boundary,
        keys: Sequence[str] = DEFAULT_POI_KEYS,
        predicate: Predicate = "within",
    ) -> FetchResult:
        """Services and points of interest inside the boundary.

        Separate from fetch() because POIs live on different element types.
        fetch() assembles areas only, which is right for buildings but misses
        most shops and cafes: in OSM those are overwhelmingly standalone nodes.
        This runs a node pass and an area pass and merges them.

        A single real-world service can carry several of `keys` at once -- a
        surgery tagged both amenity=doctors and healthcare=doctor, a building
        that is also a shop. It is one service, so the first key in `keys`
        order wins and the object is emitted once.
        """
        keys = tuple(keys)
        inside = prep(boundary.geometry)
        rows: list[dict] = []
        seen: set[tuple[str, int]] = set()
        discarded = {
            "outside boundary": 0,
            "no matching key": 0,
            "duplicate (node+area)": 0,
            "explicit absence (key=no)": 0,
            "geometry not buildable": 0,
        }

        for element, osm_id, obj_tags, geom in self._poi_candidates(keys):
            poi_key, poi_value = first_poi_key(obj_tags, keys)
            if poi_key is None:
                # KeyFilter passed the object (it has the key) but every value
                # is an explicit absence, e.g. shop=no on a former shop.
                if any(k in obj_tags for k in keys):
                    discarded["explicit absence (key=no)"] += 1
                else:
                    discarded["no matching key"] += 1
                continue

            if geom is None:
                discarded["geometry not buildable"] += 1
                continue

            # representative_point() is the point itself for a node, and a
            # point guaranteed inside the ring for an area.
            hit = (
                inside.contains(geom.representative_point())
                if predicate == "within"
                else inside.intersects(geom)
            )
            if not hit:
                discarded["outside boundary"] += 1
                continue

            ident = (element, osm_id)
            if ident in seen:
                discarded["duplicate (node+area)"] += 1
                continue
            seen.add(ident)

            rows.append(
                {
                    **obj_tags,
                    "element": element,
                    "osm_id": osm_id,
                    "poi_key": poi_key,
                    "poi_value": poi_value,
                    "geometry": geom,
                }
            )

        rows, duplicated = _drop_ways_duplicated_by_relations(rows)
        discarded["way duplicated by relation"] = duplicated

        features = (
            gpd.GeoDataFrame(rows, geometry="geometry", crs=OSM_CRS)
            if rows
            else empty_frame()
        )
        return FetchResult(
            features=features,
            boundary=boundary,
            snapshot=self.snapshot,
            predicate=predicate,
            source=self.name,
            discarded=discarded,
        )

    def _poi_candidates(
        self, keys: Sequence[str]
    ) -> Iterator[tuple[str, int, dict[str, str], BaseGeometry | None]]:
        """Yield (element, osm_id, tags, geometry) from the node then area pass.

        Geometry is None when libosmium could not build one; the caller counts
        those rather than dropping them silently.
        """
        nodes = (
            osmium.FileProcessor(self.path, osmium.osm.NODE)
            .with_filter(osmium.filter.KeyFilter(*keys))
            .with_filter(osmium.filter.GeoInterfaceFilter())
        )
        for obj in nodes:
            yield "node", obj.id, dict(obj.tags), _geometry_of(obj)

        areas = (
            osmium.FileProcessor(self.path)
            .with_areas(osmium.filter.KeyFilter(*keys))
            .with_filter(osmium.filter.EntityFilter(osmium.osm.AREA))
            .with_filter(osmium.filter.KeyFilter(*keys))
            .with_filter(osmium.filter.GeoInterfaceFilter())
        )
        for obj in areas:
            element = "way" if obj.from_way() else "relation"
            yield element, obj.orig_id(), dict(obj.tags), _geometry_of(obj)


def _solid(geom: BaseGeometry) -> BaseGeometry | None:
    """The outline with holes filled in.

    A way that duplicates a relation *is* that relation's outer ring, so the
    two match on their exteriors even though their areas differ by whatever
    the courtyard takes up. Comparing filled outlines is therefore exact where
    comparing areas would miss any building with a sizeable courtyard.
    """
    parts = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
    solids = [Polygon(p.exterior) for p in parts if p.geom_type == "Polygon"]
    if not solids:
        return None
    return solids[0] if len(solids) == 1 else unary_union(solids)


def _drop_ways_duplicated_by_relations(
    rows: list[dict], overlap: float = 0.99
) -> tuple[list[dict], int]:
    """Remove way-areas that are the outer ring of a relation-area in `rows`.

    Tagging both the outer way and its multipolygon relation is a mapping
    error -- a leftover of the pre-2010 convention that put tags on the way --
    but it produces two areas for one building, with different geometry: the
    way has no courtyard, the relation does.

    Containment alone would be wrong: a small building standing inside a
    courtyard is legitimately separate, and it is not caught here because the
    courtyard is a hole, so it lies outside the relation's polygon. Matching
    filled outlines both ways only ever catches the same object twice over.
    """
    rel_solids = [
        _solid(r["geometry"]) for r in rows if r["element"] == "relation"
    ]
    rel_solids = [g for g in rel_solids if g is not None and not g.is_empty]
    if not rel_solids:
        return rows, 0

    tree = STRtree(rel_solids)
    kept, dropped = [], 0
    for row in rows:
        if row["element"] != "relation":
            way_solid = _solid(row["geometry"])
            if way_solid is not None and not way_solid.is_empty:
                duplicate = False
                for idx in tree.query(way_solid):
                    rel_solid = rel_solids[idx]
                    inter = way_solid.intersection(rel_solid).area
                    if (inter >= overlap * way_solid.area
                            and inter >= overlap * rel_solid.area):
                        duplicate = True
                        break
                if duplicate:
                    dropped += 1
                    continue
        kept.append(row)
    return kept, dropped


def _geometry_of(obj) -> BaseGeometry | None:
    """Shapely geometry, or None when libosmium could not build one.

    GeoInterfaceFilter passes such objects through *without* the attribute
    rather than dropping them, so this must not assume it is present.
    """
    gi = getattr(obj, "__geo_interface__", None)
    if gi is None:
        return None
    geom = shape(gi)
    if not geom.is_valid:
        geom = geom.buffer(0)
    return geom
