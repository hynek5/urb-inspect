#!/usr/bin/env python3
"""Count and classify services inside a boundary, offline, from a local .osm.pbf.

No server, no database, no network. Get an extract from
https://download.geofabrik.de/.

Unlike buildings, services in OSM are mostly standalone nodes rather than
areas, so this runs a node pass as well as an area pass and merges them.

Boundary from an OSM relation:
    python count_pois_pyosmium.py praha.osm.pbf --relation-id 433401

Boundary from your own polygon:
    python count_pois_pyosmium.py bratislavsky.pbf --boundary-file bratislava_polygon.geojson

Every service is labelled with a category (what it is) and an audience
(everyday / tourist / mixed), so a quarter's everyday provision can be
compared against its tourist provision. The mapping lives in
urb_inspect/data/poi_classes.csv; pairs it does not cover are reported
rather than dropped, and the table is meant to be extended from that report.

Requires: pip install osmium shapely geopandas
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from urb_inspect.export import slugify, write_result  # noqa: E402
from urb_inspect.metrics import (  # noqa: E402
    add_poi_metrics,
    summarize_pois,
    summarize_value_counts,
)
from urb_inspect.poi_classes import DEFAULT_CLASSES  # noqa: E402
from urb_inspect.sources import PbfSource  # noqa: E402
from urb_inspect.sources.base import DEFAULT_POI_KEYS  # noqa: E402
from urb_inspect.sources.geojson_boundary import boundary_from_geojson  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("pbf", help="path to .osm.pbf extract")
    ap.add_argument("--relation-id", type=int, help="OSM relation id of the boundary")
    ap.add_argument("--boundary-file", metavar="GEOJSON",
                    help="use a local GeoJSON polygon (WGS84) as the boundary")
    ap.add_argument("--keys", default=",".join(DEFAULT_POI_KEYS), metavar="LIST",
                    help=f"comma-separated tag keys to treat as services "
                         f"(default: {','.join(DEFAULT_POI_KEYS)})")
    ap.add_argument("--classes", metavar="CSV", default=str(DEFAULT_CLASSES),
                    help="classification table (default: the bundled poi_classes.csv)")
    ap.add_argument("--top", type=int, default=20, metavar="N",
                    help="how many values to list per tag key; 0 for all (default: 20)")
    ap.add_argument("--counts-only", action="store_true",
                    help="just the value tallies, skip the everyday/tourist breakdown")
    ap.add_argument("--out-dir", default="out", metavar="DIR",
                    help="where to write .gpkg/.csv/.meta.json (default: out)")
    ap.add_argument("--no-write", action="store_true", help="print only, write nothing")
    args = ap.parse_args()

    if bool(args.relation_id) == bool(args.boundary_file):
        ap.error("give exactly one of --relation-id ID or --boundary-file GEOJSON")

    keys = tuple(k.strip() for k in args.keys.split(",") if k.strip())
    if not keys:
        ap.error("--keys must name at least one tag key")

    src = PbfSource(args.pbf)
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

    print(f"Scanning nodes and areas for {', '.join(keys)} ...", file=sys.stderr)
    result = src.fetch_pois(boundary, keys=keys, predicate="within")
    enriched = add_poi_metrics(result.features, classes_path=args.classes)

    print()
    print(result.report())
    print()
    print("  occurrences by tag value")
    print("  " + "-" * 62)
    print(summarize_value_counts(enriched, top=args.top or None))
    if not args.counts_only:
        print()
        print(summarize_pois(enriched))

    if not args.no_write:
        rid = boundary.osm_relation_id
        stem = slugify(boundary.name, fallback="area")
        basename = f"{stem}-r{rid}-pois-pbf" if rid else f"{stem}-pois-pbf"
        print()
        for path in write_result(result, args.out_dir, basename=basename, features=enriched):
            print(f"  wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
