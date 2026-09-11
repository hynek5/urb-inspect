#!/usr/bin/env python3
"""Count buildings inside a boundary, offline, from a local .osm.pbf.

No server, no database, no network. Get an extract from
https://download.geofabrik.de/ (a city extract is plenty for one quarter).

Find the boundary:
    python count_buildings_pyosmium.py praha.osm.pbf --find "Malá Strana"

Count:
    python count_buildings_pyosmium.py praha.osm.pbf --relation-id 433401
    python count_buildings_pyosmium.py praha.osm.pbf --relation-id 433401 --courtyards

Known boundaries (pin the id, not the name -- tagging gets reorganised, ids do not):
    433401  Malá Strana / Lesser Town, Praha
            boundary=cadastral, official_status=cz:katastrální území, ref=727091

Requires: pip install osmium shapely geopandas
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from urb_inspect.metrics import add_metrics, summarize  # noqa: E402
from urb_inspect.sources import PbfSource  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("pbf", help="path to .osm.pbf extract")
    ap.add_argument("--find", metavar="NAME", help="list boundary relations with this name")
    ap.add_argument("--relation-id", type=int, help="OSM relation id of the boundary")
    ap.add_argument("--courtyards", action="store_true",
                    help="also compute courtyards and footprint areas")
    ap.add_argument("--check-unclosed", action="store_true",
                    help="extra pass: report building ways that are not closed rings")
    args = ap.parse_args()

    src = PbfSource(args.pbf)

    if args.find:
        cands = src.find_boundaries(args.find)
        if not cands:
            print(f"No boundary relation named {args.find!r} found.", file=sys.stderr)
            return 1
        print(f"{len(cands)} candidate(s) named {args.find!r}:\n")
        for rid, tags in cands:
            print(f"  relation {rid}   https://www.openstreetmap.org/relation/{rid}")
            for k in ("boundary", "border_type", "official_status", "admin_level",
                      "type", "place", "ref", "ref:ruian", "source", "name:en", "wikidata"):
                if k in tags:
                    print(f"      {k:15s} = {tags[k]}")
            print()
        print("Re-run with --relation-id <id> to count buildings.")
        return 0

    if not args.relation_id:
        ap.error("give either --find NAME or --relation-id ID")

    print(f"Extract snapshot: {src.snapshot}", file=sys.stderr)
    print(f"Assembling boundary for relation {args.relation_id} ...", file=sys.stderr)
    try:
        boundary = src.boundary(args.relation_id)
    except LookupError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print("Counting buildings ...", file=sys.stderr)
    within = src.fetch(boundary, {"building": True}, predicate="within")
    touching = src.fetch(boundary, {"building": True}, predicate="intersects")

    print()
    print(within.report())
    print(f"  touching boundary          : {len(touching)}")
    print(f"  straddling the boundary    : {len(touching) - len(within)}")

    if args.courtyards:
        enriched = add_metrics(within.features)
        print()
        print(summarize(enriched))

    if args.check_unclosed:
        print()
        print("Checking for unclosed building ways ...", file=sys.stderr)
        broken = src.unclosed_ways(boundary)
        if not broken:
            print("  unclosed building ways: none (all rings are valid)")
        else:
            print(f"  unclosed building ways: {len(broken)} (not counted above)")
            for wid in broken[:20]:
                print(f"      https://www.openstreetmap.org/way/{wid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
