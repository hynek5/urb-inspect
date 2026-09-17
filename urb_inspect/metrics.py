"""Per-feature metrics derived from geometry and tags."""

from __future__ import annotations

from collections import Counter

import geopandas as gpd
from shapely.geometry.base import BaseGeometry

from .sources.base import OSM_CRS


def courtyard_count(geom: BaseGeometry) -> int:
    """Number of interior rings -- holes enclosed by the footprint.

    geom_type does not reveal these: a palace around a courtyard and a solid
    block are both `Polygon`. The difference is in `.interiors`.
    """
    if geom is None or geom.is_empty:
        return 0
    if geom.geom_type == "Polygon":
        return len(geom.interiors)
    if geom.geom_type == "MultiPolygon":
        return sum(len(p.interiors) for p in geom.geoms)
    return 0


# building=* values that mean "people live here". Used to say how many of the
# *residential* buildings carry a flat count -- the only denominator that
# matters for occupancy questions; a church without building:flats is not a gap.
RESIDENTIAL = frozenset({
    "residential", "apartments", "house", "detached", "semidetached_house",
    "terrace", "dormitory", "houseboat",
})


def parse_flats(value) -> int | None:
    """building:flats as an int, or None when absent or unparseable.

    OSM is free text: "12", "12;14", "~10", "12 flats" all occur. Anything that
    is not a plain non-negative integer is treated as missing rather than
    guessed -- a wrong count is worse than no count.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("nan", "none"):
        return None
    if text.isdigit():
        return int(text)
    return None


def add_metrics(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Attach derived columns: courtyards, footprint area in m^2, flats.

    Area is computed on an equal-area reprojection. Computing it on raw
    EPSG:4326 coordinates yields square degrees, which are not a unit of area
    and vary with latitude.
    """
    out = gdf.copy()
    out["courtyards"] = out.geometry.apply(courtyard_count)

    if len(out):
        metric = out.to_crs(out.estimate_utm_crs())
        out["area_m2"] = metric.geometry.area.round(1)
    else:
        out["area_m2"] = []

    # building:flats -> integer column `flats` (None = not tagged / unparseable)
    src = out["building:flats"] if "building:flats" in out.columns else None
    out["flats"] = (src.map(parse_flats) if src is not None else None)
    out["flats"] = out["flats"].astype("Int64")

    return out


def flats_summary(gdf: gpd.GeoDataFrame) -> dict:
    """Coverage of building:flats -- how much of the stock we can actually count.

    Returns counts, not prose, so export can write the same numbers to meta.json.
    """
    n = int(len(gdf))
    if n == 0 or "flats" not in gdf.columns:
        return {"buildings": n, "with_flats_tag": 0, "flats_sum": 0}
    has = gdf["flats"].notna()
    is_res = gdf["building"].astype(str).str.lower().isin(RESIDENTIAL) if "building" in gdf.columns else has & False
    out = {
        "buildings": n,
        "with_flats_tag": int(has.sum()),
        "flats_sum": int(gdf.loc[has, "flats"].sum()),
        "residential_buildings": int(is_res.sum()),
        "residential_with_flats_tag": int((has & is_res).sum()),
        "residential_flats_sum": int(gdf.loc[has & is_res, "flats"].sum()),
    }
    if "building:flats" in gdf.columns:
        raw_present = gdf["building:flats"].notna() & (gdf["building:flats"].astype(str).str.strip() != "")
        out["unparseable_flats_tag"] = int((raw_present & ~has).sum())
    return out


def summarize(gdf: gpd.GeoDataFrame, tag: str = "building", top: int = 10) -> str:
    """Human-readable breakdown of a feature set."""
    if gdf.empty:
        return "  (no features)"

    lines = []
    if tag in gdf.columns:
        lines.append(f"  by {tag}:")
        for value, n in gdf[tag].value_counts().head(top).items():
            lines.append(f"      {str(value):20s} {n}")

    if "courtyards" in gdf.columns:
        with_yards = int((gdf["courtyards"] > 0).sum())
        total = int(gdf["courtyards"].sum())
        pct = 100 * with_yards / len(gdf)
        lines.append("")
        lines.append(f"  with courtyards : {with_yards} ({pct:.1f}%), {total} in total")

    if "area_m2" in gdf.columns and len(gdf):
        lines.append(f"  footprint m^2   : median {gdf['area_m2'].median():.0f}, "
                     f"max {gdf['area_m2'].max():.0f}")

    if "flats" in gdf.columns and len(gdf):
        f = flats_summary(gdf)
        pct_all = 100 * f["with_flats_tag"] / f["buildings"]
        lines.append("")
        lines.append(f"  building:flats  : tagged on {f['with_flats_tag']} of {f['buildings']} "
                     f"buildings ({pct_all:.1f}%), {f['flats_sum']} flats in total")
        if f.get("residential_buildings"):
            pct_res = 100 * f["residential_with_flats_tag"] / f["residential_buildings"]
            lines.append(f"                    residential only: {f['residential_with_flats_tag']} of "
                         f"{f['residential_buildings']} ({pct_res:.1f}%), "
                         f"{f['residential_flats_sum']} flats")
        if f.get("unparseable_flats_tag"):
            lines.append(f"                    unparseable values ignored: {f['unparseable_flats_tag']}")
    return "\n".join(lines)


# --------------------------------------------------------------------- POIs

def add_poi_metrics(
    gdf: gpd.GeoDataFrame, classes_path: str | None = None
) -> gpd.GeoDataFrame:
    """Join the service classification onto a POI result.

    Adds `category`, `audience` and `classified`. `classified` is recorded
    separately rather than inferred from category == "other": some values are
    deliberately classified *as* other (a bench is known, it is just not a
    service), and conflating the two would hide the pairs the table is actually
    missing.
    """
    from .poi_classes import classify, load_classes

    out = gdf.copy()
    if out.empty or "poi_key" not in out.columns:
        out["category"] = []
        out["audience"] = []
        out["classified"] = []
        return out

    table = load_classes(classes_path)
    pairs = list(zip(out["poi_key"], out["poi_value"]))
    out["category"] = [classify(k, v, table).category for k, v in pairs]
    out["audience"] = [classify(k, v, table).audience for k, v in pairs]
    out["classified"] = [
        (str(k).strip(), str(v).strip()) in table if k and v else False
        for k, v in pairs
    ]
    return out


def unclassified_pairs(gdf: gpd.GeoDataFrame) -> Counter:
    """(key, value) pairs the table does not cover, most frequent first.

    This is the worklist for extending poi_classes.csv.
    """
    if gdf.empty or "classified" not in gdf.columns:
        return Counter()
    missing = gdf[~gdf["classified"].astype(bool)]
    return Counter(
        f"{k}={v}" for k, v in zip(missing.get("poi_key", []), missing.get("poi_value", []))
    )


def poi_summary(gdf: gpd.GeoDataFrame) -> dict:
    """Counts behind summarize_pois, shaped for meta.json."""
    if gdf.empty:
        return {"pois": 0, "by_poi_key": {}, "by_category": {}, "by_audience": {},
                "unclassified": 0, "unclassified_pairs": {}}
    missing = unclassified_pairs(gdf)
    services = service_rows(gdf)
    return {
        "pois": int(len(gdf)),
        "by_poi_key": _counts(gdf, "poi_key"),
        "by_category": _counts(gdf, "category"),
        "by_audience": _counts(gdf, "audience"),
        # The POI keys also match street furniture: amenity=bench alone can
        # outnumber every shop in a quarter. Counting it is right -- it was
        # asked for -- but letting it into the audience shares would drown the
        # question those shares exist to answer, so they are reported twice.
        "services": int(len(services)),
        "by_audience_services": _counts(services, "audience"),
        "unclassified": int(sum(missing.values())),
        "unclassified_pairs": dict(missing.most_common(50)),
    }


def service_rows(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Rows that are an actual service.

    Excludes category=other, which is where knowingly-not-a-service features
    land: benches, bins, clocks, vacant units.
    """
    if gdf.empty or "category" not in gdf.columns:
        return gdf
    return gdf[gdf["category"] != "other"]


def _counts(gdf: gpd.GeoDataFrame, column: str) -> dict[str, int]:
    if column not in gdf.columns:
        return {}
    return {str(k): int(v) for k, v in gdf[column].value_counts().items()}


def summarize_pois(gdf: gpd.GeoDataFrame, top_unclassified: int = 15) -> str:
    """Human-readable breakdown of a POI result."""
    if gdf.empty:
        return "  (no POIs)"

    s = poi_summary(gdf)
    lines = [f"  services        : {s['pois']}", "", "  by tag key:"]
    for key, n in sorted(s["by_poi_key"].items(), key=lambda kv: -kv[1]):
        lines.append(f"      {key:18s} {n}")

    lines.append("")
    lines.append("  by category:")
    for cat, n in sorted(s["by_category"].items(), key=lambda kv: -kv[1]):
        lines.append(f"      {cat:18s} {n}")

    lines.append("")
    lines.append(f"  by audience (all {s['pois']} features):")
    for aud in ("everyday", "mixed", "tourist", "unknown"):
        n = s["by_audience"].get(aud, 0)
        lines.append(f"      {aud:18s} {n:5d}  ({100 * n / s['pois']:5.1f}%)")

    n_svc = s["services"]
    if n_svc and n_svc != s["pois"]:
        lines.append("")
        lines.append(
            f"  by audience ({n_svc} services only, excluding "
            f"{s['pois'] - n_svc} non-service features in category=other):"
        )
        for aud in ("everyday", "mixed", "tourist", "unknown"):
            n = s["by_audience_services"].get(aud, 0)
            lines.append(f"      {aud:18s} {n:5d}  ({100 * n / n_svc:5.1f}%)")

    everyday = s["by_audience_services"].get("everyday", 0)
    tourist = s["by_audience_services"].get("tourist", 0)
    if tourist:
        lines.append("")
        lines.append(
            f"  everyday : tourist = {everyday / tourist:.2f}  "
            f"({everyday} vs {tourist}, services only)"
        )

    missing = unclassified_pairs(gdf)
    lines.append("")
    if not missing:
        lines.append("  unclassified    : none")
    else:
        lines.append(
            f"  unclassified    : {sum(missing.values())} features "
            f"in {len(missing)} distinct (key, value) pairs"
        )
        for pair, n in missing.most_common(top_unclassified):
            lines.append(f"      {pair:34s} {n}")
    return "\n".join(lines)


def value_counts_by_key(gdf: gpd.GeoDataFrame) -> dict[str, dict[str, int]]:
    """Raw tally of every tag value, grouped by its key.

    No classification table involved: the values come from whatever is in the
    extract. That is the point -- a table can only report what someone thought
    to put in it, while this shows what the quarter actually contains,
    including values nobody anticipated.
    """
    if gdf.empty or "poi_key" not in gdf.columns:
        return {}
    out: dict[str, dict[str, int]] = {}
    for key, group in gdf.groupby("poi_key", sort=False):
        counts = Counter(str(v) for v in group["poi_value"])
        out[str(key)] = dict(counts.most_common())
    # most numerous key first, so the eye lands on what dominates
    return dict(sorted(out.items(), key=lambda kv: -sum(kv[1].values())))


def summarize_value_counts(gdf: gpd.GeoDataFrame, top: int | None = 20) -> str:
    """Per-key ranked value counts. `top` limits each key; None shows all."""
    tally = value_counts_by_key(gdf)
    if not tally:
        return "  (no POIs)"

    lines = []
    for key, values in tally.items():
        total = sum(values.values())
        shown = list(values.items())[: top] if top else list(values.items())
        lines.append(f"  {key} ({total} features, {len(values)} distinct values)")
        for value, n in shown:
            bar = "#" * min(40, round(40 * n / max(values.values())))
            lines.append(f"      {value:26s} {n:5d}  {bar}")
        if top and len(values) > top:
            rest = total - sum(n for _, n in shown)
            lines.append(f"      {'... ' + str(len(values) - top) + ' more values':26s} {rest:5d}")
        lines.append("")
    return "\n".join(lines).rstrip()
