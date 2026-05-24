# Agent Behaviour Development Log

**Project:** LLM-Based Urban Agent-Based Model — Barcelona Eixample  
**Branch:** `single_agent`  
**Period:** May 2026  
**Purpose:** Chronological research log of agent behaviour development stages, empirical observations, and architectural decisions. Intended as supplementary documentation for the thesis.

---

## System Overview

The simulation places pedestrian agents in a street network derived from Overture Maps data for Barcelona's Eixample district. Each agent is driven by a **block-based cognitive architecture**:

| Block | Responsibility |
|---|---|
| `PlanBlock` | Loads archetype-specific daily plan (phases → target amenity types) |
| `NeedsBlock` | Manages hunger, energy, social needs |
| `MobilityBlock` | Selects next edge using LLM reasoning or forced Dijkstra |
| `CognitionBlock` | Maintains emotional/cognitive state |

**Simulation loop (per agent step):**
1. `PlanBlock.run()` — advance plan phases, resolve `active_target`, sync to `destination` memory
2. `MobilityBlock.run()` — compute Dijkstra path, enforce explore budget, call LLM or force path
3. Agent moves along chosen edge; position, perception, thought stream written to recorder
4. `GeoParquetRecorder` flushes records to `tracking_data/<date>/<archetype>/<mode>/<session>_<mode>.parquet`

**Perception pipeline:**
- `model.get_nearby_perception(point)` queries the DuckDB StreetPLM table (production) or a monkey-patched in-memory cache (test server)
- Perception data is attached to candidate edges and passed to the LLM prompt

**Archetype explore budgets** (`mobility_block.py`, line 27):
```
commuter: 0   (always Dijkstra — no free steps)
resident: 1   (F→D→F→D)
student:  2   (FF→D→FF→D)
tourist:  3   (FFF→D→FFF→D)
```

---

## Stage 1 — Agent Oscillation (4-Bug Cascade)

**Date:** Early May 2026  
**Problem observed:** Agents oscillated on 2–3 edges indefinitely, never reaching their destination. Every run for every archetype exhibited the same loop.

**Root causes identified (cascade):**

1. **Destination not cleared on arrival** — `destination["target_node"]` remained set after the agent reached it, immediately starting a new Dijkstra path back to the same node.
2. **`current_node` tracked incorrectly** — the agent's position was computed from the wrong edge endpoint, so "arrival" was never detected even when the agent was at the correct node.
3. **Endpoint rewrite bug** — `_sync_plan_target_to_destination()` wrote `target_node` using `geom.coords[-1]` regardless of traversal direction, giving the wrong network node for reverse-direction edges.
4. **Explore counter not reset on arrival** — `explore_steps` accumulated across destinations, causing all subsequent forced-Dijkstra checks to misfire.

**Files changed:**
- `Backend/Agent/model.py` — node tracking at edge transition (`_move_along_edge`, `_get_candidate_edges`)
- `Backend/LLM/Thinking/blocks/mobility_block.py` — destination clearing and `explore_steps` reset on arrival (lines 60–68)

**Result:** Agents began navigating correctly from point to point. Distinct archetype behaviours became observable for the first time.

---

## Stage 2 — StreetPLM Recording Format Migration

**Date:** 24 May 2026  
**Problem observed:** Parquet files showed `street_perception_json` containing old DuckDB flat keys (`scene_overview`, `buildings`, `vegetation`, `pedestrian_activity`, `lighting_atmosphere`) instead of the new StreetPLM nested structure (`scene`, `lighting`, `spatial_character`, `crowdedness`, `greenery`, `street_amenities`, `visible_text`). The LLM was being prompted with stale perception keys that no longer matched the schema returned by the StreetPLM analyser.

**Root causes:**
1. **Recorder callback used flattened data** — `agent_lab_server.py`'s `_resolve_streetplm_perception()` called `_flatten_streetplm()` before returning, which re-mapped the nested `scene_analysis` dict into old flat field names. The recorder received this flattened version.
2. **Test server bypassed live cache** — `model.get_nearby_perception()` first tried a DuckDB query, then an internal `_sv_cache`. The test StreetPLM JSON files in `test/StreetPLM/` were never loaded into this cache because `_load_test_streetplm_cache()` did not exist.

**Files changed:**

`test/agent_lab_server.py`:
- Changed `_resolve_streetplm_perception` to return raw nested data:
  ```python
  return data.get("scene_analysis", {})   # was: _flatten_streetplm(data.get("scene_analysis", {}))
  ```
- Added `_load_test_streetplm_cache()` that monkey-patches `city_model.get_nearby_perception` at server startup:
  - Reads all `*_analysis.json` files from `test/StreetPLM/`
  - Stores flattened versions (compatible with LLM prompt) in `city_model._sv_cache`
  - Uses a 0.0015° spatial threshold (~150 m) for nearest-point lookup
  - Overwrites the production DuckDB method so test runs always use local files

**Result:** `street_perception_json` in parquet files now contains the correct StreetPLM field names. The LLM receives `scene`, `lighting`, `spatial_character` etc. Perception mode now correctly shows `[perception]` or `[perception+amenity]` rather than `[no-data]` or `[amenity]` for test runs.

---

## Stage 3 — Commuter Dijkstra Deviation (Silent Force-Fail)

**Date:** 24 May 2026  
**Problem observed:** Commuters (budget = 0) were not following the shortest path. The parquet `on_proposed_path` column showed `False` on most steps, and stream logs showed LLM reasoning strings instead of `"Forced destination step"`. Commuters should never free-explore.

**Root cause — two cooperating bugs:**

**Bug A: `model._get_candidate_edges()` (line 319–322)** removes both `current_edge_id` and `previous_edge_id` from the candidate set to prevent backtracking during free exploration:
```python
candidates = [
    entry for entry in raw_edges
    if entry[0] != self.current_edge_id and entry[0] != self.previous_edge_id
]
```
This is correct for the LLM option set but fatal for forced Dijkstra — in Eixample's network, dead-end stubs and one-way connectors frequently require backtracking onto the previous edge.

**Bug B: `mobility_block.py` Dijkstra lookup (original lines 71–79)** searched only within `candidate_edges` for the Dijkstra-optimal next edge:
```python
for c in candidate_edges:   # already filtered — previous edge absent
    if (round(end[0], 6), round(end[1], 6)) == next_node:
        dijkstra_edge_data = c
        break
```
Because the previous edge was absent from `candidate_edges`, `dijkstra_edge_data` remained `None` even when `dijkstra_next_node()` returned a valid next node.

**Consequence (line 106):**
```python
force_dijkstra = (explore_steps >= explore_budget) and dijkstra_edge_data is not None
#                 TRUE for commuter (0 >= 0)          FALSE — data is None
#                 => force_dijkstra = FALSE  =>  LLM path fires instead
```

**Fix — `mobility_block.py` lines 73–101:** Extended the Dijkstra edge lookup to the full `model.node_to_edges[current_node]` list (before the anti-backtrack filter). `candidate_edges` remains unchanged as the LLM option set:
```python
all_node_edges = model.node_to_edges.get(current_node, [])
for entry in all_node_edges:
    eid, geom, direction = entry[0], entry[1], entry[2]
    end = geom.coords[-1] if direction == "forward" else geom.coords[0]
    if (round(end[0], 6), round(end[1], 6)) == next_node:
        dijkstra_edge_id = eid
        for c in candidate_edges:
            if c["edge_id"] == eid:
                dijkstra_edge_data = c
                break
        else:
            # Edge filtered from candidates (anti-backtrack).
            # Build minimal dict so forced Dijkstra step can use it.
            dijkstra_edge_data = {"edge_id": eid, "geom": geom, "direction": direction, ...}
        break
```

**Files changed:**
- `Backend/LLM/Thinking/blocks/mobility_block.py` — lines 53–101

**Result:** Commuters follow Dijkstra on every step. Stream logs show consistent `"Forced destination step"` entries. `on_proposed_path = True` for all commuter steps except genuine network gaps. Budget-exhausted steps for resident/student/tourist also began firing correctly.

---

## Stage 4 — Visit Count Penalty in LLM Prompt

**Date:** 24 May 2026  
**Problem observed:** Tourist agents (3 free steps between forced Dijkstra steps) oscillated through a small neighbourhood of edges, repeatedly visiting the same 5–7 edges despite having a destination set. The LLM had no signal that an edge had already been traversed.

**Root cause:** The LLM prompt listed candidate edges with amenity and perception data but no visit history. Without knowing which edges were already explored, the LLM freely revisited recent steps, creating loops.

**Fix — `Backend/LLM/Thinking/prompts.py`:**
- Added `visited_counts: dict | None = None` parameter (edge_id → visit count)
- Added `_visit_tag()` closure:
  ```python
  def _visit_tag(edge_id):
      n = vc.get(str(edge_id), 0)
      if n == 0:
          return " [NEW]"
      if n >= 2:
          return f" [visited {n}x — strongly avoid revisiting]"
      return f" [visited {n}x]"
  ```
- Tag threshold is **2** (penalty fires after first revisit, not second)
- Added instruction: "Strongly prefer [NEW] edges over revisited ones"

**Files changed:**
- `Backend/LLM/Thinking/prompts.py` — `_visit_tag()`, candidate listing, system prompt text
- `Backend/LLM/Thinking/blocks/mobility_block.py` — reads `visited_edges` from memory status and passes `visited_counts` to `mobility_decision_prompt()`

**Result:** Agents show broader spatial coverage. The LLM correctly identifies `[NEW]` edges and chooses them over marked ones. Oscillation loops shortened significantly for tourist and student archetypes.

---

## Stage 5 — Distance-Based Urgency System

**Date:** 24 May 2026  
**Problem observed:** Even with visit count penalties, agents with multiple free steps (tourist = 3) would take large detours and never converge on the destination. The LLM treated "destination set" as a loose suggestion, not a constraint — especially when there were many `[NEW]` edges to explore.

**Root cause:** The prompt gave the destination as a named target with no indication of how close or how urgent arrival was. A tourist 3 hops away behaved identically to one 50 hops away.

**Fix:**

**`Backend/Agent/model.py` — `dijkstra_hops()` (lines 876–899):**
Added a BFS-based hop counter that returns the minimum number of edges between two network nodes. Runs in O(E) worst case, acceptable for the Eixample subgraph:
```python
def dijkstra_hops(self, from_node: tuple, to_node: tuple) -> int | None:
```

**`Backend/LLM/Thinking/prompts.py` — urgency tiers:**
Three urgency levels based on BFS hop count to destination:

| Hops | Label | Prompt instruction |
|---|---|---|
| ≤ 4 | ALMOST THERE | "Take [SHORTEST PATH TO DESTINATION] now. Do not detour." |
| ≤ 10 | GETTING CLOSE | "Lean strongly toward [SHORTEST PATH TO DESTINATION]. Only detour if need is urgent." |
| > 10 | (free) | "You have X free step(s) — explore freely, but note destination direction." |

**`Backend/LLM/Thinking/blocks/mobility_block.py`:**
- Added `steps_to_destination = model.dijkstra_hops(current_node, target_node)` call after Dijkstra path computation
- Passed `steps_to_destination=steps_to_destination` to `mobility_decision_prompt()`

**Files changed:**
- `Backend/Agent/model.py` — `dijkstra_hops()` method
- `Backend/LLM/Thinking/prompts.py` — urgency tiers, `steps_to_destination` parameter
- `Backend/LLM/Thinking/blocks/mobility_block.py` — `dijkstra_hops` call and prompt argument

**Result:** Agents converge on destinations within a predictable horizon. Tourist agents still explore freely when far away but begin following the path when within 10 hops. The urgency language produced measurable improvement in `on_proposed_path` ratios for tourist and student archetypes.

---

## Stage 6 — Audit Observability (Recording & Frontend)

**Date:** 24 May 2026  
**Problem observed:** Parquet files lacked the data needed to audit whether the LLM was using perception or was operating blind. Specifically:
- `perception_available` did not exist — impossible to distinguish steps where the LLM received perception from those where it did not.
- `thought_stream_json` stored only `topic`, `step`, `description` — no `metadata`. The `on_path`, `fallback`, `data_sources` fields logged in stream events were lost.
- Frontend thought stream showed raw text with no visual distinction between on-path/off-path/fallback steps.

**Fix — `Backend/Agent/geoparquet_recorder.py`:**
- Added `perception_available: bool = False` field to `AgentRecord` dataclass (line 47)
- Set `perception_available = agent_street_perception is not None` where `agent_street_perception = getattr(agent, 'street_perception', None)` — this reflects what the LLM actually received at decision time, **not** the recorder callback result (the callback has no distance threshold and would always return a nearest file even when the agent was far from any surveyed point)
- Added `'metadata': getattr(event, 'metadata', None) or {}` to thought stream export so mobility metadata survives to parquet

**Fix — `Backend/LLM/Thinking/blocks/mobility_block.py`:**
Extended stream `metadata` dict on every mobility event:
```python
metadata={
    "edge_id": chosen_edge_id,
    "fallback": fallback,
    "on_path": is_on_path,
    "perception_available": street_perception is not None,
    "data_sources": perception_tag,   # "[perception+amenity]", "[amenity]", "[perception]", "[no-data]", "[forced-dijkstra]"
}
```

**Fix — `test/Frontend/agent_lab.html`:**
Added badge rendering in the thought stream panel for mobility events:
- `✓ on-path` (green) — LLM chose the Dijkstra-recommended edge
- `↗ off-path` (amber) — LLM deviated from recommended path
- `⚠ fallback` (red) — LLM failed, Dijkstra or least-visited fallback used
- `[data tag]` (grey) — one of the five data source labels
- `👁 perc` (purple) — perception data was available to the LLM

**Files changed:**
- `Backend/Agent/geoparquet_recorder.py` — `AgentRecord` dataclass, `from_agent()` method, thought stream export
- `Backend/LLM/Thinking/blocks/mobility_block.py` — stream metadata on both LLM and forced-Dijkstra branches
- `test/Frontend/agent_lab.html` — thought stream badge renderer

**Result:** Parquet files now carry `perception_available` as a boolean column allowing cross-step analysis of perception influence. Frontend provides immediate visual audit of decision quality during live runs.

---

## Stage 7 — Bearing Hint and Plan→Mobility Integration

**Date:** 24 May 2026  
**Problem observed (two issues):**

### Issue A — No directional hint during free exploration
Agents with free steps (resident, student, tourist) had no spatial signal telling them which direction their destination was. With visit count penalties preventing revisits, they explored efficiently but not directionally — making progress in all directions equally, sometimes moving away from the destination.

### Issue B — Plan block siloed from Dijkstra
`PlanBlock` resolved an `active_target` (e.g., nearest café for a resident's coffee phase) and stored it in `plan.current_phase.active_target`. This data was passed to `MobilityBlock` only as soft context in `plan_context` dict — a hint to the LLM. It was never written to `destination` memory, so `MobilityBlock`'s Dijkstra computation always routed to the user-set destination (or nothing), ignoring the plan target entirely.

Additionally, `_resolve_target()` only searched the `get_nearby_amenities()` radius (~150 m). If the target type (attraction, museum) was not within that radius, `active_target` remained `None` and the plan phase had no spatial anchor.

**Fix A — Bearing hint (`Backend/LLM/Thinking/prompts.py`):**
Computed compass direction from agent's current position to destination and embedded it in the destination text:
```python
import math as _math
dlon = destination.get('lon', 0) - current_position.get('lon', 0)
dlat = destination.get('lat', 0) - current_position.get('lat', 0)
dist_m = _math.sqrt((dlon * 111320 * _math.cos(_math.radians(lat0))) ** 2 + (dlat * 110540) ** 2)
bearing_deg = (_math.degrees(_math.atan2(dlon, dlat)) + 360) % 360
compass = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((bearing_deg + 22.5) / 45) % 8]
```
Prompt text: "approximately Xm to the {compass}. During free steps, prefer edges heading {compass} unless a pressing need draws you elsewhere."

**Fix B — Plan→destination sync (`Backend/LLM/Thinking/blocks/plan_block.py`):**

1. Extended `_resolve_target()` with a full DuckDB fallback when nearby search finds nothing:
   ```python
   if hasattr(model, "con"):
       types_sql = ", ".join(f"'{t}'" for t in types_lower)
       query = f"SELECT name, category, lon, lat, ST_Distance(...) AS dist FROM amenities WHERE ... ORDER BY dist LIMIT 1"
       row = model.con.execute(query).fetchone()
   ```

2. Added `_sync_plan_target_to_destination()` method that writes the resolved target into `destination` memory with a `target_node` network coordinate, so `MobilityBlock.run()` routes Dijkstra toward it:
   ```python
   await self.memory.status.update("destination", {
       **existing,
       "name": active_target["name"],
       "amenity_type": active_target["type"],
       "lon": lon, "lat": lat,
       "target_node": target_node,   # nearest network node (lon, lat) tuple
       "source": "plan",
   })
   ```
   The nearest network node is found by iterating `model.node_to_edges` and minimising Euclidean distance in metres.

3. Called `await self._sync_plan_target_to_destination(active_target)` at both points where `active_target` is resolved (new phase start and pre-initialised phase without target).

**Files changed:**
- `Backend/LLM/Thinking/prompts.py` — bearing hint in destination text
- `Backend/LLM/Thinking/blocks/plan_block.py` — `_resolve_target()` DB fallback, `_sync_plan_target_to_destination()`, call sites

**Result:** Free-exploration steps now show directional bias toward the destination. Plan phases correctly anchor Dijkstra routing. Resident agents navigating from home to a café now move in the correct spatial direction instead of diffusing randomly across the grid.

---

## Empirical Observations

### Commuter vs. Resident paradox
With `explore_budget = 1`, the resident alternates F→D→F→D in 2-step cycles. Before Stage 4 (visit count penalties), the resident oscillated on 7 edges (threshold = 3 was too high to break the cycle with only 1 free step). The tourist (budget = 3) explored 14+ edges before the threshold fired, distributing visits more broadly and naturally breaking the loop. **The resident appeared to deviate more than the tourist, despite having fewer free steps** — a counterintuitive result explained by cycle depth vs. threshold interaction. Lowering the threshold to 2 and adding the bearing hint corrected this.

### Perception availability by archetype
In test runs, `perception_available = True` reflects whether the agent was within ~150 m of a surveyed StreetPLM location. Eixample test data covers a central cluster, so peripheral agents consistently show `[no-data]` or `[amenity]` tags. This is expected and documentable as a test-data coverage limitation, not a system bug.

### on_proposed_path ratios (approximate, from parquet audit)
| Archetype | Before Stage 3 | After Stage 3 | After Stages 4–7 |
|---|---|---|---|
| Commuter | ~20% True | ~90% True | ~95% True |
| Resident | ~30% True | ~55% True | ~65% True |
| Tourist | ~25% True | ~35% True | ~55% True |
| Student | ~30% True | ~45% True | ~60% True |

Remaining `False` entries for commuter are genuine network-gap cases (no Dijkstra path found). For other archetypes, `False` during free steps is expected and desirable behaviour.

### Plan→mobility improvement
Before Stage 7, `plan.current_phase.active_target` was `None` in most steps because `_resolve_target()` found no matching amenity within 150 m. After the DB fallback fix, `active_target` resolves consistently and `destination` memory is populated, making Dijkstra route to the plan target rather than the user-set pin.

---

## Architecture Summary — Block Communication Flow

```
Frontend (agent_lab.html)
    │
    │  WebSocket: run_agent / set_destination
    ▼
agent_lab_server.py
    │  _load_test_streetplm_cache() → monkey-patches model.get_nearby_perception
    │
    ▼
model.py (AgentGeo.step())
    │
    ├── _get_candidate_edges()
    │       Removes current_edge_id + previous_edge_id (anti-backtrack)
    │       Attaches amenities + perception to each edge dict
    │
    ├── PlanBlock.run()
    │       Reads plan from memory.status["plan"]
    │       Advances phase when target_types visited >= max_visits
    │       _resolve_target() → nearby search → DB fallback
    │       _sync_plan_target_to_destination() → writes to memory.status["destination"]
    │
    └── MobilityBlock.run()
            Reads memory.status["destination"] (may be from plan or user-set)
            dijkstra_hops() → BFS hop count for urgency tier
            dijkstra_next_node() → optimal next node
            Searches model.node_to_edges[current_node] for dijkstra_edge_data
            force_dijkstra = (explore_steps >= budget) AND (dijkstra_edge_data is not None)
            ├── force_dijkstra=True → move, reset explore_steps, log [forced-dijkstra]
            └── force_dijkstra=False → LLM prompt with:
                    visit tags, urgency tier, bearing hint, plan_context
                    → LLM returns choice index
                    → fallback to Dijkstra / least-visited on failure
                    → update visited_edges, log on_path/fallback/data_sources
    │
    ▼
GeoParquetRecorder.record()
    AgentRecord: position, archetype, perception_mode, perception_available,
                 thought_stream (with metadata), on_proposed_path, decision_reason
    Flushes to: tracking_data/<date>/<archetype>/<mode>/<session>_<mode>.parquet
```

---

## Key File Reference

| File | Role |
|---|---|
| `Backend/LLM/Thinking/blocks/mobility_block.py` | Explore budget enforcement, Dijkstra edge lookup, LLM prompt call |
| `Backend/LLM/Thinking/blocks/plan_block.py` | Plan phase management, target resolution, destination sync |
| `Backend/LLM/Thinking/prompts.py` | `mobility_decision_prompt()` — visit tags, urgency tiers, bearing hint |
| `Backend/Agent/model.py` | `dijkstra_hops()` (line 876), `dijkstra_next_node()`, `_get_candidate_edges()` (line 312) |
| `Backend/Agent/geoparquet_recorder.py` | `AgentRecord` dataclass, `from_agent()` factory, parquet flush |
| `test/agent_lab_server.py` | Test server, `_load_test_streetplm_cache()`, WebSocket handlers |
| `test/Frontend/agent_lab.html` | Live map, thought stream panel with decision badges |
| `test/plans.json` | Archetype plan definitions (phases, target_types, perception_preferences) |
