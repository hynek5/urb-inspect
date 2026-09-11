#!/usr/bin/env python3
"""Count `building=*` features inside a named area, using OSMnx.

Needs internet: OSMnx is a client for two remote services.
  - Nominatim  (nominatim.openstreetmap.org)  -> resolves the name to a polygon
  - Overpass   (overpass-api.de)              -> returns the features

There is no offline mode. OSMnx cannot read a local .osm.pbf.

Usage:
    python count_buildings_osmnx.py "Malá Strana, Praha, Czechia"
    python count_buildings_osmnx.py "Malá Strana, Praha, Czechia" --which-result 2
    python count_buildings_osmnx.py --relation-id 123456      # exact, no name guessing

Requires: pip install osmnx
"""

from __future__ import annotations

import argparse
import sys

import osmnx as ox

NON_BUILDING = {"no", "none"}


def resolve_boundary(query: str | None, relation_id: int | None, which_result: int | None):
    """Nominatim lookup -> boundary polygon.

    Using an explicit OSM relation id is the reproducible option: names are
    ambiguous and Nominatim's ranking can change between runs.
    """
    if relation_id is not None:
        gdf = ox.geocode_to_gdf(f"R{relation_id}", by_osmid=True)
    else:
        gdf = ox.geocode_to_gdf(query, which_result=which_result)

    row = gdf.iloc[0]
    geom = row.geometry
    print(f"  matched      : {row.get('display_name', '?')}", file=sys.stderr)
    print(f"  osm id       : {row.get('osm_type', '?')}/{row.get('osm_id', '?')}", file=sys.stderr)
    print(f"  geometry     : {geom.geom_type}", file=sys.stderr)

    if geom.geom_type not in ("Polygon", "MultiPolygon"):
        raise SystemExit(
            f"Nominatim returned a {geom.geom_type}, not an area. "
            "Try --which-result 2 (or 3), or pass --relation-id."
        )
    return geom


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="?", help='e.g. "Malá Strana, Praha, Czechia"')
    ap.add_argument("--relation-id", type=int, help="OSM relation id (skips name lookup)")
    ap.add_argument("--which-result", type=int, default=None,
                    help="pick the Nth Nominatim result instead of the first")
    args = ap.parse_args()

    if not args.query and args.relation_id is None:
        ap.error("give a query string or --relation-id")

    ox.settings.log_console = False

    print("Resolving boundary via Nominatim ...", file=sys.stderr)
    boundary = resolve_boundary(args.query, args.relation_id, args.which_result)

    print("Querying Overpass for buildings ...", file=sys.stderr)
    gdf = ox.features_from_polygon(boundary, tags={"building": True})

    if gdf.empty:
        print("\n  buildings: 0")
        return 0

    # Overpass returns anything carrying a building tag, including building=no.
    mask = ~gdf["building"].astype(str).str.lower().isin(NON_BUILDING)
    gdf = gdf[mask]

    # features_from_polygon already clips to the polygon, but element types are
    # mixed: a building can come back as a way or as a multipolygon relation.
    by_type = gdf.index.get_level_values("element").value_counts().to_dict()

    print()
    print(f"  buildings returned : {len(gdf)}")
    for elem, n in sorted(by_type.items()):
        print(f"      as {elem:10s}   : {n}")
    print()
    print("  top building values:")
    for val, n in gdf["building"].value_counts().head(8).items():
        print(f"      {val:20s} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
