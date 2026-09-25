"""Source-agnostic types shared by every feature backend.

The guiding rule here: every number a source returns must also say what it
left out. A silent filter is worse than a loud error -- a count that quietly
drops a fifth of its input looks exactly like a correct count.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence, runtime_checkable

import geopandas as gpd
from shapely.geometry.base import BaseGeometry

# OSM stores everything in WGS84. Anything metric (areas, distances) must be
# reprojected first -- degrees are not a unit of length.
OSM_CRS = "EPSG:4326"

# Values of building=* that explicitly deny a building exists.
NON_BUILDING = frozenset({"no", "none"})

# Tag keys that mark a service or point of interest. Order matters: one object
# can carry several (a surgery is often amenity=doctors *and* healthcare=doctor,
# a shop is often also building=retail), and the first match decides how the
# feature is labelled, so that it is counted once and under its primary role.
DEFAULT_POI_KEYS: tuple[str, ...] = (
    "amenity",
    "shop",
    "leisure",
    "tourism",
    "office",
    "healthcare",
    "craft",
)

# Values that assert a service is *not* there. shop=no on a former shopfront is
# a deliberate mapping statement, not a service.
ABSENT_VALUES = frozenset({"no", "none"})

Predicate = str  # "within" | "intersects"


@dataclass(frozen=True)
class Boundary:
    """An area to query inside, plus where it came from."""

    geometry: BaseGeometry
    osm_relation_id: int | None = None
    name: str | None = None
    provenance: str = "unknown"

    def describe(self) -> str:
        rid = f"relation {self.osm_relation_id}" if self.osm_relation_id else "ad-hoc geometry"
        return f"{self.name or '?'} ({rid}, via {self.provenance})"


@dataclass(frozen=True)
class FetchResult:
    """Features plus the context needed to interpret the count."""

    features: gpd.GeoDataFrame
    boundary: Boundary
    snapshot: str
    predicate: Predicate
    source: str
    discarded: dict[str, int] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.features)

    def report(self) -> str:
        lines = [
            f"  source     : {self.source}",
            f"  boundary   : {self.boundary.describe()}",
            f"  snapshot   : {self.snapshot}",
            f"  predicate  : {self.predicate}",
            f"  features   : {len(self.features)}",
        ]
        if "element" in self.features.columns:
            for elem, n in sorted(self.features["element"].value_counts().items()):
                lines.append(f"      as {elem:9s}: {n}")
        if self.discarded:
            lines.append("  discarded  :")
            for reason, n in sorted(self.discarded.items(), key=lambda kv: -kv[1]):
                if n:
                    lines.append(f"      {reason:28s} {n}")
        return "\n".join(lines)


@runtime_checkable
class FeatureSource(Protocol):
    """Anything that can answer 'which features are in this area'."""

    name: str

    @property
    def snapshot(self) -> str:
        """When this source's data was current (ISO 8601, or 'live')."""

    def fetch(
        self,
        boundary: Boundary,
        tags: dict[str, object],
        predicate: Predicate = "within",
    ) -> FetchResult: ...


def tags_match(obj_tags: dict[str, str], wanted: dict[str, object]) -> bool:
    """OSMnx-style tag matching: True (any value), a string, or a list."""
    for key, want in wanted.items():
        if key not in obj_tags:
            return False
        if want is True:
            continue
        value = obj_tags[key]
        if isinstance(want, str):
            if value != want:
                return False
        elif isinstance(want, (list, tuple, set)):
            if value not in want:
                return False
    return True


def first_poi_key(
    obj_tags: dict[str, str], keys: Sequence[str]
) -> tuple[str | None, str | None]:
    """The first of `keys` this object carries with a real value.

    Returns (None, None) when it carries none, or only explicit absences.
    Deciding by `keys` order is what keeps a doubly-tagged object one feature.
    """
    for key in keys:
        value = obj_tags.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if not text or text.lower() in ABSENT_VALUES:
            continue
        return key, text
    return None, None


def empty_frame() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=OSM_CRS)
