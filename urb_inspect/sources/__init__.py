"""Feature sources: interchangeable backends returning GeoDataFrames.

PbfSource is the primary source (offline, reproducible, unmetered).
OverpassSource provides geocoding and an independent cross-check.

OverpassSource is imported lazily so the offline path never requires osmnx.
"""

from .base import (
    NON_BUILDING,
    OSM_CRS,
    Boundary,
    FeatureSource,
    FetchResult,
    tags_match,
)
from .pbf import PbfSource

__all__ = [
    "NON_BUILDING",
    "OSM_CRS",
    "Boundary",
    "FeatureSource",
    "FetchResult",
    "PbfSource",
    "OverpassSource",
    "tags_match",
]


def __getattr__(name: str):
    if name == "OverpassSource":
        from .overpass import OverpassSource

        return OverpassSource
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
