#!/usr/bin/env python3
"""Count buildings inside a boundary via Nominatim + Overpass (OSMnx).

Requires network. There is no offline mode -- OSMnx is a client for remote
services and cannot read a .osm.pbf. Use count_buildings_pyosmium.py for
systematic work; this is for geocoding and for an independent cross-check.

    python count_buildings_osmnx.py --relation-id 433401
    python count_buildings_osmnx.py "Malá Strana, Praha, Czechia"

Requires: pip install osmnx
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from urb_inspect.metrics import add_metrics, summarize  # noqa: E402
from urb_inspect.sources import OverpassSource  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("query", nargs="?", help='e.g. "Malá Strana, Praha, Czechia"')
    ap.add_argument("--relation-id", type=int, help="OSM relation id (skips name lookup)")
    ap.add_argument("--which-result", type=int, default=None,
                    help="pick the Nth Nominatim result instead of the first")
    ap.add_argument("--courtyards", action="store_true",
                    help="also compute courtyards and footprint areas")
    args = ap.parse_args()

    if not args.query and args.relation_id is None:
        ap.error("give a query string or --relation-id")

    src = OverpassSource()

    print("Resolving boundary via Nominatim ...", file=sys.stderr)
    try:
        boundary = src.boundary(
            query=args.query,
            relation_id=args.relation_id,
            which_result=args.which_result,
        )
    except LookupError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print("Querying Overpass for buildings ...", file=sys.stderr)
    result = src.fetch(boundary, {"building": True})

    print()
    print(result.report())

    if args.courtyards:
        enriched = add_metrics(result.features)
        print()
        print(summarize(enriched))
    else:
        print()
        print(summarize(result.features))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
