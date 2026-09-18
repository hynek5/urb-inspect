"""Classification of OSM service tags into category and audience.

The research question is whether a quarter still serves the people who live in
it, so every service is labelled twice:

  category  what the service *is*      (food_daily, health, accommodation, ...)
  audience  who it is plausibly *for*  (everyday, tourist, mixed, unknown)

The table lives in data/poi_classes.csv rather than in code: it is a judgement
call about a city, it will be argued with, and editing a CSV is how that
argument gets settled. Unknown (key, value) pairs are never dropped -- they
become other/unknown and are reported, so the table can be extended from what
the data actually contains instead of from what we guessed it would.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DEFAULT_CLASSES = Path(__file__).parent / "data" / "poi_classes.csv"

UNKNOWN_CATEGORY = "other"
UNKNOWN_AUDIENCE = "unknown"


@dataclass(frozen=True)
class PoiClass:
    category: str
    audience: str


UNCLASSIFIED = PoiClass(UNKNOWN_CATEGORY, UNKNOWN_AUDIENCE)


@lru_cache(maxsize=8)
def load_classes(path: str | Path | None = None) -> dict[tuple[str, str], PoiClass]:
    """Read the table. Cached: it is read once per run but joined row by row."""
    path = Path(path) if path else DEFAULT_CLASSES
    table: dict[tuple[str, str], PoiClass] = {}
    with Path(path).open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            key = (row["key"].strip(), row["value"].strip())
            table[key] = PoiClass(row["category"].strip(), row["audience"].strip())
    if not table:
        raise ValueError(f"{path}: no classification rows")
    return table


def classify(
    key: str | None,
    value: str | None,
    table: dict[tuple[str, str], PoiClass] | None = None,
) -> PoiClass:
    """Look up one (key, value) pair, falling back to other/unknown."""
    if not key or not value:
        return UNCLASSIFIED
    table = load_classes() if table is None else table
    return table.get((str(key).strip(), str(value).strip()), UNCLASSIFIED)


def is_classified(cls: PoiClass) -> bool:
    return cls != UNCLASSIFIED
