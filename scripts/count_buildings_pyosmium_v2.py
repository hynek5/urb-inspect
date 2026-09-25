#!/usr/bin/env python3
"""Count buildings inside a boundary, offline, from a local .osm.pbf.

No server, no database, no network. Get an extract from
https://download.geofabrik.de/ (a city extract is plenty for one quarter).

Find the boundary:
    python count_buildings_pyosmium.py praha.osm.pbf --find "Malá Strana"

Count (boundary = OSM relation):
    python count_buildings_pyosmium.py praha.osm.pbf --relation-id 433401
    python count_buildings_pyosmium.py praha.osm.pbf --relation-id 433401 --courtyards

Count (boundary = your own polygon, e.g. Westminster / St James's):
    python count_buildings_pyosmium.py greater-london.osm.pbf --boundary-file st_james.geojson

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

from urb_inspect.export import write_result  # noqa: E402
from urb_inspect.sources.geojson_boundary import boundary_from_geojson  # noqa: E402
from urb_inspect.metrics import add_metrics, summarize  # noqa: E402
from urb_inspect.sources import PbfSource  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("pbf", help="path to .osm.pbf extract")
    ap.add_argument("--find", metavar="NAME", help="list boundary relations with this name")
    ap.add_argument("--relation-id", type=int, help="OSM relation id of the boundary")
    ap.add_argument("--boundary-file", metavar="GEOJSON",
                    help="use a local GeoJSON polygon (WGS84) as the boundary instead of a relation")
    ap.add_argument("--out-dir", default="out", metavar="DIR",
                    help="where to write .gpkg/.csv/.meta.json (default: out)")
    ap.add_argument("--no-write", action="store_true", help="print only, write nothing")
    ap.add_argument("--min-courtyard-m2", type=float, default=0.0, metavar="M2",
                    help="ignore courtyards below this area when summarising; "
                         "half of them are lightwells under 50 m2 (default: 0, count all)")
    ap.add_argument("--courtyard-bin-m2", type=float, default=25.0, metavar="M2",
                    help="histogram bin width for courtyard sizes (default: 25)")
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

    if bool(args.relation_id) == bool(args.boundary_file):
        ap.error("give exactly one of --find NAME, --relation-id ID, --boundary-file GEOJSON")

    print(f"Extract snapshot: {src.snapshot}", file=sys.stderr)
    try:
        if args.boundary_file:
            print(f"Loading boundary from {args.boundary_file} ...", file=sys.stderr)
            boundary = boundary_from_geojson(args.boundary_file)
        else:
            print(f"Assembling boundary for relation {args.relation_id} ...", file=sys.stderr)
            boundary = src.boundary(args.relation_id)
    except LookupError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print("Counting buildings ...", file=sys.stderr)
    within = src.fetch(boundary, {"building": True}, predicate="within")
    touching = src.fetch(boundary, {"building": True}, predicate="intersects")

    enriched = add_metrics(within.features)

    print()
    print(within.report())
    print(f"      touching boundary        : {len(touching)}")
    print(f"      straddling the boundary  : {len(touching) - len(within)}")
    print()
    print(summarize(enriched, min_courtyard_m2=args.min_courtyard_m2,
                    courtyard_bin_m2=args.courtyard_bin_m2))

    if not args.no_write:
        print()
        for path in write_result(within, args.out_dir, features=enriched,
                                 min_courtyard_m2=args.min_courtyard_m2,
                                 courtyard_bin_m2=args.courtyard_bin_m2):
            print(f"  wrote {path}")

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
