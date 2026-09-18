"""building:flats summation, end to end from a real .osm.pbf.

Written against a fixture file rather than a hand-made DataFrame so the whole
path is exercised: area assembly, the building=no exclusion, the boundary
test, tag parsing and the summary. A DataFrame test would have skipped the
three places a flat count can silently go wrong -- a building outside the
area, a building that denies being one, and a value that is not an integer.
"""

from __future__ import annotations

import json

import osmium
import pytest
from osmium.osm.mutable import Node, Way
from shapely.geometry import box

from urb_inspect.export import write_result
from urb_inspect.metrics import add_metrics, flats_summary, parse_flats
from urb_inspect.sources import PbfSource
from urb_inspect.sources.base import Boundary

BBOX = box(17.10, 48.14, 17.11, 48.15)

# (way id, tags). Laid out so every branch of the summary is represented, and
# so the expected numbers can be read straight off this table.
BUILDINGS = [
    (1, {"building": "apartments", "building:flats": "12"}),   # residential, 12
    (2, {"building": "apartments", "building:flats": "8"}),    # residential, 8
    (3, {"building": "house", "building:flats": "1"}),         # residential, 1
    (4, {"building": "residential"}),                          # residential, untagged
    (5, {"building": "church", "building:flats": "3"}),        # tagged, NOT residential
    (6, {"building": "apartments", "building:flats": "12;14"}),  # unparseable
    (7, {"building": "apartments", "building:flats": "~10"}),    # unparseable
    (8, {"building": "apartments", "building:flats": "0"}),      # tagged, zero
    (9, {"building": "no", "building:flats": "99"}),             # not a building at all
]
# Outside the boundary: a large flat count that must not reach any total.
OUTSIDE_BUILDING = (10, {"building": "apartments", "building:flats": "1000"})

EXPECTED = {
    "buildings": 8,                    # 1-8; 9 denies being a building, 10 is outside
    "with_flats_tag": 5,               # 1, 2, 3, 5, 8
    "flats_sum": 24,                   # 12 + 8 + 1 + 3 + 0
    "residential_buildings": 7,        # 1, 2, 3, 4, 6, 7, 8  (5 is a church)
    "residential_with_flats_tag": 4,   # 1, 2, 3, 8
    "residential_flats_sum": 21,       # 12 + 8 + 1 + 0
    "unparseable_flats_tag": 2,        # 6, 7
}


def _square(writer: osmium.SimpleWriter, node_id: int, lon: float, lat: float,
            size: float = 0.0004) -> list[int]:
    corners = [(lon, lat), (lon + size, lat), (lon + size, lat + size), (lon, lat + size)]
    ids = []
    for offset, (x, y) in enumerate(corners):
        writer.add_node(Node(id=node_id + offset, location=(x, y)))
        ids.append(node_id + offset)
    return ids + [ids[0]]


@pytest.fixture(scope="module")
def flats_pbf(tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("flats") / "flats.osm.pbf"
    w = osmium.SimpleWriter(str(path))
    for index, (way_id, tags) in enumerate(BUILDINGS):
        lon = 17.101 + index * 0.0008
        w.add_way(Way(id=way_id, nodes=_square(w, 1000 + index * 10, lon, 48.141), tags=tags))
    out_id, out_tags = OUTSIDE_BUILDING
    w.add_way(Way(id=out_id, nodes=_square(w, 9000, 17.300, 48.300), tags=out_tags))
    w.close()
    return str(path)


@pytest.fixture(scope="module")
def result(flats_pbf):
    boundary = Boundary(geometry=BBOX, name="Flats fixture", provenance="test")
    return PbfSource(flats_pbf).fetch(boundary, {"building": True})


@pytest.fixture(scope="module")
def enriched(result):
    return add_metrics(result.features)


def test_fixture_reaches_the_pipeline_as_expected(result):
    """Guards the fixture itself: a miscounted input makes every total below meaningless."""
    assert len(result.features) == EXPECTED["buildings"]
    assert set(result.features["osm_id"]) == {1, 2, 3, 4, 5, 6, 7, 8}
    assert result.discarded["building=no"] == 1
    assert result.discarded["outside boundary"] == 1


@pytest.mark.parametrize("field", sorted(EXPECTED))
def test_flats_summary_field(enriched, field):
    assert flats_summary(enriched)[field] == EXPECTED[field]


def test_flats_column_holds_parsed_integers(enriched):
    import pandas as pd

    by_id = dict(zip(enriched["osm_id"], enriched["flats"]))
    assert by_id[1] == 12
    assert by_id[8] == 0              # zero is a real count, not a missing value
    assert pd.isna(by_id[4])          # untagged
    assert pd.isna(by_id[6])          # "12;14" is not guessed at
    assert str(enriched["flats"].dtype) == "Int64"


def test_building_outside_the_boundary_contributes_nothing(enriched):
    """The 1000-flat block outside the area is the loudest possible leak."""
    assert 10 not in set(enriched["osm_id"])
    assert enriched["flats"].sum() == EXPECTED["flats_sum"]
    assert enriched["flats"].max() == 12


def test_building_no_is_excluded_even_when_it_carries_flats(enriched):
    assert 9 not in set(enriched["osm_id"])
    assert 99 not in set(enriched["flats"].dropna())


def test_church_counts_in_the_total_but_not_the_residential_total(enriched):
    """The residential figure is the one that means anything for occupancy."""
    s = flats_summary(enriched)
    assert s["flats_sum"] - s["residential_flats_sum"] == 3


def test_unparseable_values_are_reported_not_guessed(enriched):
    s = flats_summary(enriched)
    assert s["unparseable_flats_tag"] == 2
    # they still count as buildings, just not as flat counts
    assert s["residential_buildings"] > s["residential_with_flats_tag"]


@pytest.mark.parametrize(
    "raw,expected",
    [("12", 12), ("0", 0), (" 7 ", 7), ("12;14", None), ("~10", None),
     ("12 flats", None), ("-3", None), ("", None), (None, None)],
)
def test_parse_flats(raw, expected):
    assert parse_flats(raw) == expected


def test_totals_reach_meta_json(result, enriched, tmp_path):
    """The summary is only useful if it survives to the exported file."""
    meta_path = next(
        p for p in write_result(result, tmp_path, features=enriched) if p.suffix == ".json"
    )
    flats = json.loads(meta_path.read_text(encoding="utf-8"))["counts"]["flats"]
    assert flats == EXPECTED
