# LLM-Based Urban ABM with Perception-Driven Planning: Research Framework

## System Overview

An agent-based urban simulation where pedestrian agents follow archetype-specific daily plans, making navigation decisions informed by street-level perception data derived from Vision-Language Model (VLM) analysis of street view images. The system supports four perception modes to isolate the contribution of visual perception data to agent behavior.

---

## Final Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Plan adaptability | **Fixed** | Deterministic, reproducible, clean experimental variable |
| Target override | **Plan adapts around target** | Preserves archetype behavior while respecting user input |
| Phase targets | **Amenity types** | Flexible, works with existing DuckDB queries, generalizable |
| Needs influence | **Movement only** | Clean separation; existing NeedsBlock handles amenity effects |
| Perception preferences | **JSON config** | Editable research artifact, enables A/B testing |
| Track encountered qualities | **Yes** | Enables plan adherence measurement |
| `perception_avoid` | **Hard filter + forced-deviation fallback** | Strict rule, clean experimental variable |
| Rule-based mode | **Plan works in all modes** | Strict enforcement in rule_based as baseline |
| Plan files | **Two copies** | `test/plans.json` for experiments, `Backend/.../plans.json` for main system |

---

## Architecture

```
Street View Images
    ↓
Perception-LM-1B (VLM) — offline analysis
    ↓
JSON analysis files (16 fields per location)
    ↓
DuckDB spatial table (streetview_perception)
    ↓
Agent step: get_nearby_perception(geometry)
    ↓
street_perception dict → PlanBlock → MobilityBlock → LLM decision
    ↓
GeoParquet recording (full chain: perception → plan → decision → needs)
```

### Perception Modes

| Mode | Amenities | Perception Images | LLM | Plan Enforcement |
|------|-----------|-------------------|-----|------------------|
| `both` | Yes | Yes | Yes | Soft (LLM-guided) |
| `perception` | No | Yes | Yes | Soft (LLM-guided) |
| `amenities` | Yes | No | Yes | Soft (LLM-guided) |
| `rule_based` | No | No | No | Hard (strict filter) |

---

## Research Questions & Testable Metrics

### RQ1: Do perception images change agent route choices?

**Hypothesis:** Agents with access to perception data choose qualitatively different routes than agents without.

**Test:** Run identical plans (same start, target, archetype) across `both` vs `amenities` mode.

**Metrics:**
- **Route divergence:** % of edges chosen differently between modes
- **Perception quality exposure:** Total vegetation score, lighting score, building condition score along route
- **Route length difference:** Absolute and relative difference in total distance
- **Forced deviation count:** How often did each mode have no valid edges?

**Verification:**
```python
# Query GeoParquet
route_both = df[df['perception_mode'] == 'both'].sort_values('step')
route_amenities = df[df['perception_mode'] == 'amenities'].sort_values('step')
divergence = (route_both['edge_id'] != route_amenities['edge_id']).mean()
```

---

### RQ2: Does perception-aware planning produce better routes?

**Hypothesis:** Agents using perception preferences in their plan choose streets with higher walkability, better lighting, more vegetation.

**Test:** Compare `both` mode vs `rule_based` mode with identical plans.

**Metrics:**
- **Mean vegetation exposure:** Average vegetation field score along route
- **Mean lighting quality:** Average lighting_atmosphere sentiment
- **Mean building condition:** Average building_condition score
- **Pedestrian activity exposure:** Average pedestrian_activity score
- **Plan adherence score:** % of steps where encountered qualities matched plan preferences

**Verification:**
```python
# Plan adherence = steps where encountered qualities ⊇ plan preferences
adherence = (encountered_qualities >= plan_preferences).mean()
```

---

### RQ3: Can agents navigate by visual cues alone?

**Hypothesis:** Agents in `perception` mode (no amenities) can still reach their target using visual landmarks.

**Test:** Run agent in `perception` mode with a target. Measure success rate and efficiency.

**Metrics:**
- **Target reach rate:** % of runs that reached the target
- **Steps to target:** Average steps compared to `both` mode baseline
- **Path efficiency:** Actual distance / shortest path distance
- **Wandering index:** Number of unique edges visited / minimum edges needed

**Verification:**
Compare `perception` mode results against `both` mode baseline for the same target.

---

### RQ4: Do archetype-specific perception preferences produce distinguishable behavior?

**Hypothesis:** A tourist's plan (prefers interesting buildings, vegetation) produces measurably different routes from a commuter's plan (prefers direct routes, avoids distractions).

**Test:** Run different archetypes with identical start/target. Compare route qualities.

**Metrics:**
- **Route overlap:** % of shared edges between archetypes
- **Perception profile divergence:** Difference in mean vegetation, lighting, building scores
- **Time to target:** Steps taken by each archetype
- **Exploration index:** Number of unique edges visited beyond shortest path

**Verification:**
```python
tourist_route = df[df['archetype'] == 'tourist']
commuter_route = df[df['archetype'] == 'commuter']
overlap = len(set(tourist_route['edge_id']) & set(commuter_route['edge_id']))
```

---

### RQ5: How often do forced deviations occur, and where?

**Hypothesis:** Forced deviations (no valid edges after filtering) cluster in specific urban areas (e.g., industrial zones, poorly lit streets).

**Test:** Run perception-aware agents across the city. Map forced deviation locations.

**Metrics:**
- **Forced deviation rate:** % of steps with `forced_deviation: true`
- **Spatial clustering:** Do forced deviations cluster in specific neighborhoods?
- **Perception quality at deviation points:** What qualities caused the deviation?
- **Recovery time:** Steps taken to return to plan-aligned route after deviation

**Verification:**
```python
deviations = df[df['plan_json'].apply(lambda x: x.get('forced_deviation', False))]
# Spatial analysis of deviation locations
```

---

### RQ6: Does perception data improve need satisfaction?

**Hypothesis:** Agents with perception data achieve better need satisfaction through visual restoration (green spaces, pleasant streets).

**Test:** Compare need trajectories between `both` and `amenities` mode.

**Metrics:**
- **Energy trajectory:** Mean energy over time (visual restoration from green spaces)
- **Comfort trajectory:** Mean comfort over time (visual environment quality)
- **Need recovery rate:** How quickly needs recover after visiting amenities
- **Visual satisfaction contribution:** % of need changes attributed to visual environment vs. amenity visits

**Verification:**
```python
both_needs = df[df['perception_mode'] == 'both']['needs_json'].apply(json.loads)
amenities_needs = df[df['perception_mode'] == 'amenities']['needs_json'].apply(json.loads)
# Compare energy, comfort trajectories
```

---

### RQ7: Which perception fields are most influential in decision-making?

**Hypothesis:** Some perception fields (vegetation, lighting) have stronger influence on agent decisions than others (materials, signage).

**Test:** Correlate perception field values with edge choice frequency.

**Metrics:**
- **Field-choice correlation:** Correlation between each perception field value and edge selection probability
- **LLM attention analysis:** Which perception fields appear most often in `decision_reason` text?
- **Field importance ranking:** Rank perception fields by their predictive power for edge choice

**Verification:**
```python
# Extract perception fields from street_perception_json
# Correlate with edge choice frequency
from scipy.stats import spearmanr
for field in perception_fields:
    corr, p = spearmanr(df[field], df['edge_choice_frequency'])
```

---

### RQ8: Does the LLM follow the plan's perception preferences?

**Hypothesis:** The LLM respects plan preferences but occasionally overrides them for efficiency tradeoffs.

**Test:** Compare plan preferences with actual encountered qualities.

**Metrics:**
- **Preference adherence rate:** % of steps where encountered qualities match plan preferences
- **Override frequency:** How often does the LLM choose an edge that violates preferences?
- **Override justification quality:** Does `decision_reason` explain the override?
- **Override cost:** Extra distance/time when LLM overrides vs. follows plan

**Verification:**
```python
plan_prefs = df['plan_json'].apply(lambda x: x['current_phase']['perception_preferences'])
encountered = df['encountered_qualities']
adherence = (encountered.isin(plan_prefs)).mean()
```

---

### RQ9: How does perception mode affect path adherence to the shortest route?

**Hypothesis:** Perception-aware agents deviate more from the shortest path but report higher satisfaction.

**Test:** Compare path adherence across modes.

**Metrics:**
- **Path adherence rate:** % of edges on the Dijkstra shortest path
- **Detour distance:** Extra distance traveled vs. shortest path
- **Detour justification:** Does `decision_reason` explain the detour?
- **Satisfaction tradeoff:** Does extra distance correlate with higher need satisfaction?

**Verification:**
```python
adherence = df['current_plan_json'].apply(lambda x: x.get('on_proposed_path', False)).mean()
detour = (df['total_distance'] / df['shortest_path_distance']).mean()
```

---

### RQ10: Can the plan module generalize across different urban areas?

**Hypothesis:** The same plan config produces meaningful behavior in different neighborhoods.

**Test:** Run identical plans in different areas of Barcelona (or other cities).

**Metrics:**
- **Plan completion rate:** % of phases completed in each area
- **Forced deviation rate by area:** Which areas cause more deviations?
- **Perception quality variance:** How much do perception qualities vary across areas?
- **Behavior consistency:** Do agents exhibit similar patterns across areas?

**Verification:**
Run the same `plans.json` config with different start/target locations. Compare metrics.

---

## Data Collection & Analysis Pipeline

### GeoParquet Schema (Extended)

| Field | Type | Description |
|-------|------|-------------|
| `agent_id` | int | Unique agent identifier |
| `step` | int | Simulation step |
| `longitude`, `latitude` | float | Agent position |
| `edge_id` | int | Current walk network edge |
| `archetype` | str | Agent archetype |
| `perception_mode` | str | Current perception mode |
| `needs_json` | JSON | Current needs state |
| `cognition_state_json` | JSON | Mood, curiosity, fatigue |
| `current_plan_json` | JSON | Per-step movement decision |
| `plan_json` | JSON | Full plan state, current phase, encountered qualities |
| `street_perception_json` | JSON | Perception image analysis at location |
| `decision_reason` | str | LLM explanation for edge choice |
| `satisfaction_source` | str | "visual", "amenity", "combined", "none" |
| `satisfaction_reasoning` | str | LLM explanation for need changes |
| `visited_amenities_json` | JSON | Amenities visited so far |
| `nearby_amenities_json` | JSON | Amenities within range |
| `thought_stream_json` | JSON | Recent memory events |
| `is_fallback` | bool | Whether decision used fallback logic |
| `target_name` | str | Target destination name |
| `target_amenity_type` | str | Target amenity type |

### Analysis Notebooks

1. **Route Comparison:** Compare routes across perception modes
2. **Plan Adherence:** Measure how well agents follow their plans
3. **Perception Impact:** Correlate perception qualities with decisions
4. **Forced Deviation Mapping:** Spatial analysis of deviation points
5. **Need Satisfaction:** Compare need trajectories across modes
6. **Archetype Behavior:** Compare behavior patterns across archetypes

---

## Thesis Defense Checklist

### Quantitative Evidence
- [ ] Route divergence statistics across perception modes
- [ ] Plan adherence scores with confidence intervals
- [ ] Forced deviation rates and spatial clustering analysis
- [ ] Need satisfaction comparison (statistical significance tests)
- [ ] Perception field importance ranking
- [ ] Path adherence vs. satisfaction tradeoff analysis

### Qualitative Evidence
- [ ] Decision reason examples showing perception influence
- [ ] Forced deviation case studies with location context
- [ ] Archetype behavior narratives from GeoParquet recordings
- [ ] Visual comparison of routes on map (perception-aware vs. amenities-only)

### Ablation Studies
- [ ] `both` vs `amenities` — perception image contribution
- [ ] `both` vs `perception` — amenity contribution
- [ ] `both` vs `rule_based` — LLM contribution
- [ ] `rule_based` with plan vs without plan — plan module contribution

### Generalization
- [ ] Same plan across different neighborhoods
- [ ] Same plan across different archetypes
- [ ] Same plan across different start/target pairs

---

## Implementation Checklist

### Phase 1: Core Plan Module
- [x] Create `test/plans.json` with archetype-specific plans
- [x] Create `Backend/.../plans.json` for main system
- [x] Implement `PlanBlock` in `Backend/LLM/Thinking/blocks/plan_block.py`
- [x] Add `plan` to `kv_memory.py` DEFAULT_SCHEMA
- [x] Insert PlanBlock in dispatcher pipeline
- [x] Update `mobility_block.py` to read plan state
- [x] Update `prompts.py` with plan context

### Phase 2: Perception Integration
- [x] Add `perception_preferences` and `perception_avoid` to plan phases
- [x] Implement hard filter with forced-deviation fallback
- [x] Track encountered qualities at each step
- [x] Record plan deviation events

### Phase 3: Recording & API
- [x] Add `plan_json` to `AgentRecord` in geoparquet_recorder.py
- [x] Add `GET /api/agent/{id}/plan` endpoint
- [x] Add `GET /api/agent/{id}/plan-adherence` endpoint
- [x] Update recording to include plan data

### Phase 4: Analysis Tools
- [ ] Create analysis notebooks for each research question
- [ ] Create visualization scripts for route comparison
- [ ] Create forced deviation mapping notebook
- [ ] Create plan adherence scoring utility
