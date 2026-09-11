#!/usr/bin/env python3
"""Count `building=*` features inside a named boundary, using a local .osm.pbf.

Fully offline. No server, no database, no network — pyosmium streams the file.

Get the extract from https://download.geofabrik.de/europe/czech-republic.html
(czech-republic-latest.osm.pbf).

Step 1 — find the boundary (prints every candidate with its tags, so you pick):

    python count_buildings_pyosmium.py czech-republic-latest.osm.pbf --find "Malá Strana"

Step 2 — count, using the relation id from step 1:

    python count_buildings_pyosmium.py czech-republic-latest.osm.pbf --relation-id 433401

Known boundaries (pin the id, not the name — tagging gets reorganised, ids do not):
    433401  Malá Strana / Lesser Town, Praha
            boundary=cadastral, official_status=cz:katastrální území, ref=727091
            https://www.openstreetmap.org/relation/433401

Requires: pip install osmium shapely
"""

from __future__ import annotations

import argparse
import sys

import osmium
from shapely.geometry import shape
from shapely.prepared import prep

# building=* values that do not denote an actual building
NON_BUILDING = {"no", "none"}


def snapshot_info(pbf: str) -> str:
    """When the extract was cut.

    A PBF is a fixed snapshot; Overpass is live. Without this, a difference
    between the two counts cannot be told apart from a bug.
    """
    header = osmium.FileProcessor(pbf, osmium.osm.NOTHING).header
    ts = header.get("osmosis_replication_timestamp", "")
    return ts or "unknown (no replication timestamp in header)"


def find_boundaries(pbf: str, name: str) -> list[tuple[int, dict]]:
    """Pass 1: every relation matching `name` that looks like a boundary.

    Printed rather than auto-selected: "Malá Strana" is not unique in Czechia,
    and the same place can exist as both a cadastral area and a suburb.
    """
    found = []
    for obj in osmium.FileProcessor(pbf, osmium.osm.RELATION):
        tags = obj.tags
        if tags.get("name") != name:
            continue
        if "boundary" in tags or tags.get("type") == "boundary":
            found.append((obj.id, dict(tags)))
    return found


def build_polygon(pbf: str, relation_id: int):
    """Pass 2: assemble the boundary relation into a polygon."""
    fp = (
        osmium.FileProcessor(pbf)
        .with_areas(osmium.filter.KeyFilter("boundary"))
        .with_filter(osmium.filter.EntityFilter(osmium.osm.AREA))
        .with_filter(osmium.filter.GeoInterfaceFilter())
    )
    for obj in fp:
        # Area ids are derived: way-based areas are id*2, relation-based id*2+1.
        # orig_id() gives back the original OSM id.
        if not obj.from_way() and obj.orig_id() == relation_id:
            return shape(obj.__geo_interface__)
    return None


def count_buildings(pbf: str, boundary) -> dict[str, int]:
    """Pass 3: count building areas against the boundary.

    `with_areas` yields one Area per closed way AND per multipolygon relation,
    so buildings mapped either way are counted exactly once.
    """
    inside = prep(boundary)
    touching = prep(boundary)  # separate prepared geom; predicates differ below
    stats = {"within": 0, "intersects": 0, "from_way": 0, "from_relation": 0}

    fp = (
        osmium.FileProcessor(pbf)
        .with_areas(osmium.filter.KeyFilter("building"))
        .with_filter(osmium.filter.EntityFilter(osmium.osm.AREA))
        .with_filter(osmium.filter.KeyFilter("building"))
        .with_filter(osmium.filter.GeoInterfaceFilter())
    )

    for obj in fp:
        if obj.tags.get("building", "").lower() in NON_BUILDING:
            continue
        geom = shape(obj.__geo_interface__)
        if not geom.is_valid:
            geom = geom.buffer(0)

        if not touching.intersects(geom):
            continue
        stats["intersects"] += 1

        # representative_point() is guaranteed to lie inside the polygon,
        # unlike centroid() for concave shapes (courtyards, L-shaped blocks).
        if inside.contains(geom.representative_point()):
            stats["within"] += 1
            stats["from_way" if obj.from_way() else "from_relation"] += 1

    return stats


def find_unclosed_ways(pbf: str, boundary, limit: int = 20) -> list[int]:
    """Ways tagged building=* that are NOT closed rings.

    These are data errors: a building must be a closed way or a multipolygon.
    The area assembler silently skips them, so without this pass they vanish
    from the count with no indication that anything was dropped.
    """
    touching = prep(boundary)
    broken: list[int] = []
    fp = (
        osmium.FileProcessor(pbf, osmium.osm.WAY)
        .with_locations()
        .with_filter(osmium.filter.KeyFilter("building"))
        .with_filter(osmium.filter.GeoInterfaceFilter())
    )
    for obj in fp:
        if obj.tags.get("building", "").lower() in NON_BUILDING:
            continue
        if obj.is_closed():
            continue  # already counted as an area
        geom = shape(obj.__geo_interface__)
        if touching.intersects(geom):
            broken.append(obj.id)
            if len(broken) >= limit:
                break
    return broken


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pbf", help="path to .osm.pbf extract")
    ap.add_argument("--find", metavar="NAME", help="list boundary relations with this name")
    ap.add_argument("--relation-id", type=int, help="OSM relation id of the boundary")
    ap.add_argument("--check-unclosed", action="store_true",
                    help="extra pass: report building ways that are not closed rings "
                         "(data errors the area assembler skips silently)")
    args = ap.parse_args()

    if args.find:
        cands = find_boundaries(args.pbf, args.find)
        if not cands:
            print(f"No boundary relation named {args.find!r} found.", file=sys.stderr)
            return 1
        print(f"{len(cands)} candidate(s) named {args.find!r}:\n")
        for rid, tags in cands:
            print(f"  relation {rid}   https://www.openstreetmap.org/relation/{rid}")
            for k in ("boundary", "border_type", "official_status", "admin_level",
                      "type", "place", "ref", "ref:ruian", "source",
                      "name:en", "wikidata"):
                if k in tags:
                    print(f"      {k:12s} = {tags[k]}")
            print()
        print("Re-run with --relation-id <id> to count buildings.")
        return 0

    if not args.relation_id:
        ap.error("give either --find NAME or --relation-id ID")

    print(f"Extract snapshot: {snapshot_info(args.pbf)}", file=sys.stderr)
    print(f"Assembling boundary for relation {args.relation_id} ...", file=sys.stderr)
    boundary = build_polygon(args.pbf, args.relation_id)
    if boundary is None:
        print(
            f"Relation {args.relation_id} was not assembled into an area.\n"
            "  - Is the relation inside this extract's geographic coverage?\n"
            "  - osmium assembles relations tagged type=multipolygon or type=boundary;\n"
            "    check the relation carries one of those.\n"
            "  - A broken/unclosed boundary ring cannot be assembled at all.",
            file=sys.stderr,
        )
        return 1
    print(f"  boundary area: {boundary.area:.8f} deg^2, bounds {boundary.bounds}",
          file=sys.stderr)

    print("Counting buildings ...", file=sys.stderr)
    s = count_buildings(args.pbf, boundary)

    print()
    print(f"  buildings fully inside (by representative point) : {s['within']}")
    print(f"  buildings touching the boundary at all           : {s['intersects']}")
    print(f"      of the inside ones, mapped as closed ways    : {s['from_way']}")
    print(f"      of the inside ones, mapped as multipolygons  : {s['from_relation']}")

    if args.check_unclosed:
        print()
        print("Checking for unclosed building ways ...", file=sys.stderr)
        broken = find_unclosed_ways(args.pbf, boundary)
        if not broken:
            print("  unclosed building ways: none (all rings are valid)")
        else:
            print(f"  unclosed building ways: {len(broken)} (not counted above)")
            for wid in broken:
                print(f"      https://www.openstreetmap.org/way/{wid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
