"""Courtyard area, and why it is restricted to residential buildings.

courtyard_count answers how many buildings enclose open space; this answers
how much. The distinction matters because a palace around a 900 m2 court and a
tenement around a 6 m2 lightwell both score 1 on the count.
"""

from __future__ import annotations

import json

import geopandas as gpd
import osmium
import pytest
from osmium.osm.mutable import Node, Relation, Way
from shapely.geometry import MultiPolygon, Polygon, box

from urb_inspect.export import write_result
from urb_inspect.metrics import add_metrics, courtyard_area, courtyard_summary
from urb_inspect.sources import PbfSource
from urb_inspect.sources.base import Boundary

SOLID = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
ONE_HOLE = Polygon(SOLID.exterior, [[(2, 2), (5, 2), (5, 5), (2, 5)]])          # 9
TWO_HOLES = Polygon(SOLID.exterior, [[(2, 2), (5, 2), (5, 5), (2, 5)],          # 9
                                     [(7, 7), (8, 7), (8, 8), (7, 8)]])         # 1


def test_area_sums_every_interior_ring():
    assert courtyard_area(ONE_HOLE) == pytest.approx(9.0)
    assert courtyard_area(TWO_HOLES) == pytest.approx(10.0)


def test_solid_building_has_no_courtyard():
    assert courtyard_area(SOLID) == 0.0


def test_multipolygon_sums_across_parts():
    other = Polygon([(20, 20), (30, 20), (30, 30), (20, 30)],
                    [[(22, 22), (24, 22), (24, 24), (22, 24)]])                 # 4
    assert courtyard_area(MultiPolygon([ONE_HOLE, other])) == pytest.approx(13.0)


def test_footprint_and_courtyard_do_not_overlap():
    """.area already excludes the holes, so the two columns can be added."""
    assert ONE_HOLE.area + courtyard_area(ONE_HOLE) == pytest.approx(SOLID.area)


@pytest.mark.parametrize("geom", [None, Polygon()])
def test_empty_geometry_is_zero(geom):
    assert courtyard_area(geom) == 0.0


def _frame(rows) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"building": [b for b, _, _ in rows],
         "courtyard_area_m2": [c for _, c, _ in rows],
         "area_m2": [a for _, _, a in rows],
         "geometry": [SOLID] * len(rows)},
        crs="EPSG:32633",
    )


def test_building_yes_is_excluded_as_untyped():
    """building=yes is a quarter of Mala Strana; counting it would mix homes
    with anything else left undertagged."""
    gdf = _frame([("residential", 100.0, 400.0), ("yes", 500.0, 400.0)])
    s = courtyard_summary(gdf)
    assert s["residential_buildings"] == 1
    assert s["courtyard_area_m2"] == 100.0        # the 500 from building=yes is out


def test_non_residential_is_excluded():
    gdf = _frame([("apartments", 50.0, 200.0), ("church", 900.0, 200.0),
                  ("retail", 80.0, 200.0)])
    assert courtyard_summary(gdf)["residential_buildings"] == 1


def test_threshold_reports_what_it_dropped():
    gdf = _frame([("residential", 6.0, 200.0),      # lightwell
                  ("residential", 300.0, 200.0),
                  ("house", 0.0, 200.0)])           # no courtyard at all
    s = courtyard_summary(gdf, min_area_m2=50)
    assert s["with_courtyard"] == 1
    assert s["courtyard_area_m2"] == 300.0
    assert s["below_threshold"] == 1                # the 6 m2 one, reported not hidden


def test_share_of_block():
    gdf = _frame([("residential", 25.0, 75.0)])
    assert courtyard_summary(gdf)["courtyard_share_of_footprint"] == pytest.approx(0.25)


def test_no_residential_buildings_is_not_an_error():
    s = courtyard_summary(_frame([("church", 900.0, 200.0)]))
    assert s["residential_buildings"] == 0 and s["with_courtyard"] == 0


@pytest.fixture(scope="module")
def courtyard_pbf(tmp_path_factory) -> str:
    """A residential multipolygon with a hole, plus a solid building=yes."""
    path = tmp_path_factory.mktemp("yard") / "yard.osm.pbf"
    w = osmium.SimpleWriter(str(path))
    outer, inner = [], []
    for i, (dx, dy) in enumerate([(0, 0), (0.002, 0), (0.002, 0.002), (0, 0.002)]):
        w.add_node(Node(id=10 + i, location=(17.10 + dx, 48.14 + dy))); outer.append(10 + i)
    for i, (dx, dy) in enumerate([(0.0008, 0.0008), (0.0012, 0.0008),
                                  (0.0012, 0.0012), (0.0008, 0.0012)]):
        w.add_node(Node(id=20 + i, location=(17.10 + dx, 48.14 + dy))); inner.append(20 + i)
    w.add_way(Way(id=100, nodes=outer + [outer[0]]))
    w.add_way(Way(id=101, nodes=inner + [inner[0]]))
    w.add_relation(Relation(id=500, members=[("w", 100, "outer"), ("w", 101, "inner")],
                            tags={"type": "multipolygon", "building": "residential"}))
    solid = []
    for i, (dx, dy) in enumerate([(0, 0), (0.001, 0), (0.001, 0.001), (0, 0.001)]):
        w.add_node(Node(id=30 + i, location=(17.105 + dx, 48.145 + dy))); solid.append(30 + i)
    w.add_way(Way(id=200, nodes=solid + [solid[0]], tags={"building": "yes"}))
    w.close()
    return str(path)


def test_end_to_end_from_a_real_extract(courtyard_pbf, tmp_path):
    boundary = Boundary(geometry=box(17.09, 48.13, 17.12, 48.16), name="Yard",
                        provenance="test")
    result = PbfSource(courtyard_pbf).fetch(boundary, {"building": True})
    enriched = add_metrics(result.features)

    assert len(enriched) == 2
    yard = enriched[enriched["building"] == "residential"].iloc[0]
    assert yard["courtyards"] == 1
    assert yard["courtyard_area_m2"] > 0
    assert enriched[enriched["building"] == "yes"].iloc[0]["courtyard_area_m2"] == 0

    meta = json.loads(
        next(p for p in write_result(result, tmp_path, features=enriched)
             if p.suffix == ".json").read_text(encoding="utf-8")
    )
    c = meta["counts"]["courtyards"]
    assert c["residential_buildings"] == 1      # building=yes is not residential
    assert c["with_courtyard"] == 1
    assert c["courtyard_area_m2"] > 0
