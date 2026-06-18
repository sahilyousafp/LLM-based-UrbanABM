# 06 — Spatial Reasoning

This document explains how the pedestrian street graph is built, how Dijkstra pathfinding works, how candidate edges are assembled, and how VLM-analysed street scenes feed into agent decisions.

**Key files:**
- `Backend/Agent/model/city_model.py` — graph loading, Dijkstra, candidate edges, perception query
- `Backend/Agent/routers/streetview.py` — streetview download, VLM analysis, reimport endpoints
- `Backend/LLM/Thinking/prompts.py` — how perception data is formatted for LLM
- `Backend/Environment/ingestion/perception.py` — VLM JSON → DuckDB loader (`collect_perception_rows`, `write_perception_rows`)

---

## Street Graph Loading

At `CityModel.__init__()`, all `walk_edges` from DuckDB are loaded into two Python data structures:

```python
# 1. Edge lookup by ID
self.edges: dict[int, LineString]
#   {1234: LineString([(2.161, 41.385), (2.162, 41.386)]), ...}

# 2. Node-to-edges adjacency (the movement graph)
self.node_to_edges: dict[tuple, list]
#   {(2.162, 41.386): [(1234, geom, "backward"), (5678, geom, "forward"), ...]}
```

Each entry in `node_to_edges` maps a junction point `(lon, lat)` to all edges connected to it — with their direction. **This is how agents find their next move:** look up the node at the end of the current edge, get all outgoing edges.

For `routing_mode='footway'`, a filtered copy `node_to_ped_edges` includes only edges with `road_class IN ('footway', 'pedestrian', 'path', 'steps')`.

---

## Dijkstra Pathfinding

### Precomputation

At model init, Dijkstra's shortest-path algorithm is precomputed for the entire Eixample walk graph. This is done **once** — not per step — making route lookups O(1) at runtime.

```python
def _precompute_dijkstra_graph(self):
    """
    Builds: self.dijkstra_graph
    Shape:  {start_node: {end_node: (distance, next_hop_node)}}
    """
    # For each node, run Dijkstra over the walk graph
    # Edge weight = LineString.length (Euclidean, degrees → metres via *111320)
    ...

def dijkstra_next_node(self, current_node, target_node) -> tuple | None:
    """O(1) lookup: next hop from current to target."""
    return self.dijkstra_graph[current_node][target_node].next_hop

def dijkstra_hops(self, current_node, target_node) -> int | None:
    """Count of hops (edges) from current to target."""
    ...
```

### How Agents Use Dijkstra

```
Agent has destination → destination has target_node (nearest walk junction)
         │
         ▼
At each step where needs_new_edge:
    next_node = model.dijkstra_next_node(current_node, target_node)
         │
         ▼
    Find edge in node_to_edges that leads to next_node
    → dijkstra_edge_id, dijkstra_edge_direction, dijkstra_edge_data
         │
         ▼
    If explore_budget exhausted: FORCE this edge (no LLM)
    If still exploring: pass as path_hint to LLM prompt
                        (shown as [SHORTEST PATH TO DESTINATION])
```

### Proposed Path

When an agent gets a new destination, a full path is precomputed:

```python
def compute_proposed_path(self, start_node, target_node) -> dict:
    """
    Returns: {
        "nodes": [(lon,lat), ...],    # full route as node sequence
        "total_distance": float,       # metres
        "created_at_step": int
    }
    """
```

This path is stored in `memory.status["proposed_path"]` and used to track **path adherence** (`GET /api/agent/{id}/path-adherence`).

---

## Candidate Edge Assembly

`CityAgent._get_candidate_edges()` builds the list of choices for MobilityBlock:

```python
def _get_candidate_edges(self) -> list[dict]:
    # 1. Get all edges from current end node
    raw = node_to_edges.get(current_end_node, [])

    # 2. Anti-backtrack filter
    candidates = [e for e in raw
                  if e[0] != current_edge_id        # no U-turn
                  and e[0] != previous_edge_id]     # no immediate backtrack

    # 3. Annotate each candidate
    for edge_id, geom, direction in candidates:
        # Nearby amenities (within 50m of edge midpoint)
        nearby = [a for a in nearby_amenities
                  if distance(edge_midpoint, a) < 50]

        # Perception (nearest streetview point within 80m of edge midpoint)
        perception = nearest_streetview(edge_midpoint)

        yield {
            "edge_id":    edge_id,
            "direction":  direction,
            "geom":       geom,
            "amenities":  [a["type"] for a in nearby],
            "perception": perception.get("scene_overview", ""),
            "description": f"{direction} along {edge_name}",
        }
```

The resulting list (up to 8 candidates) is what the LLM sees when choosing where to walk.

---

## Navigation Modes

The `nav_mode` setting (per-archetype, from `plans.json`) controls how strongly GPS routing is enforced:

| Mode | Behaviour | Archetype default |
|------|-----------|-------------------|
| `"gps"` | `[SHORTEST PATH TO DESTINATION]` label shown; MUST be chosen if present | commuter |
| `"direction_sense"` | Compass bearing toward destination shown; LLM uses as soft guidance | — |
| `"both"` | GPS label + compass bearing shown | resident, tourist, student |
| `"none"` | No directional hints in prompt | — |

---

## VLM Perception Pipeline

The perception pipeline gives agents **real visual context** about each street. It runs offline (not per simulation step) and stores results in DuckDB.

### Step 1: Street View Image Download

```
POST /api/streetview/download
    body: {"zone_bbox": [w, s, e, n], "spacing_m": 200}
    
→ Background job:
    1. Sample walk edges at 200m intervals
    2. For each point: call Google Street View Static API
       GET https://maps.googleapis.com/maps/api/streetview?
           location={lat},{lon}&heading={bearing}&size=640x640&key={KEY}
    3. Save JPEG to Backend/Environment/output/images/{lat}_{lon}.jpg
    
→ Returns: {"job_id": "...", "total_candidates": N}

GET /api/streetview/download/status/{job_id}
→ {"status": "running", "completed": N, "total": M}
```

### Step 2: VLM Analysis

```
POST /api/streetview/analyze
    body: {"model": "Qwen/Qwen2.5-VL-3B-Instruct", "images": [...]}
    
→ Background job:
    For each image:
        Load from disk
        Call Qwen2.5-VL with structured prompt:
            "Describe this Barcelona street scene for a pedestrian simulation.
             Return JSON with fields: scene_overview, buildings, vegetation,
             pedestrian_activity, lighting_atmosphere, spatial_enclosure,
             as_resident, as_tourist, as_commuter, as_student"
        
        Save result JSON to Backend/Environment/output/results/{lat}_{lon}_analysis.json
        Insert into DuckDB streetview_perception table
```

### Step 3: Agent Lookup

Each simulation step, agents query the nearest perception point via `CityModel.get_nearby_perception()`:

```python
# Uses self.perception_con — the dedicated read-write connection to perception.duckdb
# (completely separate from self.con which holds eixample_overture.duckdb)
result = self.perception_con.execute("""
    SELECT scene_overview, buildings, materials, building_condition,
           street_furniture, vegetation_text, signage, ground_surfaces,
           spatial_impression, pedestrian_activity, lighting_atmosphere,
           as_resident, as_commuter, as_tourist, as_student,
           latitude, longitude
    FROM streetview_perception
    WHERE ST_DWithin(geometry, ST_GeomFromText('POINT (? ?)'), 0.0015)
    ORDER BY ST_Distance(geometry, ST_GeomFromText('POINT (? ?)'))
    LIMIT 1
""").fetchone()
```

If the DuckDB query returns nothing (e.g., table not yet populated), `get_nearby_perception()` falls back to a JSON file cache (`self._sv_cache`) built at startup from the same `output/results/*.json` files.

### Step 4: Runtime Reimport

After running new VLM analysis, call `POST /api/streetview/reimport-perception` to refresh `perception.duckdb` without restarting the server. The endpoint:
1. Parses all `*_analysis.json` files in a thread (pure file I/O)
2. Drops and recreates the `streetview_perception` table via `self.perception_con`
3. Returns `{"ok": true, "records_in_table": N}`

### Perception Data in Prompts

The 13 perception fields are formatted into the LLM prompt:

```
Street environment at current location:
  Scene: Modernist residential block, wide pavement, trees in bloom
  Buildings: 5-storey Eixample buildings with ornate facades
  Vegetation: Mature plane trees lining both sides
  Pedestrian activity: Moderate foot traffic, cafe tables outside
  Lighting/atmosphere: Morning light, warm, pleasant
  Resident perspective: Familiar stretch near the market, quiet at this hour
  Tourist perspective: Classic Eixample architecture, worth pausing to look up
```

> **Note — agents do not "fetch" perception themselves.** The Python layer runs `get_nearby_perception()` *before* the LLM call and embeds the result as the text above. The LLM never queries the database; it reasons over the snapshot it is handed. For the full data-access model (context injection vs. tool-use, per-source table, and the unused `tools=` scaffold), see [`05_llm_integration.md` → "How Agents Access External Data"](05_llm_integration.md#how-agents-access-external-data).

---

## Archetype-Specific Street Reading

One of the unique features of this system: the same physical street is described **differently** for each archetype. Qwen2.5-VL generates four perspectives per capture point:

| Field | What it captures |
|-------|----------------|
| `as_resident` | Familiarity cues, local knowledge ("the bakery is around the corner") |
| `as_tourist` | Photogenic elements, notable buildings, cultural interest |
| `as_commuter` | Efficiency, directness, congestion, pavement quality |
| `as_student` | Social spots, shade, outdoor seating, cost indicators |

The prompt for MobilityBlock includes the relevant archetype's perspective:

```python
if archetype == "tourist":
    perception_text += f"\n  Tourist perspective: {perception.get('as_tourist', '')}"
```

---

## External References

| Resource | URL |
|----------|-----|
| Dijkstra's algorithm | https://en.wikipedia.org/wiki/Dijkstra%27s_algorithm |
| Shapely interpolate() | https://shapely.readthedocs.io/en/stable/manual.html#linear-referencing |
| Google Street View Static API | https://developers.google.com/maps/documentation/streetview/overview |
| Qwen2.5-VL on HuggingFace | https://huggingface.co/Qwen/Qwen2.5-VL-3B-Instruct |
| Qwen2-VL paper (arXiv) | https://arxiv.org/abs/2409.12191 |
| DuckDB ST_Distance | https://duckdb.org/docs/extensions/spatial/functions.html |

---

**Next:** [`07_api_layer.md`](07_api_layer.md) — all FastAPI endpoints and how the frontend communicates with the backend.
