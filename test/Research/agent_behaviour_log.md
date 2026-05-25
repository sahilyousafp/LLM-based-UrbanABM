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

## Stage 8 — Plan Override Bug & DuckDB Schema Fix

**Date:** 24–25 May 2026

### Issue A — PlanBlock overwrote user-configured destination

**Problem observed:** For resident agents, the Dijkstra path shown on the frontend disappeared immediately after step 1 and the agent deviated from the selected target regardless of nav mode. Commuters were unaffected.

**Root cause:** On step 1, `PlanBlock._sync_plan_target_to_destination()` unconditionally overwrote `memory.status["destination"]` with the nearest match for the resident plan's first phase (`target_types: ["supermarket", "bakery"]`). Dijkstra then routed to the supermarket. After the agent passed that node, `target_node` became stale and the path disappeared.

**Fix — two-part:**

1. `test/agent_lab_server.py` — `configure_single_agent()` now tags the user-set destination:
   ```python
   target_info = {
       "name": target_name, "lon": ..., "lat": ...,
       "target_node": target_node,
       "source": "user_configured",   # added
   }
   ```

2. `Backend/LLM/Thinking/blocks/plan_block.py` — `_sync_plan_target_to_destination()` returns early when the existing destination is user-configured:
   ```python
   existing = await self.memory.status.get("destination", {}) or {}
   if existing.get("source") == "user_configured":
       return
   ```
   Also added `"visited": False` to newly-synced plan targets so a stale `visited=True` flag from a prior destination is never carried over.

### Issue B — DuckDB `_resolve_target()` column name mismatch

**Problem observed:** Backend log showed `Binder Error: Referenced column "category" not found in FROM clause` whenever `PlanBlock` tried to fall back to the full DB query for attraction/museum types.

**Root cause:** The fallback SQL used `category` (old schema name) and bare `lon`, `lat` columns instead of `ST_X(geometry)` / `ST_Y(geometry)`.

**Fix — `plan_block.py` `_resolve_target()` fallback query:**
```sql
SELECT name, amenity, ST_X(geometry) as lon, ST_Y(geometry) as lat,
    ST_Distance(geometry, ST_GeomFromText('POINT ({lon} {lat})')) AS dist
FROM amenities
WHERE LOWER(amenity) IN ({types_sql})
ORDER BY dist LIMIT 1
```

**Files changed:**
- `test/agent_lab_server.py` — `source: "user_configured"` in `target_info`
- `Backend/LLM/Thinking/blocks/plan_block.py` — early-return guard in `_sync_plan_target_to_destination()`, corrected DuckDB query

**Result:** Resident agents (and all archetypes with plan phases) no longer have their configured destination overwritten. The path line stays fixed on the frontend throughout the run. DuckDB fallback queries succeed for all amenity types.

---

## Stage 9 — GPS Rule Hardening & Direction-Aware Visit Tags

**Date:** 25 May 2026

### Issue A — LLM defects from GPS edge due to visit-count penalty

**Problem observed:** Tourist recording at step 563 was 40m Euclidean from the pharmacy destination on edge 497. The junction had all candidates visited 4×. The LLM read `[SHORTEST PATH TO DESTINATION] [visited 4x — strongly avoid revisiting]` on the GPS-labeled edge and chose a different edge — "also heading NW" per compass reasoning. The agent drifted to 181m and never arrived.

**Root cause:** `_visit_tag()` applied the visit-count penalty unconditionally, including to the edge marked `[SHORTEST PATH TO DESTINATION]`. The LLM resolved the contradiction by discarding the GPS label.

**Fix A1 — suppress visit penalty on GPS edge (`Backend/LLM/Thinking/prompts.py`):**
```python
def _visit_tag(edge_id, direction):
    if edge_id == path_hint_edge_id and nav_mode in ('gps', 'both'):
        return ""   # GPS edge: reaching goal overrides novelty signal
    ...
```

**Fix A2 — harden GPS instruction:**
```
Old: "When in doubt between an amenity edge and [SHORTEST PATH TO DESTINATION], prefer the path."
New: "GPS RULE: If any candidate shows [SHORTEST PATH TO DESTINATION], you MUST choose it.
     Visit counts, amenity preferences, and archetype interests are NOT valid reasons to skip it.
     The only permitted exception is a critical survival need: hunger > 0.9 or energy < 0.1.
     Restaurants, shops, or curiosity do not qualify."
```
Threshold raised from 0.8 → 0.9 (hunger) and 0.2 → 0.1 (energy) to close the loophole where the LLM rationalised restaurant stops as "directly relevant".

### Issue B — Edge oscillation on bidirectional traversal (edge 1783)

**Problem observed:** Tourist recording showed edge 1783 visited in both forward and reverse directions in alternating steps. `_visit_tag(1783)` returned `""` for BOTH traversal directions because only `edge_id` was checked. The LLM never learned to avoid 1783 forward after having used 1783 reverse, enabling a direction-flip oscillation that bypassed the penalty.

**Fix — direction-aware visit tag (`Backend/LLM/Thinking/prompts.py`):**

The `path_hint_direction` parameter (Dijkstra-optimal traversal direction) was threaded from `mobility_block.py` into `mobility_decision_prompt()`. The GPS-label and visit-tag suppression now check both `edge_id` AND `direction`:

```python
# In candidates_text:
f"{'[SHORTEST PATH TO DESTINATION]' if c['edge_id'] == path_hint_edge_id
   and c.get('direction') == path_hint_direction
   and nav_mode in ('gps', 'both') else ''}"
```

The wrong-direction traversal of the GPS edge still accumulates a visit penalty, breaking the forward/reverse oscillation cycle.

**Files changed:**
- `Backend/LLM/Thinking/prompts.py` — `_visit_tag()`, GPS label in `candidates_text`, hardened GPS RULE text
- `Backend/LLM/Thinking/blocks/mobility_block.py` — `path_hint_direction` capture and prompt argument

**Result:** Visit-count penalty no longer competes with the GPS label. LLM consistently chooses the Dijkstra-optimal edge even when it has been traversed multiple times. Direction-flip oscillation eliminated for tourist agents on dead-end stubs.

---

## Stage 10 — Distance-Based Explore Budget Reduction

**Date:** 25 May 2026  
**Problem observed:** Tourist agents with `explore_budget = 3` never triggered the prompt urgency tiers (hop-based thresholds from Stage 5) because the Eixample network has variable edge lengths — agents could be 40m Euclidean from the destination but still 8+ hops away through one-way connectors. The "ALMOST THERE" language fired only in the prompt text; the actual Dijkstra enforcement (budget reduction) never fired until within 4 hops.

**Root cause:** `force_dijkstra` checked only `explore_steps >= explore_budget`, with no geographic trigger. The urgency text and the actual enforcement were decoupled.

**Fix — `Backend/LLM/Thinking/blocks/mobility_block.py`:**

Dynamic budget reduction based on Euclidean distance to destination:

```python
if destination and not destination.get("visited") and destination.get("lon"):
    import math as _math
    _ALMOST_THERE = {"tourist": 60, "resident": 40, "student": 50}.get(archetype, 50)
    _GETTING_CLOSE = {"tourist": 350, "resident": 200, "student": 200}.get(archetype, 200)
    dlon = (destination["lon"] - position["lon"]) * 111320 * _math.cos(_math.radians(lat))
    dlat = (destination["lat"] - position["lat"]) * 110540
    dist_to_dest = _math.sqrt(dlon**2 + dlat**2)
    if dist_to_dest <= _ALMOST_THERE:
        explore_budget = 0          # pure Dijkstra
    elif dist_to_dest <= _GETTING_CLOSE:
        explore_budget = min(explore_budget, 1)   # at most 1 free step
```

**Threshold values and rationale:**

| Archetype | `_ALMOST_THERE` | `_GETTING_CLOSE` | Notes |
|---|---|---|---|
| Tourist | 60m | 350m | Budget=3 causes diverging random walk beyond 150m — 350m corrects convergence. Pragmatic rather than purely behavioural. |
| Resident | 40m | 200m | Budget=1 already converges; 200m adds insurance near destination. |
| Student | 50m | 200m | Budget=2 — moderate convergence window. |
| Commuter | — | — | Budget=0 always: Dijkstra on every step, thresholds irrelevant. |

**Honest limitation:** The 350m threshold for tourists is a workaround for a deeper issue: `explore_budget=3` produces a diverging random walk for any configured destination. A more behaviourally accurate fix would differentiate `explore_budget` by destination source — a tourist who has entered a specific café into their phone (user-configured) should behave more like budget=1 throughout the journey, not just within 350m. This remains a known open item.

**Files changed:**
- `Backend/LLM/Thinking/blocks/mobility_block.py` — distance-based budget block (lines 53–67)

**Result:** Tourist agents with a configured destination within 350m begin converging via the 1-free-step cycle. Agents within 60m switch to pure Dijkstra, guaranteeing arrival. The urgency prompt text and the actual Dijkstra enforcement are now aligned.

---

## Stage 11 — Agent Profile Plans (plans.json Restructure)

**Date:** 25 May 2026  
**Motivation:** The original `plans.json` used a flat `phases` list with indexed `target_types` arrays. It had no personality model for each archetype and no mechanism for need-driven detours during a phase. The structure also contained commented-out entries (invalid JSON) and hardcoded perception field lists disconnected from agent identity.

**New structure — `test/plans.json`:**

Each archetype now has:
1. **`profile`** — personality descriptor: `interests`, `pace`, `curiosity`, `social`, `description`. Gives the LLM and future analyses a narrative identity for the agent type.
2. **`daily_plan`** — replaces flat `phases`. Each phase has a `time_of_day` field (morning/midday/afternoon) and a list of `en_route_stops`.
3. **`en_route_stops`** — need-triggered interruptions within a phase. Each stop has a `trigger` (`need` key + `threshold`) and its own `target_types` and `max_visits`.

Example (tourist morning phase):
```json
{
  "id": "morning_attraction",
  "time_of_day": "morning",
  "goal": "Visit a cultural attraction or museum",
  "target_types": ["attraction", "museum"],
  "max_visits": 1,
  "perception_preferences": ["buildings", "spatial_enclosure", "vegetation"],
  "en_route_stops": [
    {
      "id": "breakfast_stop",
      "trigger": {"need": "hunger", "threshold": 0.55},
      "goal": "Grab breakfast on the way",
      "target_types": ["cafe", "bakery"],
      "max_visits": 1
    }
  ]
}
```

**`Backend/LLM/Thinking/blocks/plan_block.py` — updates:**

- `_init_plan()` — reads `daily_plan` if present; falls back to legacy `phases` key for backward compatibility. Adds `interrupted_phase`, `en_route_stop_active`, and `profile` to plan memory state.
- `run()` — new state machine for en_route_stop handling:
  - If `en_route_stop_active`: check completion of stop phase first; restore interrupted main phase on completion.
  - Main phase completion check runs only when no stop is active.
  - After main phase is confirmed active: call `_check_and_trigger_en_route_stop()`.
- `_check_and_trigger_en_route_stop()` — new method. Reads current `needs`, checks all `en_route_stops` of the active phase. If a trigger fires and the stop type has not already been visited in this phase, saves the main phase as `interrupted_phase`, activates the stop as `current_phase`, resolves and syncs its `active_target`. Returns early if destination source is `user_configured` (stop would conflict with the user-set GPS route).

**`Backend/LLM/Thinking/blocks/mobility_block.py` and `prompts.py`:**
- `plan_context` now includes `time_of_day` from the current phase.
- Prompt shows `"Current Plan Phase: Visit a cultural attraction (morning)"` so the LLM understands the narrative context of each phase.

**Files changed:**
- `test/plans.json` — full restructure
- `Backend/LLM/Thinking/blocks/plan_block.py` — `_init_plan()`, `run()`, new `_check_and_trigger_en_route_stop()`
- `Backend/LLM/Thinking/blocks/mobility_block.py` — `time_of_day` in `plan_context`
- `Backend/LLM/Thinking/prompts.py` — `time_of_day` in plan phase header

**Result:** Agent plans now carry a personality model and support mid-phase need stops. The en_route_stop mechanism allows a tourist to detour for breakfast if hungry while heading to a museum, then resume the museum phase — without overriding a user-configured GPS destination.

**Current limitation:** `time_of_day` is purely descriptive metadata. Phases advance by `max_visits`, not by simulation clock. A "midday" phase can start at step 3 if the morning phase resolves quickly. A step-to-time mapping is a future extension.

---

## Stage 12 — Pedestrian Routing Accuracy (Dijkstra & GPS Label)

**Date:** 25 May 2026

### Issue A — planned-path endpoint was O(N²) in Dijkstra calls

**Problem observed:** The path line on the frontend appeared to update late — lagging 1–2 steps behind the agent's actual position during live runs.

**Root cause:** `GET /api/agent/{id}/planned-path` computed the path by calling `dijkstra_next_node()` in a loop — one full Dijkstra pass per hop. For a 20-hop path this was 20 × O(E log V). Each Dijkstra pass on the Eixample subgraph took ~0.02–0.05s, making a 20-hop path request take ~0.5–1s.

**Fix — `test/agent_lab_server.py`:** Rewrote the endpoint to run a single Dijkstra pass with a predecessor dictionary, then reconstruct the full node sequence in one backward traversal — O(E log V) total regardless of path length. Identical algorithm to `model._compute_proposed_path()`.

### Issue B — Dijkstra used WGS84 degree-lengths as edge weights

**Problem observed:** Paths shown on the frontend took subtly non-optimal routes — specifically showing a bias toward north-south segments over equal-length east-west segments.

**Root cause:** All three Dijkstra implementations (`dijkstra_next_node`, `_compute_proposed_path`, planned-path endpoint) used `geom.length` — the Shapely geometry length in WGS84 degrees. At latitude 41°N:
- 100m east-west ≈ 0.001198 degrees longitude
- 100m north-south ≈ 0.000905 degrees latitude

East-west edges appeared ~32% "longer" in degree-space than equal metric-length north-south edges, causing Dijkstra to systematically prefer N-S routing over E-W routing.

**Fix — `Backend/Agent/model.py`:** Precomputed metric edge lengths at network load time using a flat-earth approximation (accurate to <0.1% for street segments):

```python
def _metric_length(geom) -> float:
    total = 0.0
    for (lon1, lat1), (lon2, lat2) in zip(geom.coords, geom.coords[1:]):
        dlon, dlat = lon2 - lon1, lat2 - lat1
        mid_lat = (lat1 + lat2) / 2
        dx = dlon * 111320 * math.cos(math.radians(mid_lat))
        dy = dlat * 110540
        total += math.sqrt(dx**2 + dy**2)
    return total
```

`length_m` is stored as the 5th element of each `node_to_edges` tuple: `(edge_id, geom, direction, weight_mult, length_m)`. All Dijkstra implementations now use `length_m * weight_mult` as the edge cost.

### Issue C — GPS label and visit-tag suppression required direction match

**Problem observed:** In some topologies, the anti-backtrack dead-end fallback (`candidates = raw_edges`) restored the GPS-edge candidate but in the opposite direction from what Dijkstra computed. The GPS label silently disappeared and the visit-count penalty was not suppressed.

**Root cause:** Both the `[SHORTEST PATH TO DESTINATION]` label and the `_visit_tag()` suppression required the candidate's direction to exactly match `path_hint_direction`. For pedestrian movement on a bidirectional network, direction is not a one-way constraint — both traversal directions of the same physical street are valid.

**Fix — `Backend/LLM/Thinking/prompts.py`:** Removed the direction check from both:
```python
# GPS label: fires on any direction of the GPS edge_id
f"{'[SHORTEST PATH TO DESTINATION]' if c['edge_id'] == path_hint_edge_id
   and nav_mode in ('gps', 'both') else ''}"

# _visit_tag: suppresses penalty for any direction of the GPS edge_id
if edge_id == path_hint_edge_id and nav_mode in ('gps', 'both'):
    return ""
```

This is safe because at any given node, each edge_id appears at most once in the candidate list (forward OR reverse, not both), making ambiguity impossible in practice.

**Note:** Stage 9 introduced direction-aware visit tags to prevent direction-flip oscillation on the GPS edge. Stage 12 reverts this to direction-agnostic for pedestrian realism. The oscillation risk from Stage 9 is now handled by the hardened GPS RULE instruction (threshold 0.9/0.1) and the raised `_GETTING_CLOSE` budget reduction (350m), which together enforce GPS compliance before oscillation can develop.

**Files changed:**
- `Backend/Agent/model.py` — `_metric_length()` helper, 5-element `node_to_edges` tuple, metric weight in `dijkstra_next_node()` and `_compute_proposed_path()`
- `test/agent_lab_server.py` — single-pass Dijkstra in `planned-path` endpoint with metric weights
- `Backend/LLM/Thinking/prompts.py` — direction-agnostic GPS label and `_visit_tag()` suppression

**Result:** Dijkstra paths now reflect actual pedestrian walking distances. The path line on the frontend updates at the speed of a single graph search. GPS labels fire reliably regardless of which direction the dead-end fallback presents the optimal edge.

---

## Stage 13 — Destination-Source Navigation, Needs Realism & Recording Quality

**Date:** 25 May 2026

### Issue A — `_GETTING_CLOSE` was a band, not a funnel

**Problem observed:** The 350m `_GETTING_CLOSE` threshold introduced in Stage 10 was insufficient. In a 358-step tourist recording, 68% of steps occurred outside the 350m zone — the agent drifted to 630m and the cap never fired. Root cause: with `explore_budget=3`, the agent performs a diverging random walk at *any* distance. A geographic band does not correct a walk that has already left the band.

**Analysis:** The correct discriminant is not *how far away the destination is* but *what kind of destination it is*. A tourist who has entered a specific address (user_configured) has declared navigational intent — their cognitive mode is "navigate to X", not "explore until I find something". A plan-assigned destination is a softer goal — the agent is fulfilling an activity schedule and exploration is appropriate. These two cases demand different budgets throughout the walk, not just in the final 350m.

**Fix — `Backend/LLM/Thinking/blocks/mobility_block.py`:**
- Removed the `_GETTING_CLOSE` tier entirely.
- Added a destination-source cap before the distance block: if `destination.source == "user_configured"` and not yet visited, cap `explore_budget = min(explore_budget, 1)` regardless of distance.
- Retained `_ALMOST_THERE` (60m tourist) for final pure-Dijkstra approach — this covers the last-hop case where even 1 free step can cause a miss.

```python
# User-configured: cap budget throughout the journey
if destination and destination.get("source") == "user_configured" and not destination.get("visited"):
    explore_budget = min(explore_budget, 1)

# Final approach: pure Dijkstra regardless of source
if dist_to_dest <= _ALMOST_THERE:
    explore_budget = 0
```

**Behavioral semantics:**
| Destination source | Budget | F→D pattern | Analogy |
|---|---|---|---|
| `user_configured` | min(archetype, 1) | F→D→F→D | Person who typed the address into Maps |
| `plan` | full archetype budget | FFF→D (tourist) | Person following a loose daily itinerary |

**Result:** Tourist agents with a user-configured destination now make net forward progress from step 1. The F→D cycle guarantees one Dijkstra step per two moves. The open item from Stage 10 is closed.

---

### Issue B — Needs decay rate 5× too fast

**Problem observed:** In multi-step recordings, `hunger` decayed from initial 0.52 to 0.00 within ~35 steps. At ~30 seconds per step, this means the agent became maximally hungry in under 18 minutes of simulated walking. Plan phases triggered early (hunger threshold crossing `en_route_stop` triggers almost immediately) and `current_phase` became `None` before the agent reached its destination.

**Root cause — `Backend/LLM/Thinking/blocks/needs_block.py`:**
```python
DECAY_RATES = {
    "hunger": 0.015,   # was: full in 67 steps ≈ 33 minutes
    "energy": 0.010,   # was: empty in 100 steps ≈ 50 minutes
    "social": 0.010,
    "comfort": 0.015,
}
```

**Fix:**
```python
DECAY_RATES = {
    "hunger": 0.003,   # full in 333 steps ≈ 2.8 hours walking
    "energy": 0.003,   # empty in 333 steps ≈ 2.8 hours walking
    "social": 0.003,
    "comfort": 0.003,
}
```

Calibration basis: at ~30 seconds per edge traversal (1.4 m/s walking speed, average 42m edge length), 333 steps ≈ 167 minutes ≈ 2.8 hours. A pedestrian walking for 2–3 hours without eating will feel significantly hungry — consistent with real physiology. Plan-phase hunger triggers (~0.55 threshold) now fire after ~183 steps (≈1.5h), appropriate for a lunch-break detour on a morning walk.

---

### Issue C — Amenity satisfaction fired from 150m proximity

**Problem observed:** `NeedsBlock` called `_evaluate_amenity_satisfaction()` whenever `nearby_amenities` was non-empty. Since `get_nearby_amenities()` returns all amenities within ~150m, the closest restaurant on a passing street block triggered a hunger reset every step the agent walked by it. Needs dropped to near-zero within 30–50 steps without the agent ever stopping at an amenity.

**Root cause — `needs_block.py` lines 76–95:**
```python
if nearby_amenities:
    closest = nearby_amenities[0]
    amenity_result = await self._evaluate_amenity_satisfaction(...)   # fired always
```

**Fix:** Added a distance guard using the `dist` field already present in the `nearby_amenities` dicts:
```python
AMENITY_SATISFACTION_RADIUS_M = 30   # must be physically at the amenity

if nearby_amenities:
    closest = nearby_amenities[0]
    if closest.get("dist", 9999) <= AMENITY_SATISFACTION_RADIUS_M:
        # evaluate satisfaction only if truly at the amenity
        amenity_result = await self._evaluate_amenity_satisfaction(...)
```

**Behavioral effect:** The agent now only receives need satisfaction from an amenity it is standing next to (within 30m). Walking past a café has no effect on hunger. This aligns with the simulation's physical premise — proximity observation ≠ consumption.

**30m threshold rationale:** The Eixample block is ~113m wide. An agent on a street edge is typically ≤30m from the nearest building entrance. If an amenity is within 30m, the agent is effectively on the same block face and could plausibly be entering.

---

### Issue D — Duplicate step rows in parquet recordings

**Problem observed:** Parquet files contained 79–100 duplicate `(agent_id, step)` rows per recording session. Analysis showed the recorder correctly calls `record_agent_state` once per step in `model.async_step()`, but `_flush_to_parquet` reads and appends the existing file on every flush without deduplication. If a flush fails mid-write (exception before `self.buffer = []`), the retained buffer records get written again on the next flush, producing exact duplicates.

**Fix — `Backend/Agent/geoparquet_recorder.py`, `_flush_to_parquet()`:**
```python
# After concat with existing file, before writing:
group_gdf = group_gdf.drop_duplicates(subset=['agent_id', 'step'], keep='last')
group_gdf.to_parquet(str(file_path))
```

Using `keep='last'` preserves the most recently recorded state for a given step (which is the post-LLM state with all memory updates applied), discarding any earlier partial captures of the same step.

**Files changed:**
- `Backend/LLM/Thinking/blocks/mobility_block.py` — user_configured explore_budget cap, removed `_GETTING_CLOSE` tier
- `Backend/LLM/Thinking/blocks/needs_block.py` — `DECAY_RATES` reduced 5×, `AMENITY_SATISFACTION_RADIUS_M = 30` guard
- `Backend/Agent/geoparquet_recorder.py` — `drop_duplicates` in `_flush_to_parquet()`

---

## Empirical Observations

### Commuter vs. Resident paradox
With `explore_budget = 1`, the resident alternates F→D→F→D in 2-step cycles. Before Stage 4 (visit count penalties), the resident oscillated on 7 edges (threshold = 3 was too high to break the cycle with only 1 free step). The tourist (budget = 3) explored 14+ edges before the threshold fired, distributing visits more broadly and naturally breaking the loop. **The resident appeared to deviate more than the tourist, despite having fewer free steps** — a counterintuitive result explained by cycle depth vs. threshold interaction. Lowering the threshold to 2 and adding the bearing hint corrected this.

### Perception availability by archetype
In test runs, `perception_available = True` reflects whether the agent was within ~150 m of a surveyed StreetPLM location. Eixample test data covers a central cluster, so peripheral agents consistently show `[no-data]` or `[amenity]` tags. This is expected and documentable as a test-data coverage limitation, not a system bug.

### on_proposed_path ratios (approximate, from parquet audit)
| Archetype | Before Stage 3 | After Stage 3 | After Stages 4–7 | After Stages 8–12 |
|---|---|---|---|---|
| Commuter | ~20% True | ~90% True | ~95% True | ~97% True |
| Resident | ~30% True | ~55% True | ~65% True | ~70% True |
| Tourist | ~25% True | ~35% True | ~55% True | ~65% True |
| Student | ~30% True | ~45% True | ~60% True | ~65% True |

Remaining `False` entries for commuter are genuine network-gap cases (no Dijkstra path found). For other archetypes, `False` during free steps is expected and desirable behaviour.

### Plan→mobility improvement
Before Stage 7, `plan.current_phase.active_target` was `None` in most steps because `_resolve_target()` found no matching amenity within 150 m. After the DB fallback fix, `active_target` resolves consistently and `destination` memory is populated, making Dijkstra route to the plan target rather than the user-set pin.

### Tourist convergence failure (Stage 10 analysis)
A 358-step tourist recording (20260525_102539_both.parquet) targeting Verdaguer Café (344m away) showed the agent reaching a minimum distance of only 284m after 300 steps, then drifting to 453m by the end — net progress of −109m. Analysis revealed:
- With `explore_budget=3`, the expected number of Dijkstra-directed hops per N steps is N/4. Each hop advances ~25m; three random LLM steps can drift ~75m. The agent diffuses rather than converges.
- The distance-based budget reduction (`_GETTING_CLOSE = 150m`) never fired because the agent never closed within 150m.
- 0 fallbacks: the LLM was making valid choices — just not toward the destination.
- 25 unique edges visited across 358 steps: the agent was efficiently exploring a small geographic cluster rather than making spatial progress.

The 350m threshold fix addresses convergence but the underlying issue is that `explore_budget` conflates two distinct behaviours: *how curious is this agent while exploring freely* vs. *how strictly does this agent follow a known GPS route*. These should be parameterised separately.

### Plan phase completion artifact
When the agent archetype's plan `target_types` overlap with the configured destination type (e.g., tourist targeting a café with `midday_lunch` phase also requiring a café), `NeedsBlock` can register a visit to a nearby café in passing, completing the plan phase before the agent reaches the user-configured destination. This produces a `plan.current_phase = None` state while the Dijkstra still routes toward the user destination. Not a bug (the guard prevents plan override), but creates potentially confusing thought-stream entries.

---

## Architecture Summary — Block Communication Flow

```
Frontend (agent_lab.html)
    │
    │  WebSocket: run_agent / set_destination / configure
    ▼
agent_lab_server.py
    │  configure_single_agent() → destination tagged source: "user_configured"
    │  _load_test_streetplm_cache() → monkey-patches model.get_nearby_perception
    │  GET /planned-path → single-pass Dijkstra with metric weights (O(E log V))
    │
    ▼
model.py (AgentGeo.step())
    │
    ├── _get_candidate_edges()
    │       Removes current_edge_id + previous_edge_id (anti-backtrack, by edge_id only)
    │       Attaches amenities + perception to each edge dict
    │       node_to_edges tuples: (edge_id, geom, direction, weight_mult, length_m)
    │
    ├── PlanBlock.run()
    │       Reads plan from memory.status["plan"] (profile + daily_plan format)
    │       En-route stop state machine:
    │           en_route_stop_active? → check stop completion → restore main phase
    │           no stop active? → check main phase completion → advance phase
    │           _check_and_trigger_en_route_stop() → fires if need > threshold
    │               → skips if destination.source == "user_configured"
    │       _resolve_target() → nearby search → DuckDB fallback (amenity, ST_X/ST_Y)
    │       _sync_plan_target_to_destination() → skips if source == "user_configured"
    │
    └── MobilityBlock.run()
            Reads memory.status["destination"] (user_configured | plan | none)
            Source-based budget cap (Stage 13):
                source == "user_configured" → explore_budget = min(archetype_budget, 1)
            Distance-based budget reduction:
                dist_to_dest < _ALMOST_THERE → explore_budget = 0
                (_GETTING_CLOSE tier removed — superseded by source cap)
            dijkstra_hops() → BFS hop count for urgency tier
            dijkstra_next_node() → metric-weighted shortest path (length_m × weight_mult)
            Searches model.node_to_edges[current_node] (full, not filtered) for dijkstra_edge_data
            force_dijkstra = (explore_steps >= budget) AND (dijkstra_edge_data is not None)
            ├── force_dijkstra=True → move, reset explore_steps, log [forced-dijkstra]
            └── force_dijkstra=False → LLM prompt with:
                    visit tags (GPS edge exempt, direction-agnostic),
                    [SHORTEST PATH TO DESTINATION] label (direction-agnostic),
                    GPS RULE (must follow unless hunger>0.9 or energy<0.1),
                    urgency tier, bearing hint, plan_context (goal, time_of_day, preferences)
                    → LLM returns choice index
                    → fallback to Dijkstra / least-visited on failure
                    → update visited_edges, log on_path/fallback/data_sources
    │
    ▼
NeedsBlock.run()
    Decay: hunger/energy/social/comfort at 0.003/step (~2.8h to full/empty)
    Visual satisfaction: every 5 steps if street_perception available
    Amenity satisfaction: only if closest amenity dist ≤ 30m (AMENITY_SATISFACTION_RADIUS_M)
    │
    ▼
GeoParquetRecorder.record()
    AgentRecord: position, archetype, perception_mode, perception_available,
                 thought_stream (with metadata), on_proposed_path, decision_reason
    _flush_to_parquet: drop_duplicates(agent_id, step) before write
    Flushes to: tracking_data/<date>/<archetype>/<mode>/<session>_<mode>.parquet
```

---

## Open Items

| Item | Description | Status |
|---|---|---|
| explore_budget vs. destination source | Tourist budget=3 causes diverging walk for user-configured destinations. Fixed in Stage 13: user_configured destinations cap budget=min(archetype,1) throughout the journey. | **Closed — Stage 13** |
| Amenity satisfaction proximity | NeedsBlock fired for any amenity within 150m — hunger reset from passing restaurants. Fixed in Stage 13: 30m hard threshold (AMENITY_SATISFACTION_RADIUS_M). | **Closed — Stage 13** |
| Duplicate step rows in parquet | 79–100 duplicate rows per recording. Fixed in Stage 13: drop_duplicates(agent_id, step) in _flush_to_parquet before write. | **Closed — Stage 13** |
| Decay rate calibration | DECAY_RATES 5× too fast (hunger full in 33min). Fixed in Stage 13: 0.003/step → full in ~2.8h. | **Closed — Stage 13** |
| time_of_day as clock | Phase `time_of_day` field is descriptive only — phases advance by `max_visits`, not simulation time. A step→time mapping would anchor agent behaviour to a realistic daily schedule. | Future work |
| Euclidean vs. hop-count urgency | `_ALMOST_THERE` threshold is Euclidean; hop count for urgency tier can diverge in one-way network segments. Both signals together cover the gap. `_GETTING_CLOSE` removed (Stage 13). | Acceptable |
| Dead-end topology oscillation | 2-degree nodes trap agents when anti-backtrack filter removes all candidates. Dijkstra correctly routes back through them but the reversal step consumes free-exploration budget. | Known |
| GPS RULE defection rate | ~36% of free steps ignore [SHORTEST PATH TO DESTINATION] even with hardened rule. Suspected cause: stale GPS labels from previous steps appearing in recent_moves history in the prompt. | Known |

---

## Key File Reference

| File | Role |
|---|---|
| `Backend/LLM/Thinking/blocks/mobility_block.py` | Explore budget, distance reduction, Dijkstra edge lookup, LLM prompt call |
| `Backend/LLM/Thinking/blocks/plan_block.py` | Plan phase state machine, en_route_stops, target resolution, destination sync |
| `Backend/LLM/Thinking/blocks/needs_block.py` | Hunger/energy/social decay (0.003/step), amenity satisfaction ≤30m guard, visited_amenities tracking |
| `Backend/LLM/Thinking/prompts.py` | `mobility_decision_prompt()` — visit tags, GPS RULE, urgency tiers, bearing hint, plan context |
| `Backend/Agent/model.py` | Network graph (bidirectional, metric weights), `dijkstra_next_node()`, `dijkstra_hops()`, `_get_candidate_edges()` |
| `Backend/Agent/geoparquet_recorder.py` | `AgentRecord` dataclass, `from_agent()` factory, parquet flush with dedup guard |
| `test/agent_lab_server.py` | Test server, destination source tagging, single-pass `/planned-path`, StreetPLM cache |
| `test/Frontend/agent_lab.html` | Live map, thought stream panel with decision badges |
| `test/plans.json` | Agent profiles (personality + daily_plan + en_route_stops per archetype) |
