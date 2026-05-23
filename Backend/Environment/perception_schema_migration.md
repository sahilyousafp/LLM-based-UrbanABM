# Perception Schema Migration Guide

When adding new categories or changing field names in the VLM image descriptions, you must update every layer in the pipeline below. The order matters — changes flow from VLM output → DB → Python dict → prompts → frontend.

---

## Tier 1 — Source of Truth (JSON Output)

**The VLM generates `scene_analysis` dict with these keys** (file: `Backend/Environment/output/results/*_analysis.json`)

```json
{
  "scene_analysis": {
    "scene_overview":    "...",
    "buildings":         "...",
    "materials":         "...",
    "building_condition":"...",
    "street_furniture":  "...",
    "vegetation":        "...",
    "signage":           "...",
    "ground_surfaces":   "...",
    "spatial_enclosure": "...",
    "pedestrian_activity":"...",
    "lighting_atmosphere":"...",
    "as_resident":       "...",
    "as_commuter":       "...",
    "as_tourist":        "...",
    "as_student":        "..."
  }
}
```

**Add new keys here** — this is where the new category name is born.

---

## Tier 2 — Database Schema

**File:** `Backend/Environment/overture_to_duckdb.py`

| Lines | What to change |
|---|---|
| `596–605` | Extract new field from `scene_analysis` JSON with `.get("new_field", "")` |
| `607–632` | Add new field to the `rows.append((...))` tuple |
| `658–668` | Add new column to `CREATE TABLE streetview_perception (...)` |
| `672–679` | Add new `?` to the `INSERT INTO` VALUES list |
| `522–529` | If new field needs aggregation from quadrant data, add extraction logic |

**If you rename a field**, change both the JSON key AND the DB column name to match.

---

## Tier 3 — Python Dict Mapping (Downstream Dict Keys)

**File:** `Backend/Agent/model.py:757–766`

This SQL `SELECT` and the Python mapping define **what key names the rest of the system sees**.

```python
SELECT 
    scene_overview, buildings, materials, building_condition,
    street_furniture, vegetation_text, signage, ground_surfaces,
    spatial_impression, pedestrian_activity, lighting_atmosphere,
    as_resident, as_commuter, as_tourist, as_student,
    latitude, longitude
```

```python
return {
    "scene_overview": result[0] or "",
    "buildings":      result[1] or "",
    ...
    "as_student":     result[14] or "",
}
```

**IMPORTANT:** Note the key naming inconsistency already present — the DB column is `vegetation_text` but the Python dict key is `vegetation` (via `scene_analysis.get("vegetation", "")` in the ETL). When you add new fields, choose one name convention and use it consistently across DB, Python dict, and JSON.

---

## Tier 4 — Validation Set

**File:** `Backend/LLM/Thinking/blocks/plan_block.py:36–39`

```python
VALID_PERCEPTION_KEYS = {
    "scene_overview", "buildings", "materials", "building_condition",
    "street_furniture", "vegetation", "signage", "ground_surfaces",
    "spatial_enclosure", "pedestrian_activity", "lighting_atmosphere",
    "as_resident", "as_commuter", "as_tourist", "as_student",
}
```

Used to validate `perception_preferences` and `perception_avoid` in plan JSON configs. **New fields must be added here**, or plan validation will reject them.

---

## Tier 5 — LLM Prompts

### Mobility prompt — `Backend/LLM/Thinking/prompts.py`

| Lines | Purpose |
|---|---|
| `61–71` | Scene fields rendered to LLM when making movement decisions |
| `173–177` | Similar block in another prompt variant |
| `230–235` | Another variant |

Each `scene_fields` list is a `(dict_key, label)` tuple. **Add new fields here** to include them in LLM context. If omitted, the LLM won't "see" the new data.

### Cognition prompt — `Backend/LLM/Thinking/blocks/cognition_block.py:45–53`

Same pattern — `(dict_key, label)` tuples. **Add new fields here** for cognitive state updates.

---

## Tier 6 — Frontend / API Responses

### Map server — `Backend/Agent/map_server.py:783–797`

Explicit dict mapping for GeoJSON feature properties served to the frontend map. **Add new keys here** to make them visible in the map UI.

### Agent Lab — `test/agent_lab_server.py`

| Lines | Purpose |
|---|---|
| `266–267` | Perception endpoint response |
| `676–680` | Another endpoint with specific field access |
| `906` | Iteration over specific keys |
| `923` | Scene display in diary/adherence |

---

## One-Time Caveats

| Detail | File | Lines |
|---|---|---|
| DB naming inconsistency: `vegetation_text` (DB) vs `vegetation` (dict) | `overture_to_duckdb.py:667`, `model.py:759` | Decide on a convention for new fields |
| DB naming inconsistency: `spatial_impression` (DB) vs `spatial_enclosure` (dict) | `overture_to_duckdb.py:653`, `map_server.py:791` | Pick one for new fields |
| `as_resident/commuter/tourist/student` keys must match the `perception_preferences` in plan JSON | `test/plans.json`, `Backend/LLM/Thinking/plans.json` | Archetype-specific fields used in `perception_preferences` and `perception_avoid` |

---

## Checklist for Adding a New Field Called `comfort_level`

```
☐ 1. Add to VLM output JSON: "comfort_level": "..."
☐ 2. overture_to_duckdb.py: extract (line ~605), append (line ~632), CREATE column (line ~668), INSERT ? (line ~678)
☐ 3. model.py: add to SELECT (line ~762) and dict mapping (line ~765)
☐ 4. plan_block.py: add to VALID_PERCEPTION_KEYS (line ~38)
☐ 5. prompts.py: add (key, label) to scene_fields in all 3 variants (lines ~71, ~177, ~235)
☐ 6. cognition_block.py: add (key, label) to scene_fields (line ~53)
☐ 7. map_server.py: add to GeoJSON properties (line ~797)
☐ 8. agent_lab_server.py: update any endpoints that filter/serve perception (lines 266, 676, 906, 923)
☐ 9. Re-run overture_to_duckdb.py to rebuild the DB table with the new column
```
