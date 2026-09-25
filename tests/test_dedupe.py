"""One building, one row: dropping ways that duplicate their own relation.

Tagging both a multipolygon relation and its outer way is a mapping error --
a leftover of the pre-2010 convention that put tags on the way -- and it
yields two areas for one building, with different geometry: the way has no
courtyard, the relation does.
"""

from __future__ import annotations

import osmium
import pytest
from osmium.osm.mutable import Node, Relation, Way
from shapely.geometry import box

from urb_inspect.sources import PbfSource
from urb_inspect.sources.base import Boundary

BOUNDARY = Boundary(geometry=box(0.5, 0.5, 1.5, 1.5), name="Fixture", provenance="test")
DUP = "way duplicated by relation"


class _Writer:
    """SimpleWriter with a square helper and its own node-id counter."""

    def __init__(self, path):
        self.w = osmium.SimpleWriter(str(path))
        self.nid = 0

    def square(self, x, y, size):
        ids = []
        for dx, dy in [(0, 0), (size, 0), (size, size), (0, size)]:
            self.nid += 1
            self.w.add_node(Node(id=self.nid, location=(x + dx, y + dy)))
            ids.append(self.nid)
        return ids + [ids[0]]

    def multipolygon(self, rel_id, hole_size, way_tags, rel_tags):
        outer = self.square(1.0, 1.0, 0.10)
        inner = self.square(1.0 + (0.10 - hole_size) / 2, 1.0 + (0.10 - hole_size) / 2,
                            hole_size)
        self.w.add_way(Way(id=100, nodes=outer, tags=way_tags))
        self.w.add_way(Way(id=101, nodes=inner))
        self.w.add_relation(Relation(id=rel_id,
                                     members=[("w", 100, "outer"), ("w", 101, "inner")],
                                     tags=rel_tags))

    def close(self):
        self.w.close()


def _fetch(path):
    return PbfSource(str(path)).fetch(BOUNDARY, {"building": True})


MP_TAGS = {"type": "multipolygon", "building": "yes"}


@pytest.mark.parametrize("hole_size,label", [(0.02, "small hole"), (0.06, "large hole")])
def test_double_tagged_way_is_dropped(tmp_path, hole_size, label):
    """The large-hole case is why outlines are compared rather than areas.

    With a hole taking a third of the footprint the relation's area is nowhere
    near the way's, so an area-similarity test would miss the duplicate.
    """
    path = tmp_path / f"dup-{label.replace(' ', '-')}.osm.pbf"
    w = _Writer(path)
    w.multipolygon(500, hole_size, {"building": "yes"}, MP_TAGS)
    w.close()

    result = _fetch(path)
    assert len(result) == 1
    assert result.features.iloc[0]["element"] == "relation"
    assert result.discarded[DUP] == 1


def test_correctly_mapped_multipolygon_is_untouched(tmp_path):
    """Tags on the relation, outer way bare: nothing to deduplicate."""
    path = tmp_path / "clean.osm.pbf"
    w = _Writer(path)
    w.multipolygon(500, 0.02, {}, MP_TAGS)
    w.close()

    result = _fetch(path)
    assert len(result) == 1
    assert result.discarded[DUP] == 0


def test_building_inside_a_courtyard_survives(tmp_path):
    """The false positive that matters.

    A separate building standing in a courtyard must not be mistaken for a
    duplicate. It is safe because the courtyard is a hole, so the small
    building lies outside the relation's polygon -- but only an outline match
    in both directions guarantees that, containment alone would not.
    """
    path = tmp_path / "infill.osm.pbf"
    w = _Writer(path)
    w.multipolygon(500, 0.06, {}, MP_TAGS)
    w.w.add_way(Way(id=300, nodes=w.square(1.03, 1.03, 0.02),
                    tags={"building": "garage"}))
    w.close()

    result = _fetch(path)
    assert len(result) == 2
    assert set(result.features["building"]) == {"yes", "garage"}
    assert result.discarded[DUP] == 0


def test_independent_neighbours_are_not_merged(tmp_path):
    path = tmp_path / "neighbours.osm.pbf"
    w = _Writer(path)
    w.w.add_way(Way(id=400, nodes=w.square(1.00, 1.0, 0.05), tags={"building": "yes"}))
    w.w.add_way(Way(id=401, nodes=w.square(1.06, 1.0, 0.05), tags={"building": "yes"}))
    w.close()

    result = _fetch(path)
    assert len(result) == 2
    assert result.discarded[DUP] == 0


def test_discard_reason_is_always_reported(tmp_path):
    """Present even at zero: a reader must be able to tell 'none' from 'not checked'."""
    path = tmp_path / "plain.osm.pbf"
    w = _Writer(path)
    w.w.add_way(Way(id=400, nodes=w.square(1.0, 1.0, 0.05), tags={"building": "yes"}))
    w.close()
    assert DUP in _fetch(path).discarded
