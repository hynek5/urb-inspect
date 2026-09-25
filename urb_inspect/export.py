"""Writing results to disk.

Three files per run, because a geometry file alone loses the context needed
to interpret it:

  <base>.gpkg       full geometry plus every OSM tag as a column
  <base>.csv        the columns a human actually reads, no geometry
  <base>.meta.json  provenance: source, snapshot, predicate, what was discarded

GeoPackage is the default geometry format: one file, typed columns, opens
directly in QGIS, and unlike GeoJSON it does not re-encode coordinates as text.
"""

from __future__ import annotations

import json
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd

from .metrics import (
    courtyard_summary,
    flats_summary,
    poi_summary,
    value_counts_by_key,
)
from .sources.base import FetchResult

# Columns worth putting in the human-readable CSV. OSM data is very wide --
# a few hundred sparse tag columns is normal -- so the CSV is a readable
# projection while the GeoPackage keeps everything.
CORE_COLUMNS = [
    "element",
    "osm_id",
    "building",
    "name",
    "addr:street",
    "addr:housenumber",
    "addr:conscriptionnumber",
    "addr:postcode",
    "building:levels",
    "building:flats",
    "flats",
    "roof:shape",
    "start_date",
    "heritage",
    "area_m2",
    "courtyards",
    "courtyard_area_m2",
]

# POI results are a different shape: mostly point geometry, and the columns
# that matter are what the service is rather than how big its footprint is.
POI_CORE_COLUMNS = [
    "element",
    "osm_id",
    "poi_key",
    "poi_value",
    "category",
    "audience",
    "name",
    "addr:street",
    "addr:housenumber",
    "cuisine",
    "opening_hours",
]


def core_columns(gdf: gpd.GeoDataFrame) -> list[str]:
    """Which projection to write to CSV, decided by what the frame contains."""
    wanted = POI_CORE_COLUMNS if "poi_key" in gdf.columns else CORE_COLUMNS
    return [c for c in wanted if c in gdf.columns]


def slugify(text: str | None, fallback: str = "area") -> str:
    if not text:
        return fallback
    text = text.split(",")[0]
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    out = "".join(c.lower() if c.isalnum() else "-" for c in text)
    out = "-".join(part for part in out.split("-") if part)
    return out or fallback


def sanitize(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Flatten values no file format can represent.

    OSMnx returns list-valued columns (relation member ids, for instance);
    these have no scalar representation in GPKG or CSV.
    """
    out = gdf.copy()
    for col in out.columns:
        if col == out.geometry.name:
            continue
        if out[col].map(lambda v: isinstance(v, (list, tuple, set, dict))).any():
            out[col] = out[col].map(
                lambda v: "; ".join(map(str, v))
                if isinstance(v, (list, tuple, set))
                else (json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v)
            )
    return out


def _metadata(result: FetchResult, gdf: gpd.GeoDataFrame,
              min_courtyard_m2: float = 0.0, courtyard_bin_m2: float = 25.0) -> dict:
    by_element = (
        gdf["element"].value_counts().to_dict() if "element" in gdf.columns else {}
    )
    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": result.source,
        "snapshot": result.snapshot,
        "predicate": result.predicate,
        "query": result.query,
        "crs": str(gdf.crs) if gdf.crs else None,
        "boundary": {
            "name": result.boundary.name,
            "osm_relation_id": result.boundary.osm_relation_id,
            "provenance": result.boundary.provenance,
            "bounds": list(result.boundary.geometry.bounds),
        },
        "counts": {
            "features": int(len(gdf)),
            "by_element": {str(k): int(v) for k, v in by_element.items()},
        },
        "discarded": {k: int(v) for k, v in result.discarded.items()},
    }
    if "building" in gdf.columns:
        meta["counts"]["by_building"] = {
            str(k): int(v) for k, v in gdf["building"].value_counts().items()
        }
    if "courtyards" in gdf.columns:
        meta["counts"]["with_courtyards"] = int((gdf["courtyards"] > 0).sum())
    if "courtyard_area_m2" in gdf.columns:
        # Must use the same threshold the run reported, or the file contradicts
        # the summary that produced it -- and the file is what survives.
        meta["counts"]["courtyards"] = courtyard_summary(
            gdf, min_courtyard_m2, bin_width=courtyard_bin_m2
        )
    if "flats" in gdf.columns:
        meta["counts"]["flats"] = flats_summary(gdf)
    if "poi_key" in gdf.columns:
        # unclassified_pairs travels with the counts on purpose: it is the
        # worklist for extending poi_classes.csv, and it is only meaningful
        # next to the run that produced it.
        meta["counts"].update(poi_summary(gdf))
        # Full tally, not truncated: this is a data file, and the long tail is
        # exactly where the unanticipated values live.
        meta["counts"]["by_key_value"] = value_counts_by_key(gdf)
    return meta


def write_result(
    result: FetchResult,
    out_dir: str | Path = "out",
    basename: str | None = None,
    features: gpd.GeoDataFrame | None = None,
    min_courtyard_m2: float = 0.0,
    courtyard_bin_m2: float = 25.0,
) -> list[Path]:
    """Write geometry, a readable table, and provenance. Returns the paths.

    `min_courtyard_m2` must match what the caller summarised with: the point
    of the metadata is to describe the run, so a reporting option that changes
    the numbers has to reach the file as well as the screen.
    """
    gdf = result.features if features is None else features
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if basename is None:
        rid = result.boundary.osm_relation_id
        stem = slugify(result.boundary.name, fallback="area")
        tag = "pbf" if "pbf" in result.source else "overpass"
        basename = f"{stem}-r{rid}-{tag}" if rid else f"{stem}-{tag}"

    written: list[Path] = []

    meta_path = out_dir / f"{basename}.meta.json"
    meta_path.write_text(
        json.dumps(_metadata(result, gdf, min_courtyard_m2, courtyard_bin_m2),
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    written.append(meta_path)

    if gdf.empty:
        return written

    clean = sanitize(gdf)

    # Writers call reset_index(drop=False) internally; a named index level that
    # duplicates a column name raises. Sources already return a RangeIndex --
    # this covers frames assembled by hand.
    named = {n for n in clean.index.names if n is not None}
    if named & set(clean.columns):
        clean = clean.reset_index(drop=True)

    gpkg_path = out_dir / f"{basename}.gpkg"
    clean.to_file(gpkg_path, driver="GPKG", layer="features")
    written.append(gpkg_path)

    cols = core_columns(clean)
    csv_path = out_dir / f"{basename}.csv"
    clean[cols].to_csv(csv_path, index=False, encoding="utf-8")
    written.append(csv_path)

    return written
