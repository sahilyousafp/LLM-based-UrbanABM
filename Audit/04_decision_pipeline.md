# 04 — Decision Pipeline

Each simulation step, every agent runs through four **thinking blocks** coordinated by a `BlockDispatcher`. This document explains what each block does, what it costs, and in what order they run.

**Key files:**
- `Backend/LLM/Thinking/dispatcher.py`
- `Backend/LLM/Thinking/blocks/needs_block.py`
- `Backend/LLM/Thinking/blocks/cognition_block.py`
- `Backend/LLM/Thinking/blocks/plan_block.py`
- `Backend/LLM/Thinking/blocks/mobility_block.py`
- `Backend/LLM/Thinking/block.py` — base class

---

## Execution Order

```
dispatcher.run(step, candidate_edges, nearby_amenities,
               street_perception, needs_new_edge,
               nearby_agents, nearby_transit, time_of_day)

Phase 1 — Parallel (mutually independent, no shared writes):
┌─────────────┐  ┌──────────────────┐  ┌───────────┐
│  NeedsBlock │  │  CognitionBlock  │  │ PlanBlock │
│  (always)   │  │  (every 10 steps)│  │ (always)  │
└──────┬──────┘  └────────┬─────────┘  └─────┬─────┘
       │                  │                   │
       └──────────────────┼───────────────────┘
                          │  asyncio.gather()
                          ▼
Phase 2 — Sequential (reads updated cognition from Phase 1):
┌──────────────────────────────────────────────────┐
│  MobilityBlock  (only if needs_new_edge == True) │
└──────────────────────────────────────────────────┘
```

Mobility runs after the other three because it reads the **updated** cognition state — the LLM reasoning about where to go next is informed by the agent's current mood/fatigue.

---

## BlockResult

Every block returns a `BlockResult` dataclass:

```python
@dataclass
class BlockResult:
    action:    str    # what happened: "needs_updated", "move_to_edge", "stay", …
    params:    dict   # action-specific data (e.g., {"edge_id": 1234, "direction": "forward"})
    reasoning: str    # one-sentence explanation (logged to stream memory)
    fallback:  bool   # True if rule-based fallback was used instead of LLM
```

---

## NeedsBlock

**Runs:** Every step  
**LLM calls:** Only at amenities (~20 tokens)  
**Source:** `Backend/LLM/Thinking/blocks/needs_block.py`

### What it does

```
Step 1: Weather modulation
    rain_mult  = 1.5 if rain_mm > 1.0 else 1.0
    heat_mult  = 1.3 if temp_c  > 30  else 1.0
    wind_mult  = 1.2 if wind_ms > 8   else 1.0

Step 2: Cross-need coupling multipliers (computed from pre-step values)
    hunger_energy_mult   = 1.0 + 0.3 * max(0, (hunger - 0.5) / 0.5)
        → hunger > 0.5 ramps energy drain up to ×1.3 at hunger=1.0
    comfort_energy_mult  = 1.0 + 0.2 * max(0, (0.5 - comfort) / 0.5)
        → comfort < 0.5 ramps energy drain up to ×1.2 at comfort=0.0
    energy_hunger_mult   = 1.0 + 0.2 * max(0, (0.3 - energy) / 0.3)
        → energy < 0.3 ramps hunger rise up to ×1.2 at energy=0.0
    social_energy_scale  = energy / 0.3 if energy < 0.3 else 1.0
        → crowd social bonus scales to zero as energy hits zero

Step 3: Decay needs each step (weather + cross-need coupling)
    hunger  += 0.003 * energy_hunger_mult
    energy  -= 0.003 * heat * hunger_energy_mult * comfort_energy_mult
    social  += 0.003                   (no cross-need coupling)
    comfort -= 0.003 * rain * wind

    Worst-case energy drain: 0.003 × 1.3(heat) × 1.3(hunger) × 1.2(comfort) ≈ 0.0061/step
    (~164 steps to exhaustion for a starving, uncomfortable agent in summer heat vs. ~333 baseline)

Step 4: Crowd bonus (rule-based, energy-scaled)
    If nearby_agents present:
        social -= min(0.05, len(agents) * 0.01) * social_energy_scale
        (exhausted agents benefit less from crowd proximity)

Step 5: Visual satisfaction (LLM, every 5 steps, if street_perception present)
    visual_satisfaction_prompt() → hunger/energy/social/comfort deltas
    (green spaces restore energy; lively streets reduce social need)

Step 6: Amenity satisfaction (LLM, if agent within 30m of an amenity)
    needs_evaluation_prompt() → hunger/energy/social/comfort deltas
    (café → hunger -0.2, social -0.15; park → energy +0.25, comfort +0.10)
    Fallback: AMENITY_NEED_MAP rule table if LLM fails
```

### Need Semantics

| Need | 0 means | 1 means |
|------|---------|---------|
| hunger | full | starving |
| energy | exhausted | energised |
| social | connected | lonely |
| comfort | uncomfortable | comfortable |

### Cross-Need Coupling Summary

| From → To | Mechanism | Threshold | Max effect |
|-----------|-----------|-----------|------------|
| hunger → energy drain | ×multiplier on energy decay | hunger > 0.5 | ×1.3 at hunger=1.0 |
| comfort → energy drain | ×multiplier on energy decay | comfort < 0.5 | ×1.2 at comfort=0.0 |
| energy → hunger rise | ×multiplier on hunger decay | energy < 0.3 | ×1.2 at energy=0.0 |
| energy → social gain | scales crowd bonus down | energy < 0.3 | ×0 at energy=0.0 |

---

## CognitionBlock

**Runs:** Every step (LLM every 10 steps; rule-based between)  
**LLM calls:** ~100 tokens every 10 steps  
**Source:** `Backend/LLM/Thinking/blocks/cognition_block.py`

### What it does

```
Every step (rule-based, cheap):
    fatigue   += 0.002    (slow accumulation between LLM calls)
    curiosity -= fatigue * 0.002   (fatigue suppresses curiosity)

Every 10 steps (LLM):
    cognition_update_prompt(
        archetype, agent_profile (age, preferences),
        current_needs, current_cognition,
        recent_history (last 15 events), street_perception,
        memory_context, time_of_day
    )
    → {"mood": "...", "curiosity": 0.X, "fatigue": 0.X, "summary": "2-3 sentence first-person thought"}

    Mood options (14): happy, neutral, tired, curious, bored, energised,
                       social, focused, irritable, content, restless,
                       anxious, relieved, absorbed
```

The system prompt enforces specificity: the LLM must reference actual events from the recent history (named places, decisions, sensations). Generic phrases like "as the day progresses" or "the urban environment" are explicitly banned. Time of day is context, not the story.

### Mood ↔ Need Interactions (all 4 needs, bi-directional)

| Need state | Mood / cognition impact |
|------------|------------------------|
| hunger > 0.7 | irritable, shortened patience, hard to enjoy surroundings |
| hunger < 0.3 | satisfied, slightly more content |
| energy < 0.3 | tired/exhausted, curiosity drops sharply, fatigue rises fast |
| energy > 0.8 | alert, curiosity boosted, fatigue recovers slightly |
| social > 0.7 (unmet) | restless, lonely undertone, craves contact |
| social < 0.3 (satisfied) | warm, social mood, slight energy boost |
| comfort < 0.3 | anxious, uneasy, difficulty concentrating on surroundings |
| comfort > 0.8 | content, relaxed curiosity, absorbs environment better |

### Memory Consolidation (runs at archetype-specific intervals)

```
Every N steps (archetype-specific):
    memory_summary_prompt(recent events) → short episode narrative
    Append to memory_summaries list (trim to max_summaries)

Residents only, every 60 steps:
    memory_consolidation_prompt(all summaries) → unified long-term narrative
    Write to memory_summary_unified (never deleted)
```

---

## PlanBlock

**Runs:** Every step  
**LLM calls:** None (fully rule-based)  
**Source:** `Backend/LLM/Thinking/blocks/plan_block.py`

### What it does

Each archetype has a daily plan defined in `Backend/LLM/Thinking/plans.json`. The plan is a sequence of phases:

```json
{
  "archetype": "tourist",
  "phases": [
    {
      "goal": "Get breakfast",
      "amenity_type": "cafe",
      "perception_preferences": ["lively pedestrian activity"],
      "perception_avoid": []
    },
    {
      "goal": "Visit a landmark",
      "amenity_type": "attraction",
      "perception_preferences": ["interesting architecture"],
      "perception_avoid": [{"field": "pedestrian_activity", "value": "empty"}]
    }
  ]
}
```

PlanBlock:
1. Reads current phase from `memory.status["plan"]`
2. Checks if the current phase is complete (agent reached target amenity type)
3. If complete: advance to next phase, resolve new target from DuckDB (`SELECT ... FROM amenities WHERE amenity = phase.amenity_type ORDER BY distance LIMIT 1`)
4. Writes updated plan + resolved destination to memory
5. Applies `perception_avoid` as a **hard filter** on candidate edges (removes edges that match avoid criteria from the list passed to MobilityBlock)

---

## MobilityBlock

**Runs:** Only when `needs_new_edge == True`  
**LLM calls:** Up to `LLM_CALLS_PER_STEP` (default 50) per simulation step  
**Source:** `Backend/LLM/Thinking/blocks/mobility_block.py`

### Exploration Budget

The "free vs forced" cycle controls how directly agents move toward their destination:

| Archetype | Budget | Pattern |
|-----------|--------|---------|
| Commuter | 0 | Always Dijkstra (pure shortest-path) |
| Resident | 1 | F → D → F → D (one free, one forced) |
| Student | 2 | FF → D → FF → D |
| Tourist | 3 | FFF → D → FFF → D |

**Free step** = LLM chooses any candidate edge freely.  
**Forced step** = agent must take the Dijkstra-optimal next step toward destination (no LLM choice).

This guarantees progress toward the destination while still allowing exploration.

### Decision Flow

```
1. Read explore_steps from memory
2. Compute Dijkstra next edge toward destination (if any)
3. Is explore_steps >= explore_budget?
   YES → Force Dijkstra, reset explore_steps to 0
   NO  →
       a. Check LLM budget guard:
             If this agent is in the first LLM_CALLS_PER_STEP of the step:
                 Use LLM → mobility_decision_prompt(…) → JSON choice
             Else:
                 Use rule-based → least-visited candidate edge
       b. Increment explore_steps
4. Apply chosen edge: write to memory, log to stream
```

### Distance-Based Budget Reduction

As an agent closes in on its destination, the explore budget shrinks to ensure convergence:

```python
if dist_to_destination <= ALMOST_THERE_METRES:
    explore_budget = 0    # pure Dijkstra in final approach
```

Thresholds: tourist 60m, resident 40m, student 50m.

---

## LLM Budget Guard

The budget guard is the single most important cost-control mechanism:

```python
# In dispatcher.py / model/city_model.py:
LLM_CALLS_PER_STEP = int(os.getenv("LLM_CALLS_PER_STEP", 50))

# Only the first N agents (after shuffle) call LLM for mobility
# The rest use rule-based fallback
```

At 50 agents/step × ~60 tokens/call = ~3,000 tokens/step.  
At 500 agents fully rule-based = 0 tokens/step.  
Set `LLM_CALLS_PER_STEP=0` for pure rule-based (deterministic, ~10ms/step).

---

## Data Flow Summary

```
Step input:
  candidate_edges      ← from _get_candidate_edges() in model/agent.py
  nearby_amenities     ← DuckDB query
  street_perception    ← DuckDB query (nearest streetview point)
  nearby_agents        ← agent_snapshot filter
  nearby_transit       ← DuckDB query (ext_transit_stops)
  time_of_day          ← CityModel.time_of_day property

Parallel phase:
  NeedsBlock   → updates needs in KVMemory
  CognitionBlock → updates cognition_state in KVMemory
  PlanBlock    → updates plan + destination in KVMemory

Serial phase:
  MobilityBlock (reads updated cognition) → picks edge
               → returns BlockResult(action="move_to_edge", params={edge_id, direction, geom})

Applied in model/agent.py:
  _apply_mobility(params) → updates current_edge, resets position_along_edge
  _advance_along_edge()   → updates geometry (lon, lat)
```

---

**Next:** [`05_llm_integration.md`](05_llm_integration.md) — the LLM client, provider abstraction, and prompt templates.
