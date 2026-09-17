"""POI extraction: the node pass, deduplication, and classification.

Fixtures are written as real .osm.pbf files with osmium.SimpleWriter rather
than mocked, so the tests exercise the same libosmium area assembly the
production path uses. Everything here is offline.
"""

from __future__ import annotations

from pathlib import Path

import osmium
import pytest
from osmium.osm.mutable import Node, Way
from shapely.geometry import box

from urb_inspect.metrics import add_poi_metrics, poi_summary, unclassified_pairs
from urb_inspect.sources import PbfSource
from urb_inspect.sources.base import Boundary

# The study area for the synthetic fixture, and a point well outside it.
INSIDE = (17.105, 48.143)
OUTSIDE = (17.300, 48.300)
BBOX = box(17.10, 48.14, 17.11, 48.15)

DATA = Path(__file__).resolve().parent.parent / "urb_inspect" / "data"
BRATISLAVA_PBF = DATA / "bratislavsky.pbf"
BRATISLAVA_POLY = DATA / "bratislava_polygon.geojson"


def _square(writer: osmium.SimpleWriter, first_id: int, lon: float, lat: float,
            size: float = 0.0005) -> list[int]:
    """Write four nodes forming a closed ring and return their ids."""
    corners = [(lon, lat), (lon + size, lat), (lon + size, lat + size), (lon, lat + size)]
    ids = []
    for offset, (x, y) in enumerate(corners):
        node_id = first_id + offset
        writer.add_node(Node(id=node_id, location=(x, y)))
        ids.append(node_id)
    return ids + [ids[0]]


@pytest.fixture(scope="module")
def synthetic_pbf(tmp_path_factory) -> str:
    """One fixture covering every case the merge logic has to get right."""
    path = tmp_path_factory.mktemp("poi") / "synthetic.osm.pbf"
    w = osmium.SimpleWriter(str(path))

    # 1. plain node POI, inside
    w.add_node(Node(id=1, location=INSIDE, tags={"shop": "bakery", "name": "Pekaren"}))
    # 2. plain node POI, outside the boundary
    w.add_node(Node(id=2, location=OUTSIDE, tags={"shop": "supermarket"}))
    # 3. surgery tagged twice -- one service, not two
    w.add_node(Node(id=3, location=(17.1051, 48.1431),
                    tags={"amenity": "doctors", "healthcare": "doctor"}))
    # 4. a value the classification table does not know
    w.add_node(Node(id=4, location=(17.1052, 48.1432), tags={"shop": "wibble"}))
    # 5. explicit absence -- a mapped statement that there is no shop here
    w.add_node(Node(id=5, location=(17.1053, 48.1433), tags={"shop": "no"}))
    # 6. node with no POI key at all
    w.add_node(Node(id=6, location=(17.1054, 48.1434), tags={"building": "yes"}))

    # 7. area POI, inside
    w.add_way(Way(id=100, nodes=_square(w, 10, 17.106, 48.144),
                  tags={"leisure": "fitness_centre", "name": "Gym"}))
    # 8. a building that is also a shop -- one POI, under its shop role
    w.add_way(Way(id=200, nodes=_square(w, 20, 17.107, 48.145),
                  tags={"building": "retail", "shop": "bakery"}))
    w.close()
    return str(path)


@pytest.fixture(scope="module")
def boundary() -> Boundary:
    return Boundary(geometry=BBOX, name="Synthetic", provenance="test")


@pytest.fixture(scope="module")
def result(synthetic_pbf, boundary):
    return PbfSource(synthetic_pbf).fetch_pois(boundary)


def test_node_pass_finds_standalone_pois(result):
    """The reason fetch_pois exists: fetch() assembles areas and would see none."""
    nodes = result.features[result.features["element"] == "node"]
    assert set(nodes["osm_id"]) == {1, 3, 4}


def test_area_pois_are_included_with_polygon_geometry(result):
    areas = result.features[result.features["element"] == "way"]
    assert set(areas["osm_id"]) == {100, 200}
    assert set(areas.geometry.geom_type) <= {"Polygon", "MultiPolygon"}


def test_node_outside_boundary_is_counted_not_dropped(result):
    assert 2 not in set(result.features["osm_id"])
    assert result.discarded["outside boundary"] >= 1


def test_object_tagged_building_and_shop_counts_once(result):
    """A shop in a building is one service. It is filed under shop, not building."""
    rows = result.features[result.features["osm_id"] == 200]
    assert len(rows) == 1
    assert rows.iloc[0]["poi_key"] == "shop"
    assert rows.iloc[0]["poi_value"] == "bakery"


def test_doctors_and_healthcare_on_one_node_counts_once(result):
    """amenity and healthcare overlap; key order decides, and it stays one row."""
    rows = result.features[result.features["osm_id"] == 3]
    assert len(rows) == 1
    assert rows.iloc[0]["poi_key"] == "amenity"
    assert rows.iloc[0]["poi_value"] == "doctors"


def test_explicit_absence_is_reported_not_silently_dropped(result):
    assert 5 not in set(result.features["osm_id"])
    assert result.discarded["explicit absence (key=no)"] == 1


def test_discarded_reports_every_reason(result):
    for reason in ("outside boundary", "no matching key", "duplicate (node+area)"):
        assert reason in result.discarded


def test_unknown_value_lands_in_other_unknown_and_is_reported(result):
    enriched = add_poi_metrics(result.features)
    row = enriched[enriched["osm_id"] == 4].iloc[0]
    assert (row["category"], row["audience"]) == ("other", "unknown")
    assert row["classified"] is False or not row["classified"]
    assert unclassified_pairs(enriched)["shop=wibble"] == 1


def test_short_term_rental_is_not_folded_into_accommodation():
    """Flats let to visitors are the mechanism under study; they stay countable."""
    from urb_inspect.poi_classes import classify

    assert classify("tourism", "apartment").category == "short_term_rental"
    assert classify("tourism", "hotel").category == "accommodation"
    assert classify("tourism", "apartment").audience == "tourist"


def test_poi_summary_shape(result):
    summary = poi_summary(add_poi_metrics(result.features))
    for field in ("pois", "by_poi_key", "by_category", "by_audience", "unclassified"):
        assert field in summary
    assert summary["pois"] == len(result.features)


@pytest.mark.skipif(
    not (BRATISLAVA_PBF.exists() and BRATISLAVA_POLY.exists()),
    reason="bratislavsky.pbf is not committed (gitignored); download it to run this",
)
def test_smoke_on_real_extract():
    from urb_inspect.sources.geojson_boundary import boundary_from_geojson

    src = PbfSource(str(BRATISLAVA_PBF))
    res = src.fetch_pois(boundary_from_geojson(BRATISLAVA_POLY))

    assert len(res) > 0
    assert res.discarded["outside boundary"] > 0
    # the point of the node pass: a real quarter's services are mostly nodes
    assert (res.features["element"] == "node").sum() > 0

    enriched = add_poi_metrics(res.features)
    assert set(enriched["audience"]) <= {"everyday", "tourist", "mixed", "unknown"}


def test_value_counts_are_grouped_by_key_and_ranked():
    """Plain tallies, discovered from the data rather than from a table."""
    import geopandas as gpd
    from shapely.geometry import Point

    from urb_inspect.metrics import summarize_value_counts, value_counts_by_key

    gdf = gpd.GeoDataFrame(
        {
            "poi_key": ["amenity", "amenity", "amenity", "shop", "shop", "tourism"],
            "poi_value": ["bar", "bar", "cafe", "bakery", "bakery", "hotel"],
            "geometry": [Point(0, 0)] * 6,
        },
        crs="EPSG:4326",
    )
    tally = value_counts_by_key(gdf)

    assert tally["amenity"] == {"bar": 2, "cafe": 1}
    assert tally["shop"] == {"bakery": 2}
    # busiest key first, and values ranked within it
    assert list(tally) == ["amenity", "shop", "tourism"]
    assert list(tally["amenity"]) == ["bar", "cafe"]

    text = summarize_value_counts(gdf, top=1)
    assert "bar" in text and "1 more values" in text


def test_value_counts_need_no_classification():
    """The tally must work on a raw fetch result, before add_poi_metrics."""
    import geopandas as gpd
    from shapely.geometry import Point

    from urb_inspect.metrics import value_counts_by_key

    raw = gpd.GeoDataFrame(
        {"poi_key": ["shop"], "poi_value": ["wibble"], "geometry": [Point(0, 0)]},
        crs="EPSG:4326",
    )
    assert value_counts_by_key(raw) == {"shop": {"wibble": 1}}


def test_value_counts_empty_frame():
    from urb_inspect.metrics import value_counts_by_key
    from urb_inspect.sources.base import empty_frame

    assert value_counts_by_key(empty_frame()) == {}
