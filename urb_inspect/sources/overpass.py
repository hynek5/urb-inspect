"""Online feature source: Nominatim + Overpass via OSMnx.

Requires network; there is no offline mode. Kept for two jobs the offline
source cannot do:

  - geocoding: resolving a place name to a boundary (a PBF has no geocoder)
  - cross-validation: an independent second opinion on a count

Overpass is donated shared infrastructure and is rate limited. Do not use this
source to sweep large areas systematically -- that is what PbfSource is for.
"""

from __future__ import annotations

import geopandas as gpd

from .base import (
    NON_BUILDING,
    Boundary,
    FetchResult,
    Predicate,
    empty_frame,
)

AREAL = frozenset({"Polygon", "MultiPolygon"})


class OverpassSource:
    """Feature source backed by the public Overpass and Nominatim servers."""

    name = "overpass/osmnx"
    snapshot = "live"

    def __init__(self):
        import osmnx as ox  # imported lazily: the offline path must not need it

        self._ox = ox
        ox.settings.log_console = False

    # ---------------------------------------------------------------- boundaries

    def boundary(
        self,
        query: str | None = None,
        relation_id: int | None = None,
        which_result: int | None = None,
    ) -> Boundary:
        """Resolve a boundary by name, or exactly by OSM relation id.

        Prefer relation_id: names are ambiguous and Nominatim's ranking can
        change between releases, so a name is not a reproducible input.
        """
        if relation_id is not None:
            gdf = self._ox.geocode_to_gdf(f"R{relation_id}", by_osmid=True)
            provenance = f"nominatim:R{relation_id}"
        elif query is not None:
            gdf = self._ox.geocode_to_gdf(query, which_result=which_result)
            provenance = f"nominatim:{query!r}"
        else:
            raise ValueError("give either query or relation_id")

        row = gdf.iloc[0]
        if row.geometry.geom_type not in AREAL:
            raise LookupError(
                f"Nominatim returned a {row.geometry.geom_type}, not an area. "
                "Try which_result=2, or pass relation_id."
            )
        return Boundary(
            geometry=row.geometry,
            osm_relation_id=relation_id or _osm_id_if_relation(row),
            name=row.get("display_name"),
            provenance=provenance,
        )

    # ---------------------------------------------------------------- features

    def fetch(
        self,
        boundary: Boundary,
        tags: dict[str, object],
        predicate: Predicate = "intersects",
    ) -> FetchResult:
        gdf = self._ox.features_from_polygon(boundary.geometry, tags=tags)
        discarded = {"building=no": 0, "not an area": 0}

        if gdf.empty:
            return FetchResult(
                features=empty_frame(),
                boundary=boundary,
                snapshot=self.snapshot,
                predicate=predicate,
                source=self.name,
                discarded=discarded,
                query={"tags": dict(tags)},
            )

        if "building" in gdf.columns:
            keep = ~gdf["building"].astype(str).str.lower().isin(NON_BUILDING)
            discarded["building=no"] = int((~keep).sum())
            gdf = gdf[keep]

        # Overpass returns any way carrying the tag, closed or not. An unclosed
        # way becomes a LineString: a broken building in the source data, not a
        # building. PbfSource never sees these -- its assembler requires a ring.
        areal = gdf.geometry.geom_type.isin(AREAL)
        discarded["not an area"] = int((~areal).sum())
        gdf = gdf[areal]

        # OSMnx indexes by a MultiIndex of (element, id). Lift those into
        # columns and drop the index: writers call reset_index(drop=False)
        # internally, which collides when an index level and a column share a
        # name. PbfSource returns a RangeIndex, so this also makes the two
        # backends return identically shaped frames.
        gdf = gdf.copy()
        gdf["element"] = gdf.index.get_level_values("element")
        gdf["osm_id"] = gdf.index.get_level_values("id")
        gdf = gdf.reset_index(drop=True)

        return FetchResult(
            features=gdf,
            boundary=boundary,
            snapshot=self.snapshot,
            predicate=predicate,
            source=self.name,
            discarded=discarded,
            query={"tags": dict(tags)},
        )


def _osm_id_if_relation(row) -> int | None:
    if str(row.get("osm_type", "")).lower() == "relation":
        try:
            return int(row.get("osm_id"))
        except (TypeError, ValueError):
            return None
    return None
