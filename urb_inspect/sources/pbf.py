"""Offline feature source: reads a local .osm.pbf extract with pyosmium.

No server, no database, no network. Extracts come from
https://download.geofabrik.de/.

This is the primary source. Area assembly is done by libosmium -- the same
code that runs OpenStreetMap's own infrastructure -- so closed ways and
multipolygon relations are handled correctly by construction rather than by
filtering geometries after the fact.
"""

from __future__ import annotations

import osmium
import geopandas as gpd
from shapely.geometry import shape
from shapely.prepared import prep

from .base import (
    NON_BUILDING,
    OSM_CRS,
    Boundary,
    FetchResult,
    Predicate,
    empty_frame,
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
        }

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

            geom = shape(obj.__geo_interface__)
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
            if touching.intersects(shape(obj.__geo_interface__)):
                broken.append(obj.id)
        return broken
