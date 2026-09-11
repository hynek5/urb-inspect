# urb-inspect

Inspect OpenStreetMap objects — buildings and other features — within a given area.

## Data sources

Two interchangeable backends behind one interface. Both return a GeoPandas
`GeoDataFrame`, so nothing downstream depends on which one produced it.

| | `PbfSource` | `OverpassSource` |
|---|---|---|
| backend | pyosmium over a local `.osm.pbf` | Nominatim + Overpass via OSMnx |
| server needed | no — reads the file directly | no, but depends on public servers |
| network | none after download | every query |
| reproducible | yes — snapshot with a timestamp | no — live data |
| rate limits | none | yes, shared donated infrastructure |
| geocoding | no | yes |

`PbfSource` is the primary source. `OverpassSource` exists to resolve place
names to boundaries, and to act as an independent second opinion.

## Install

```bash
pip install -e ".[pbf]"          # offline only
pip install -e ".[all]"          # both backends
```

Extracts: <https://download.geofabrik.de/>

## Use

```bash
# find a boundary by name (prints every candidate with its tags)
python scripts/count_buildings_pyosmium.py praha.osm.pbf --find "Malá Strana"

# count, offline
python scripts/count_buildings_pyosmium.py praha.osm.pbf --relation-id 433401 --courtyards

# count, online — independent check
python scripts/count_buildings_osmnx.py --relation-id 433401
```

```python
from urb_inspect.sources import PbfSource
from urb_inspect.metrics import add_metrics

src = PbfSource("praha.osm.pbf")
result = src.fetch(src.boundary(433401), {"building": True})
gdf = add_metrics(result.features)
```

## What counts as a building

A feature is counted when it carries `building=*` **and** can be assembled
into a closed area — either a closed way, or a multipolygon relation whose
members form a ring. Member ways of a relation are not counted individually.

Excluded, and reported rather than dropped silently:

- `building=no` — a valid tag meaning "this is not a building"
- unclosed ways tagged `building=*` — errors in the source data
- nodes — a point has no footprint

Containment uses `representative_point()`, not `centroid()`: a centroid can
fall outside a concave footprint and land in the wrong area.

## Validation

Malá Strana, Praha (cadastral area, OSM relation
[433401](https://www.openstreetmap.org/relation/433401), RÚIAN ref 727091):

| method | rule | ways | relations | total |
|---|---|---|---|---|
| pyosmium | representative point within | 687 | 166 | **853** |
| OSMnx / Overpass | clipped to polygon | 688 | 166 | **854** |
| pyosmium | intersects | — | — | **855** |

Two independent implementations agree to within 0.23%, and the multipolygon
counts match exactly. The spread is 2 buildings straddling the cadastral
boundary — few, because Czech cadastral boundaries follow parcel lines, which
follow building walls.

PBF snapshot `2026-09-10T20:21:06Z`; Overpass live on 2026-09-11.
