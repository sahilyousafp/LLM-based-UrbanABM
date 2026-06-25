"""
Street View perception data loader.

Reads VLM analysis JSON files from Backend/Environment/output/results/ and
loads them into a DuckDB `streetview_perception` table.

Supports two VLM output schemas:
  OLD — flat scene_analysis fields + quadrant_analysis grid
  NEW — structured scene_analysis arrays (lighting, spatial_character, etc.)
Both produce the same 39-column table so queries in model.py are unchanged.
"""

import json
from pathlib import Path

# ingestion/ → Environment/ → Backend/ → project root
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


def _aggregate_quadrant_field(quadrants, field_name, field_variants=None):
    """Extract a field across all 9 quadrants, handling inconsistent VLM output keys."""
    variants = [field_name] + (field_variants or [])
    values = []
    for qdata in quadrants:
        if not isinstance(qdata, dict):
            continue
        for key in variants:
            val = qdata.get(key)
            if val is not None and val != "unknown" and val != 0:
                if isinstance(val, list):
                    values.extend(v for v in val if v and v != 0)
                else:
                    values.append(val)
                break
    return values


def _is_new_schema(data: dict) -> bool:
    """Return True when scene_analysis uses structured array fields (new VLM schema)."""
    sa = data.get("scene_analysis")
    if not isinstance(sa, dict):
        return False
    return isinstance(sa.get("lighting"), list) or isinstance(sa.get("spatial_character"), list)


def _join_zones(items, *keys) -> str:
    """Summarize a list of per-zone dicts into a readable string.

    '; ' between zones, ', ' between the requested fields within a zone.
    Skips missing/empty/'unknown' values. Used by render_scene_analysis.
    """
    parts = []
    for item in items:
        if not isinstance(item, dict):
            continue
        vals = []
        for k in keys:
            v = item.get(k)
            if v is None:
                continue
            s = str(v).strip()
            if s and s.lower() != "unknown":
                vals.append(s)
        if vals:
            parts.append(", ".join(vals))
    # de-duplicate while preserving order (zones often repeat the same descriptor)
    seen = set()
    uniq = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return "; ".join(uniq)


def render_scene_analysis(scene_analysis: dict) -> dict:
    """Canonical renderer: the 7-category scene_analysis → readable strings.

    Single source of truth for turning the nested VLM scene_analysis (scene +
    6 per-zone arrays) into LLM-readable text. Reused by ingestion, the agent
    perception query fallback, and the agent-lab display. Returns exactly these
    7 keys (empty string when a category is absent):
      scene, lighting, spatial_character, crowdedness, greenery,
      street_amenities, visible_text
    """
    sa = scene_analysis or {}
    return {
        "scene": str(sa.get("scene", "") or "").strip()[:600],
        "lighting": _join_zones(sa.get("lighting", []), "element", "condition")[:500],
        "spatial_character": _join_zones(
            sa.get("spatial_character", []),
            "enclosure", "width", "architectural_style", "building_condition", "storefront_type",
        )[:600],
        "crowdedness": _join_zones(sa.get("crowdedness", []), "zone", "density_level")[:300],
        "greenery": _join_zones(sa.get("greenery", []), "element", "coverage")[:400],
        "street_amenities": _join_zones(sa.get("street_amenities", []), "element")[:400],
        "visible_text": _join_zones(sa.get("visible_text", []), "text")[:300],
    }


def _extract_new_schema_flat(data: dict) -> dict:
    """
    Transform new structured VLM JSON into a row dict for the streetview_perception
    table. Produces:
      - the 7 rendered category strings (cat_*) via render_scene_analysis (agent contract)
      - display scalars: walkability (0-10), has_vegetation (bool),
        pedestrian_activity (numeric 0-3), architectural_style, building_condition
      - the raw 7 category arrays preserved as *_json strings (audit/display)
      - metadata columns
    """
    meta = data.get("metadata") or {}
    sa = data.get("scene_analysis") or {}

    cats = render_scene_analysis(sa)
    scene = cats["scene"]

    sc_list = sa.get("spatial_character", [])
    styles = [(z.get("architectural_style") or "").strip().lower()
              for z in sc_list if isinstance(z, dict)]
    styles = [s for s in styles if s and s != "unknown"]
    architectural_style = max(set(styles), key=styles.count) if styles else None

    conditions = [(z.get("building_condition") or "").strip().lower()
                  for z in sc_list if isinstance(z, dict)]
    conditions = [c for c in conditions if c and c != "unknown"]
    building_condition = max(set(conditions), key=conditions.count) if conditions else None

    # crowdedness → numeric pedestrian_activity (0–3) for the map display only
    crowd_list = sa.get("crowdedness", [])
    _density_map = {
        "empty": 0.0, "sparse": 0.5, "light": 1.0, "few": 1.0,
        "moderate": 2.0, "medium": 2.0, "busy": 3.0, "crowded": 3.0, "dense": 3.0,
    }
    density_levels = [(c.get("density_level") or "").lower()
                      for c in crowd_list if isinstance(c, dict)]
    ped_numeric = [_density_map[d] for d in density_levels if d in _density_map]
    avg_pedestrian = round(sum(ped_numeric) / len(ped_numeric), 1) if ped_numeric else None

    # greenery → has_vegetation (bool) for the map display
    greenery_list = sa.get("greenery", [])
    has_vegetation = any(
        (g.get("coverage") or "").lower() not in ("none", "unknown", "no", "")
        for g in greenery_list if isinstance(g, dict)
    )

    # walkability derived from passability × lane_type weights (0–10 scale)
    _passability_score = {"clear": 1.0, "obstructed": 0.4, "blocked": 0.1}
    _lane_weight = {
        "sidewalk": 1.0, "pedestrian": 1.0, "footway": 1.0,
        "main_roadway": 0.5, "bike_lane": 0.6, "service": 0.7,
    }
    walk_scores = []
    for z in sc_list:
        if not isinstance(z, dict):
            continue
        p = _passability_score.get(z.get("passability", "").lower(), 0.7)
        lw = _lane_weight.get(z.get("lane_type", "").lower(), 0.7)
        walk_scores.append(p * lw * 10)
    avg_walkability = round(sum(walk_scores) / len(walk_scores), 1) if walk_scores else None

    return {
        # display scalars
        "walkability": avg_walkability,
        "has_vegetation": has_vegetation,
        "pedestrian_activity": avg_pedestrian,
        "architectural_style": architectural_style,
        "building_condition": building_condition,
        # scene text (kept for display/backward-compat)
        "scene_overview": scene,
        "scene_text": scene,
        "scene_narrative": scene,
        # metadata
        "street_name": meta.get("street_name", "") or "",
        "highway_type": meta.get("highway_type", "") or "",
        "edge_id": meta.get("edge_id", "") or "",
        "device": meta.get("device", "") or "",
        "latency_ms": float(meta.get("latency_ms", 0) or 0),
        # 7 rendered category strings (agent contract)
        "cat_scene": cats["scene"],
        "cat_lighting": cats["lighting"],
        "cat_spatial_character": cats["spatial_character"],
        "cat_crowdedness": cats["crowdedness"],
        "cat_greenery": cats["greenery"],
        "cat_street_amenities": cats["street_amenities"],
        "cat_visible_text": cats["visible_text"],
        # raw category arrays preserved (audit/display)
        "lighting_json": json.dumps(sa.get("lighting", [])),
        "spatial_character_json": json.dumps(sc_list),
        "crowdedness_json": json.dumps(crowd_list),
        "greenery_json": json.dumps(greenery_list),
        "street_amenities_json": json.dumps(sa.get("street_amenities", [])),
        "visible_text_json": json.dumps(sa.get("visible_text", [])),
        "nearby_landmarks_json": json.dumps(data.get("nearby_landmarks", [])),
    }


# Canonical non-geometry column order. write_perception_rows builds each row's
# value list from a dict using exactly this order, and the INSERT lists these
# columns explicitly — so adding/removing a column can never silently misalign.
_DATA_COLUMNS = [
    "latitude", "longitude",
    "walkability", "has_vegetation", "pedestrian_activity",
    "architectural_style", "building_condition", "source_image",
    "scene_narrative", "heading", "timestamp_str", "model_name",
    "scene_overview", "scene_text",
    "street_name", "highway_type", "edge_id", "device", "latency_ms",
    # 7 rendered category strings (agent contract)
    "cat_scene", "cat_lighting", "cat_spatial_character", "cat_crowdedness",
    "cat_greenery", "cat_street_amenities", "cat_visible_text",
    # raw category arrays (audit/display)
    "lighting_json", "spatial_character_json", "crowdedness_json",
    "greenery_json", "street_amenities_json", "visible_text_json",
    "nearby_landmarks_json",
]

_CREATE_TABLE_SQL = """
    CREATE OR REPLACE TABLE streetview_perception (
        latitude DOUBLE, longitude DOUBLE, geometry GEOMETRY,
        walkability DOUBLE, has_vegetation BOOLEAN, pedestrian_activity DOUBLE,
        architectural_style VARCHAR, building_condition VARCHAR, source_image VARCHAR,
        scene_narrative VARCHAR, heading DOUBLE, timestamp_str VARCHAR, model_name VARCHAR,
        scene_overview VARCHAR, scene_text VARCHAR,
        street_name VARCHAR, highway_type VARCHAR, edge_id VARCHAR,
        device VARCHAR, latency_ms DOUBLE,
        cat_scene VARCHAR, cat_lighting VARCHAR, cat_spatial_character VARCHAR,
        cat_crowdedness VARCHAR, cat_greenery VARCHAR, cat_street_amenities VARCHAR,
        cat_visible_text VARCHAR,
        lighting_json VARCHAR, spatial_character_json VARCHAR,
        crowdedness_json VARCHAR, greenery_json VARCHAR,
        street_amenities_json VARCHAR, visible_text_json VARCHAR,
        nearby_landmarks_json VARCHAR
    )
"""

# geometry is inserted from latitude/longitude via ST_Point(lon, lat); every other
# column maps 1:1 to _DATA_COLUMNS in order.
_INSERT_SQL = """
    INSERT INTO streetview_perception (latitude, longitude, geometry, {cols})
    VALUES (?, ?, ST_Point(?, ?), {ph})
""".format(
    cols=", ".join(c for c in _DATA_COLUMNS if c not in ("latitude", "longitude")),
    ph=", ".join(["?"] * (len(_DATA_COLUMNS) - 2)),
)


def collect_perception_rows(results_dir=None):
    """Parse all *_analysis.json files and return a list of row tuples.

    Pure Python / file I/O — no DuckDB. Safe to run in a thread executor.
    """
    if results_dir is None:
        results_dir = PROJECT_ROOT / "Backend" / "Environment" / "output" / "results"
    results_dir = Path(results_dir)
    if not results_dir.is_dir():
        return []

    rows = []
    for fp in sorted(results_dir.glob("*_analysis.json")):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(data, dict):
            continue

        meta = data.get("metadata") or {}
        lat = meta.get("latitude")
        lon = meta.get("longitude")
        if lat is None or lon is None:
            continue

        heading = float(meta.get("heading", 0.0) or 0.0)
        timestamp_str = str(meta.get("timestamp", "") or "")
        model_name = str(meta.get("model", "") or "")
        source_image = meta.get("source_image", "")

        # ── NEW SCHEMA (structured arrays) ───────────────────────────────
        if _is_new_schema(data):
            row = _extract_new_schema_flat(data)
            row.update({
                "latitude": lat, "longitude": lon,
                "heading": heading, "timestamp_str": timestamp_str,
                "model_name": model_name, "source_image": source_image,
            })
            rows.append(row)
            continue

        # ── OLD SCHEMA (quadrant_analysis + flat scene_analysis) ─────────
        qa = data.get("quadrant_analysis") or {}
        parse_failed = bool(qa.get("_parse_error"))

        avg_walkability = avg_pedestrian = dominant_style = dominant_condition = None
        has_vegetation = None
        scene_narrative = dominant_materials = dominant_furniture = spatial_impression = None

        if not parse_failed:
            quadrant_values = [v for k, v in qa.items()
                               if isinstance(v, dict) and not k.startswith("_")]

            walk_scores = _aggregate_quadrant_field(quadrant_values, "walkability_score",
                                                    ["walkability score"])
            numeric_scores = [s for s in walk_scores if isinstance(s, (int, float))]
            avg_walkability = round(sum(numeric_scores) / len(numeric_scores), 1) \
                if numeric_scores else None

            veg_vals = _aggregate_quadrant_field(quadrant_values, "vegetation", [" vegetation"])
            has_vegetation = any(
                v and isinstance(v, str) and v.lower() not in ("none", "unknown", "no", "0")
                for v in veg_vals)

            ped_vals = _aggregate_quadrant_field(
                quadrant_values, "pedestrian_activity",
                ["pedestrian activity", "pedestrianActivity"])
            ped_str_vals = [v.lower() for v in ped_vals
                            if isinstance(v, str) and v.lower() not in ("unknown",)]
            ped_map = {"low": 1, "moderate": 2, "medium": 2, "high": 3}
            ped_numeric = [ped_map.get(v, 0) for v in ped_str_vals if v in ped_map]
            avg_pedestrian = round(sum(ped_numeric) / len(ped_numeric), 1) \
                if ped_numeric else None

            style_vals = _aggregate_quadrant_field(
                quadrant_values, "architectural_style",
                ["building_typology", "building style", "architectural style"])
            style_strs = [s.lower().strip() for s in style_vals
                          if isinstance(s, str) and s.lower() not in ("unknown",)]
            dominant_style = max(set(style_strs), key=style_strs.count) if style_strs else None

            cond_vals = _aggregate_quadrant_field(quadrant_values, "condition")
            cond_strs = [c.lower().strip() for c in cond_vals
                         if isinstance(c, str) and c.lower() not in ("unknown",)]
            dominant_condition = max(set(cond_strs), key=cond_strs.count) if cond_strs else None

            center = qa.get("center_center", {})
            center_narrative = center.get("narrative") if isinstance(center, dict) else None
            if center_narrative and center_narrative.lower() not in ("unknown", ""):
                scene_narrative = center_narrative[:600]
            else:
                narratives = [
                    v.get("narrative", "") for v in qa.values()
                    if isinstance(v, dict)
                    and v.get("narrative", "").lower() not in ("unknown", "")
                ]
                if narratives:
                    scene_narrative = " | ".join(narratives[:3])[:600]

            mat_vals = _aggregate_quadrant_field(quadrant_values, "materials")
            unique_mats = list(dict.fromkeys(
                m.strip().lower() for m in mat_vals
                if isinstance(m, str) and m.strip().lower() not in ("unknown", "")
            ))
            if unique_mats:
                dominant_materials = ", ".join(unique_mats[:6])

            furn_vals = _aggregate_quadrant_field(quadrant_values, "street_furniture")
            unique_furn = list(dict.fromkeys(
                f.strip().lower() for f in furn_vals
                if isinstance(f, str) and f.strip().lower() not in ("unknown", "")
            ))
            if unique_furn:
                dominant_furniture = ", ".join(unique_furn[:6])

            sp = center.get("spatial_impression") if isinstance(center, dict) else None
            if sp and sp.lower() not in ("unknown", ""):
                spatial_impression = sp
            else:
                sp_vals = _aggregate_quadrant_field(quadrant_values, "spatial_impression")
                if sp_vals:
                    spatial_impression = sp_vals[0]

        # Old schema is deprecated. Map its flat scene_analysis fields onto the
        # cat_* contract best-effort so any legacy files still yield usable text;
        # the raw *_json arrays don't exist for old rows, so emit "[]".
        sa = data.get("scene_analysis") or {}
        scene_ov = sa.get("scene_overview", "") or ""
        rows.append({
            "latitude": lat, "longitude": lon,
            "walkability": avg_walkability, "has_vegetation": has_vegetation,
            "pedestrian_activity": avg_pedestrian,
            "architectural_style": dominant_style, "building_condition": dominant_condition,
            "source_image": source_image,
            "scene_narrative": scene_narrative, "heading": heading,
            "timestamp_str": timestamp_str, "model_name": model_name,
            "scene_overview": scene_ov, "scene_text": scene_ov,
            "street_name": "", "highway_type": "", "edge_id": "", "device": "", "latency_ms": 0.0,
            "cat_scene": scene_ov,
            "cat_lighting": sa.get("lighting_atmosphere", "") or "",
            "cat_spatial_character": sa.get("spatial_enclosure", "") or sa.get("buildings", "") or "",
            "cat_crowdedness": str(sa.get("pedestrian_activity", "") or ""),
            "cat_greenery": sa.get("vegetation", "") or "",
            "cat_street_amenities": sa.get("street_furniture", "") or "",
            "cat_visible_text": sa.get("signage", "") or "",
            "lighting_json": "[]", "spatial_character_json": "[]", "crowdedness_json": "[]",
            "greenery_json": "[]", "street_amenities_json": "[]", "visible_text_json": "[]",
            "nearby_landmarks_json": "[]",
        })

    return rows


def write_perception_rows(duckdb_con, rows: list) -> int:
    """Write pre-collected rows into the streetview_perception DuckDB table.

    Uses the provided connection — caller must ensure it is writable.
    Returns the number of rows inserted.
    """
    if not rows:
        return 0

    def _vals(d: dict) -> list:
        lat, lon = d["latitude"], d["longitude"]
        # matches _INSERT_SQL: latitude, longitude, ST_Point(lon, lat), then
        # every other _DATA_COLUMNS value in order.
        out = [lat, lon, lon, lat]
        out.extend(d.get(c) for c in _DATA_COLUMNS if c not in ("latitude", "longitude"))
        return out

    duckdb_con.execute(_CREATE_TABLE_SQL)
    duckdb_con.executemany(_INSERT_SQL, [_vals(d) for d in rows])
    count = duckdb_con.execute("SELECT COUNT(*) FROM streetview_perception").fetchone()[0]
    print(f"[OK] Loaded {count:,} street view perception points")
    return count


def load_streetview_perception(duckdb_con) -> bool:
    """Load VLM street view analysis JSONs into DuckDB (backward-compatible entry point)."""
    rows = collect_perception_rows()
    if not rows:
        print("[WARN] No valid street view perception data found")
        return False
    write_perception_rows(duckdb_con, rows)
    return True
