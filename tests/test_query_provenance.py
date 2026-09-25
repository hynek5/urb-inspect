"""What was asked for has to reach the file, not just the screen.

Two runs over the same extract and the same boundary can differ only in the
tags or keys they requested. Without that recorded, a narrower run is
indistinguishable from a full one -- it just looks like a quarter with fewer
services in it.
"""

from __future__ import annotations

import json

import osmium
import pytest
from osmium.osm.mutable import Node, Way
from shapely.geometry import box

from urb_inspect.export import write_result
from urb_inspect.sources import PbfSource
from urb_inspect.sources.base import DEFAULT_POI_KEYS, Boundary

BOUNDARY = Boundary(geometry=box(17.09, 48.13, 17.12, 48.16), name="Q", provenance="test")


@pytest.fixture(scope="module")
def mixed_pbf(tmp_path_factory) -> str:
    """One node per POI key family, plus a building."""
    path = tmp_path_factory.mktemp("q") / "mixed.osm.pbf"
    w = osmium.SimpleWriter(str(path))
    for i, tags in enumerate([
        {"amenity": "cafe"},
        {"shop": "bakery"},
        {"tourism": "hotel"},
        {"leisure": "fitness_centre"},
    ]):
        w.add_node(Node(id=1 + i, location=(17.100 + i * 0.001, 48.140), tags=tags))
    ids = []
    for i, (dx, dy) in enumerate([(0, 0), (0.001, 0), (0.001, 0.001), (0, 0.001)]):
        w.add_node(Node(id=50 + i, location=(17.11 + dx, 48.15 + dy))); ids.append(50 + i)
    w.add_way(Way(id=100, nodes=ids + [ids[0]], tags={"building": "house"}))
    w.close()
    return str(path)


def _meta(result, tmp_path, name):
    paths = write_result(result, tmp_path / name)
    return json.loads(
        next(p for p in paths if p.suffix == ".json").read_text(encoding="utf-8")
    )


def test_poi_keys_are_recorded(mixed_pbf, tmp_path):
    result = PbfSource(mixed_pbf).fetch_pois(BOUNDARY, keys=("amenity", "shop"))
    assert result.query == {"keys": ["amenity", "shop"]}
    assert _meta(result, tmp_path, "narrow")["query"] == {"keys": ["amenity", "shop"]}


def test_default_keys_are_recorded_explicitly(mixed_pbf, tmp_path):
    """Recorded even when nothing was overridden: a reader should not have to
    know what the default was on the day the file was written."""
    result = PbfSource(mixed_pbf).fetch_pois(BOUNDARY)
    assert _meta(result, tmp_path, "full")["query"]["keys"] == list(DEFAULT_POI_KEYS)


def test_narrow_and_full_runs_are_distinguishable(mixed_pbf, tmp_path):
    """The regression this exists for: same extract, same boundary, different
    counts, and previously nothing in the file to explain why."""
    src = PbfSource(mixed_pbf)
    narrow = _meta(src.fetch_pois(BOUNDARY, keys=("amenity",)), tmp_path, "n")
    full = _meta(src.fetch_pois(BOUNDARY), tmp_path, "f")

    assert narrow["counts"]["features"] < full["counts"]["features"]
    assert narrow["query"] != full["query"]
    assert narrow["boundary"] == full["boundary"]     # nothing else differs
    assert narrow["snapshot"] == full["snapshot"]


def test_building_tags_are_recorded(mixed_pbf, tmp_path):
    result = PbfSource(mixed_pbf).fetch(BOUNDARY, {"building": True})
    assert result.query == {"tags": {"building": True}}
    assert _meta(result, tmp_path, "b")["query"] == {"tags": {"building": True}}


def test_query_survives_json_round_trip(mixed_pbf, tmp_path):
    """Tag filters can hold True or a list; both have to serialise."""
    result = PbfSource(mixed_pbf).fetch(BOUNDARY, {"building": ["house", "yes"]})
    assert _meta(result, tmp_path, "list")["query"]["tags"]["building"] == ["house", "yes"]
