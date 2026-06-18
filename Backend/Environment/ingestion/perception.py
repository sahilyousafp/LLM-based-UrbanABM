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
    sa = data.get("scene_analysis", {})
    return isinstance(sa.get("lighting"), list) or isinstance(sa.get("spatial_character"), list)


def _extract_new_schema_flat(data: dict) -> dict:
    """
    Flatten new structured VLM JSON into the flat field set expected by the old schema,
    and additionally return all new structured JSON columns.
    Mapping:
      scene_analysis.scene            → scene_overview / scene_text
      lighting[]                      → lighting_atmosphere
      spatial_character[]             → architectural_style, building_condition,
                                        spatial_impression, buildings
      crowdedness[]                   → pedestrian_activity (numeric 0–3)
      greenery[]                      → vegetation_text / has_vegetation
      street_amenities[]              → street_furniture
      visible_text[]                  → signage
    """
    meta = data.get("metadata", {})
    sa = data.get("scene_analysis", {})

    scene_overview = sa.get("scene", "")

    # lighting_atmosphere
    lighting_list = sa.get("lighting", [])
    lighting_parts = [
        f"{l.get('element', '')} ({l.get('condition', '')})"
        for l in lighting_list if isinstance(l, dict)
    ]
    lighting_atmosphere = ", ".join(p for p in lighting_parts if p.strip("() "))[:500]

    # spatial_character → architectural_style, building_condition, spatial_impression, buildings
    sc_list = sa.get("spatial_character", [])
    styles = [(z.get("architectural_style") or "").strip().lower()
              for z in sc_list if isinstance(z, dict)]
    styles = [s for s in styles if s and s != "unknown"]
    architectural_style = max(set(styles), key=styles.count) if styles else None

    conditions = [(z.get("building_condition") or "").strip().lower()
                  for z in sc_list if isinstance(z, dict)]
    conditions = [c for c in conditions if c and c != "unknown"]
    building_condition = max(set(conditions), key=conditions.count) if conditions else None

    enclosures = [z.get("enclosure", "") for z in sc_list
                  if isinstance(z, dict) and z.get("enclosure", "")]
    widths = [z.get("width", "") for z in sc_list
              if isinstance(z, dict) and z.get("width", "")]
    sp_parts = []
    if enclosures:
        sp_parts.append("Enclosure: " + ", ".join(enclosures))
    if widths:
        sp_parts.append("Width: " + ", ".join(widths))
    spatial_impression = " | ".join(sp_parts)[:300] if sp_parts else None

    storefronts = list(dict.fromkeys(
        (z.get("storefront_type") or "").strip().lower()
        for z in sc_list if isinstance(z, dict)
        if (z.get("storefront_type") or "").strip().lower() not in ("", "unknown")
    ))
    arch_details = list(dict.fromkeys(
        (z.get("architectural_details") or "").strip()
        for z in sc_list if isinstance(z, dict)
        if (z.get("architectural_details") or "").strip().lower() not in ("", "unknown")
    ))
    buildings_parts = storefronts[:4]
    if arch_details:
        buildings_parts.append(arch_details[0])
    buildings_text = " | ".join(buildings_parts)[:400] if buildings_parts else ""

    # crowdedness → pedestrian_activity (numeric 0–3)
    crowd_list = sa.get("crowdedness", [])
    _density_map = {
        "empty": 0.0, "sparse": 0.5, "light": 1.0, "few": 1.0,
        "moderate": 2.0, "medium": 2.0, "busy": 3.0, "crowded": 3.0, "dense": 3.0,
    }
    density_levels = [(c.get("density_level") or "").lower()
                      for c in crowd_list if isinstance(c, dict)]
    ped_numeric = [_density_map[d] for d in density_levels if d in _density_map]
    avg_pedestrian = round(sum(ped_numeric) / len(ped_numeric), 1) if ped_numeric else None

    # greenery
    greenery_list = sa.get("greenery", [])
    has_vegetation = any(
        (g.get("coverage") or "").lower() not in ("none", "unknown", "no", "")
        for g in greenery_list if isinstance(g, dict)
    )
    veg_parts = [
        f"{g.get('element', '')} ({g.get('coverage', '')})"
        for g in greenery_list if isinstance(g, dict)
        if (g.get("coverage") or "").lower() not in ("none", "unknown", "no", "")
    ]
    vegetation_text = ", ".join(veg_parts)[:300]

    # street_amenities → street_furniture
    amenity_list = sa.get("street_amenities", [])
    furniture_parts = list(dict.fromkeys(
        (a.get("element") or "").strip().lower()
        for a in amenity_list if isinstance(a, dict)
        if (a.get("element") or "").strip().lower() not in ("", "unknown")
    ))
    street_furniture = ", ".join(furniture_parts[:6])

    # visible_text → signage
    vt_list = sa.get("visible_text", [])
    texts = [vt.get("text", "") for vt in vt_list
             if isinstance(vt, dict) and vt.get("text", "")]
    signage = ", ".join(texts)[:300]

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
        # legacy flat fields (keep column names identical to old schema)
        "scene_overview": scene_overview,
        "buildings": buildings_text,
        "materials": "",
        "building_condition": building_condition,
        "street_furniture": street_furniture,
        "vegetation_text": vegetation_text,
        "signage": signage,
        "ground_surfaces": "",
        "spatial_impression": spatial_impression,
        "pedestrian_activity": avg_pedestrian,
        "lighting_atmosphere": lighting_atmosphere,
        "as_resident": "",
        "as_commuter": "",
        "as_tourist": "",
        "as_student": "",
        "walkability": avg_walkability,
        "has_vegetation": has_vegetation,
        "architectural_style": architectural_style,
        # new structured columns
        "street_name": meta.get("street_name", "") or "",
        "highway_type": meta.get("highway_type", "") or "",
        "edge_id": meta.get("edge_id", "") or "",
        "device": meta.get("device", "") or "",
        "latency_ms": float(meta.get("latency_ms", 0) or 0),
        "scene_text": scene_overview,
        "lighting_json": json.dumps(lighting_list),
        "spatial_character_json": json.dumps(sc_list),
        "crowdedness_json": json.dumps(crowd_list),
        "greenery_json": json.dumps(greenery_list),
        "street_amenities_json": json.dumps(amenity_list),
        "visible_text_json": json.dumps(vt_list),
        "nearby_landmarks_json": json.dumps(data.get("nearby_landmarks", [])),
    }


_CREATE_TABLE_SQL = """
    CREATE OR REPLACE TABLE streetview_perception (
        latitude DOUBLE, longitude DOUBLE, geometry GEOMETRY,
        walkability DOUBLE, has_vegetation BOOLEAN, pedestrian_activity DOUBLE,
        architectural_style VARCHAR, building_condition VARCHAR, source_image VARCHAR,
        scene_narrative VARCHAR, materials VARCHAR, street_furniture VARCHAR,
        spatial_impression VARCHAR, heading DOUBLE, timestamp_str VARCHAR, model_name VARCHAR,
        scene_overview VARCHAR, buildings VARCHAR, signage VARCHAR, ground_surfaces VARCHAR,
        lighting_atmosphere VARCHAR, as_resident VARCHAR, as_commuter VARCHAR,
        as_tourist VARCHAR, as_student VARCHAR, vegetation_text VARCHAR,
        street_name VARCHAR, highway_type VARCHAR, edge_id VARCHAR,
        device VARCHAR, latency_ms DOUBLE, scene_text VARCHAR,
        lighting_json VARCHAR, spatial_character_json VARCHAR,
        crowdedness_json VARCHAR, greenery_json VARCHAR,
        street_amenities_json VARCHAR, visible_text_json VARCHAR,
        nearby_landmarks_json VARCHAR
    )
"""

_INSERT_SQL = """
    INSERT INTO streetview_perception VALUES (
        ?, ?, ST_Point(?, ?),
        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
    )
"""


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

        meta = data.get("metadata", {})
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
            f = _extract_new_schema_flat(data)
            rows.append((
                lat, lon,
                f["walkability"], f["has_vegetation"], f["pedestrian_activity"],
                f["architectural_style"], f["building_condition"],
                source_image,
                f["scene_overview"],        # scene_narrative reuses scene_overview
                f["materials"], f["street_furniture"], f["spatial_impression"],
                heading, timestamp_str, model_name,
                f["scene_overview"],        # scene_overview column
                f["buildings"],
                f["signage"],
                f["ground_surfaces"],
                f["lighting_atmosphere"],
                f["as_resident"], f["as_commuter"], f["as_tourist"], f["as_student"],
                f["vegetation_text"],
                # new columns
                f["street_name"], f["highway_type"], f["edge_id"],
                f["device"], f["latency_ms"], f["scene_text"],
                f["lighting_json"], f["spatial_character_json"],
                f["crowdedness_json"], f["greenery_json"],
                f["street_amenities_json"], f["visible_text_json"],
                f["nearby_landmarks_json"],
            ))
            continue

        # ── OLD SCHEMA (quadrant_analysis + flat scene_analysis) ─────────
        qa = data.get("quadrant_analysis", {})
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

        sa = data.get("scene_analysis") or {}
        rows.append((
            lat, lon,
            avg_walkability, has_vegetation, avg_pedestrian,
            dominant_style, dominant_condition,
            source_image,
            scene_narrative, dominant_materials, dominant_furniture, spatial_impression,
            heading, timestamp_str, model_name,
            sa.get("scene_overview", ""),
            sa.get("buildings", ""),
            sa.get("signage", ""),
            sa.get("ground_surfaces", ""),
            sa.get("lighting_atmosphere", ""),
            sa.get("as_resident", ""),
            sa.get("as_commuter", ""),
            sa.get("as_tourist", ""),
            sa.get("as_student", ""),
            sa.get("vegetation", ""),
            # new columns — empty for old schema rows
            "", "", "", "", 0.0, "",
            "[]", "[]", "[]", "[]", "[]", "[]", "[]",
        ))

    return rows


def write_perception_rows(duckdb_con, rows: list) -> int:
    """Write pre-collected rows into the streetview_perception DuckDB table.

    Uses the provided connection — caller must ensure it is writable.
    Returns the number of rows inserted.
    """
    if not rows:
        return 0
    duckdb_con.execute(_CREATE_TABLE_SQL)
    duckdb_con.executemany(_INSERT_SQL, [
        [r[0], r[1], r[1], r[0],
         r[2], r[3], r[4], r[5], r[6], r[7],
         r[8], r[9], r[10], r[11], r[12], r[13], r[14],
         r[15], r[16], r[17], r[18], r[19], r[20], r[21], r[22], r[23], r[24],
         r[25], r[26], r[27], r[28], r[29], r[30],
         r[31], r[32], r[33], r[34], r[35], r[36], r[37]]
        for r in rows
    ])
    count = duckdb_con.execute("SELECT COUNT(*) FROM streetview_perception").fetchone()[0]
    print(f"✓ Loaded {count:,} street view perception points")
    return count


def load_streetview_perception(duckdb_con) -> bool:
    """Load VLM street view analysis JSONs into DuckDB (backward-compatible entry point)."""
    rows = collect_perception_rows()
    if not rows:
        print("✗ No valid street view perception data found")
        return False
    write_perception_rows(duckdb_con, rows)
    return True
