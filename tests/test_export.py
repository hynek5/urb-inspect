"""Regression tests for result writing."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon

from urb_inspect.export import sanitize, slugify, write_result
from urb_inspect.sources.base import Boundary, FetchResult

BOX = Polygon([(0, 0), (0, 4), (4, 4), (4, 0)])
A = Polygon([(0, 0), (0, 1), (1, 1), (1, 0)])
B = Polygon([(2, 2), (2, 3), (3, 3), (3, 2)], [[(2.4, 2.4), (2.4, 2.6), (2.6, 2.6), (2.6, 2.4)]])


def _result(gdf: gpd.GeoDataFrame) -> FetchResult:
    return FetchResult(
        features=gdf,
        boundary=Boundary(BOX, 1990592, "Innere Stadt, Vienna", "test"),
        snapshot="live",
        predicate="intersects",
        source="overpass/osmnx",
        discarded={"not an area": 13},
    )


def test_multiindex_colliding_with_columns_is_writable(tmp_path):
    """OSMnx indexes by (element, id) and we lift those into columns.

    Writers call reset_index(drop=False) internally, which raised
    "cannot insert element, already exists" until the index was dropped.
    """
    idx = pd.MultiIndex.from_tuples([("way", 1), ("relation", 2)], names=["element", "id"])
    gdf = gpd.GeoDataFrame(
        {"building": ["yes", "church"], "element": ["way", "relation"],
         "osm_id": [1, 2], "geometry": [A, B]},
        index=idx, crs="EPSG:4326",
    )
    paths = write_result(_result(gdf), tmp_path)
    gpkg = next(p for p in paths if p.suffix == ".gpkg")
    assert len(gpd.read_file(gpkg)) == 2


def test_list_valued_columns_are_flattened(tmp_path):
    gdf = gpd.GeoDataFrame(
        {"building": ["yes", "church"], "nodes": [[1, 2, 3], [4, 5]],
         "geometry": [A, B]},
        crs="EPSG:4326",
    )
    assert sanitize(gdf)["nodes"].tolist() == ["1; 2; 3", "4; 5"]
    paths = write_result(_result(gdf), tmp_path)
    assert any(p.suffix == ".gpkg" for p in paths)


def test_metadata_records_provenance_and_discards(tmp_path):
    import json

    gdf = gpd.GeoDataFrame({"building": ["yes"], "geometry": [A]}, crs="EPSG:4326")
    meta_path = next(p for p in write_result(_result(gdf), tmp_path) if p.suffix == ".json")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    assert meta["snapshot"] == "live"
    assert meta["predicate"] == "intersects"
    assert meta["boundary"]["osm_relation_id"] == 1990592
    assert meta["discarded"]["not an area"] == 13
    assert meta["counts"]["features"] == 1


def test_empty_result_still_writes_metadata(tmp_path):
    empty = gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs="EPSG:4326")
    paths = write_result(_result(empty), tmp_path)
    assert [p.suffix for p in paths] == [".json"]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Malá Strana", "mala-strana"),
        ("Innere Stadt, Vienna, 1010, Austria", "innere-stadt"),
        ("Praha 1", "praha-1"),
        (None, "area"),
    ],
)
def test_slugify(text, expected):
    assert slugify(text) == expected
