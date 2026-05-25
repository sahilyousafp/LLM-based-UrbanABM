# Vision–Action Trial Scenarios
## LLM-Based Urban ABM — Barcelona Eixample

**Author:** Urban ABM Developer  
**Date:** May 2026  
**Branch:** `single_agent`  
**Purpose:** Structured research trial designs grounding vision-action coupling in the recorded parquet data. Intended as a companion to `agent_behaviour_log.md` for thesis methodology.

---

## 1. Introduction

### 1.1 Why Pedestrian Turn-by-Turn Simulation Matters

Pedestrian movement is not random. Every step a person takes in an urban environment is the output of a cognitive process that integrates spatial memory, physiological need, social cues, and moment-to-moment visual perception. Understanding this process — not just the aggregate flow, but the individual decision at each junction — has direct consequences for how cities are designed, how autonomous systems share space with humans, and how emergencies are managed.

Turn-by-turn simulation captures what aggregate flow models cannot: the decision, not just the trajectory. When an agent pauses at a junction and chooses a shaded, tree-lined street over a direct but exposed route, that choice encodes information about thermal comfort preference, aesthetic response, and risk tolerance. Repeated across thousands of agents and thousands of steps, this produces the spatial fingerprint of a city's pedestrian culture — data that is structurally invisible in origin-destination matrices or heatmaps.

**Real-world applications of turn-by-turn pedestrian simulation:**

| Application | How simulation helps |
|---|---|
| Emergency evacuation planning | Identifies bottlenecks when agents with different mobility profiles compete for the same exits; tests counter-flow signage |
| Autonomous vehicle pedestrian prediction | Training data for AV models to anticipate human crossing decisions at unsignalised intersections |
| Smart signal timing | Optimises pedestrian phase duration based on simulated agent arrival patterns and needs states |
| Tourism pressure management | Predicts which streets will saturate under high-volume tourist archetypes navigating between attractions |
| Active transport promotion | Tests which infrastructure changes (shade, seating, visual quality) shift agent route choices toward healthier paths |
| Retail district design | Simulates customer journey through shopping environments; identifies dead zones and attractor edges |
| Accessibility auditing | Reveals which network nodes become impassable when mobility-limited archetypes are added |
| Urban heat island mitigation | Routes agents through cooler, vegetated corridors; quantifies uptake as a function of visual perception weight |

### 1.2 The Role of Vision in Pedestrian Behaviour

Classical pedestrian models — the Social Force Model (Helbing & Molnár 1995), ORCA, and their descendants — treat agents as particles responding to repulsive and attractive force fields. They are accurate for dense crowd flow but have no mechanism for encoding why a pedestrian chose the left fork over the right: there is no visual scene, no aesthetic preference, no remembered narrative. The agent does not *see* the street.

Computer vision research has produced trajectory prediction models (LSTM, Social-GAN, Trajectron++) that infer future positions from observed past positions, but these models are also blind to the visual environment — they predict where humans *will* go, not why. They cannot answer: would the agent have made the same choice if the street looked different?

LLM-based agents answer this by grounding each navigation decision in natural language descriptions of the visual scene. When the agent reads `"A narrow, sun-drenched passage between ornate modernist facades; outdoor seating visible ahead; low foot traffic"`, it reasons the same way a human would — mapping visual content to behavioural preference. This is the research gap this system occupies.

The key question this trial series investigates: **does the visual environment, as encoded by StreetPLM analysis, produce measurable, systematic, and interpretable effects on LLM agent navigation decisions?**

---

## 2. Prior Art — Rule-Based Simulation and Reinforcement Learning

The following projects demonstrate the progression from rule-based pedestrian and traffic simulation toward RL-enhanced agents, and have been validated against real-world behaviour. They provide the methodological baseline against which this system's LLM-driven approach should be evaluated.

### 2.1 Social Force Model Baselines

**PySocialForce** — Python implementation of Helbing's Social Force Model.
```
https://github.com/svenkreiss/pysocialforce
```
Pure rule-based. Agents follow attraction/repulsion forces. No perception, no memory, no individual goals. Useful as a lower-bound behavioural baseline: what trajectory emerges with zero cognitive modelling?

**Mesa** — Python Agent-Based Modelling framework used in thousands of urban studies.
```
https://github.com/projectmesa/mesa
```
Rule-based ABM. Agents follow deterministic or stochastic rules. No visual reasoning. The dominant framework for peer-reviewed urban ABM; comparison against Mesa-based pedestrian models is the standard methodological test for any new approach.

### 2.2 Rule-Based Simulation + Reinforcement Learning

**SUMO-RL** — SUMO traffic simulator (rule-based road network physics) combined with RL for signal timing optimisation.
```
https://github.com/LucasAlegre/sumo-rl
```
Real-world validated: RL signal timing trained in SUMO has been deployed in real intersections (Barcelona, Cologne). Demonstrates the paradigm this system extends: a physically accurate rule-based simulator provides the environment; an intelligent policy (here, RL; in this system, LLM) provides the decision-making. The pedestrian analogue is directly applicable — SUMO models pedestrians as rule-following particles; an LLM layer would give each pedestrian a perceiving, reasoning mind.

**FLOW** — Berkeley's traffic RL framework. Rule-based vehicle dynamics (SUMO/Aimsun backend) + RL-trained controllers.
```
https://github.com/flow-project/flow
```
Used to solve real mixed-autonomy freeway problems (ring-road oscillation damping with a single autonomous vehicle). Demonstrates that a hybrid rule-based/RL architecture outperforms pure RL (no simulation grounding) and pure rule-based (no adaptive policy). The architecture maps: rule-based = Dijkstra + explore budget in this system; RL policy = LLM reasoning module.

**CrowdNav** — IROS 2019. Social Force Model (rule-based baseline crowd) + RL-trained robot navigation policy.
```
https://github.com/vita-epfl/CrowdNav
```
The robot must navigate through a crowd of SFM pedestrians using RL. The pedestrian crowd is rule-based; the ego agent is RL-trained. Real-world validated in physical robot experiments. Directly relevant: this system inverts the relationship — the environment rules (Dijkstra, explore budget, anti-backtrack) are fixed; the agent's perception-to-action mapping (LLM) is the learned/prompted policy under study.

### 2.3 The LLM Bridge

None of the above systems can answer the visual preference question. SUMO-RL optimises flow; CrowdNav avoids collisions; FLOW damps oscillations. None encode: *given a visual scene, which edge does a curious tourist choose?*

This system's contribution is the perception–cognition–action chain:

```
StreetPLM visual analysis → LLM reasoning → edge choice → movement → parquet record
```

The parquet files are the ground truth for isolating each link in this chain.

---

## 3. Trial Scenarios

Each trial is designed to be runnable with the current `agent_lab_server.py` infrastructure and analysable from existing parquet data. The **Data columns** field specifies the exact parquet fields used for analysis.

---

### Trial 1 — Perception Toggle: Does Vision Cause GPS Defection?

**Research question:** Does providing StreetPLM visual perception data increase the rate at which the LLM deviates from the GPS-labeled optimal edge?

**Motivation from recorded data:**
In `20260525_111744_both.parquet` (tourist, 387 steps): of 234 off-path move-steps, **198 (85%) occurred when `perception_available=True`**. In the best recording (123 steps): 30 off-path steps, all with perception, but only 26% defection rate. The difference suggests perception interacts with the explore-budget system, not that perception alone causes defection.

**Design:**

| Condition | `nav_mode` | `perception_mode` | `explore_budget cap` | n runs |
|---|---|---|---|---|
| A — GPS only, no vision | `gps` | `amenities` | `user_configured` (=1) | 5 |
| B — GPS only, with vision | `gps` | `both` | `user_configured` (=1) | 5 |
| C — No GPS, no vision | `none` | `amenities` | — | 5 |
| D — No GPS, with vision | `none` | `both` | — | 5 |

**Independent variable:** `perception_mode` (amenities-only vs full StreetPLM)  
**Dependent variables:**
- GPS compliance rate: `on_proposed_path` column, proportion True across move-steps
- Steps to destination (arrival detection via `decision_reason` containing "Reached destination")
- GPS defection events: steps where `decision_reason` contains "SHORTEST PATH" AND `on_proposed_path = False`

**Data columns:**
```
perception_available, current_plan_json.on_proposed_path, decision_reason,
is_fallback, thought_stream_json[topic=mobility].metadata.on_path
```

**Hypothesis:** Condition B will show higher GPS defection rate than Condition A, because the visual scene description provides the LLM with positive aesthetic reasons to prefer non-GPS edges. The magnitude of this effect is the measure of visual salience's influence on navigation choice.

**Expected quantitative outcome (based on existing data):**
- Condition A: on-path rate ≈ 65–75% (forced steps dominate; LLM free steps follow GPS with no competing signal)
- Condition B: on-path rate ≈ 55–65% (visual novelty competes with GPS label on free steps)
- Difference (GPS defection rate driven by vision): ~10–15 percentage points

---

### Trial 2 — Archetype Navigation Signature

**Research question:** Does each archetype produce a statistically distinct spatial signature, and does the signature hold across multiple runs of the same destination?

**Motivation:** The explore-budget system encodes behavioural personality as an integer (0–3 free steps per forced Dijkstra step). Trial 2 tests whether this produces measurably different spatial behaviour, not just faster/slower convergence.

**Design:**
Fix destination, starting position, and nav_mode. Run each archetype 5 times.

| Archetype | Budget | Expected behaviour |
|---|---|---|
| Commuter | 0 | Pure Dijkstra — minimal edge diversity |
| Resident | 1 | F→D cycle — moderate coverage, reliable convergence |
| Student | 2 | FF→D cycle — broader coverage, slower convergence |
| Tourist | 1 (user_configured cap) | F→D with visual preference — similar to resident but vision-driven choices |

**Dependent variables:**
- **Edge diversity index:** unique edge IDs / total steps (higher = broader exploration)
- **Convergence profile:** Euclidean distance to destination at steps 25, 50, 100, 150, 200
- **Directional entropy:** Shannon entropy of compass bearings of chosen edges (higher = less directional bias)
- **Amenity visit rate:** steps where `satisfaction_source` ∈ {amenity, combined} / total steps

**Data columns:**
```
edge_id (per step), longitude, latitude, target_lon, target_lat,
satisfaction_source, archetype, step, current_plan_json.on_proposed_path
```

**Analysis:**
```python
# Edge diversity index
edge_diversity = gdf['edge_id'].nunique() / len(gdf)

# Convergence profile at fixed checkpoints
checkpoints = [25, 50, 100, 150, 200]
for s in checkpoints:
    row = gdf[gdf['step'] <= s].iloc[-1]
    dist = haversine(row.latitude, row.longitude, tlat, tlon)
```

**Hypothesis:** Commuter will show edge diversity < 0.15 (repeated Dijkstra edges). Tourist and student will show diversity > 0.30. Resident will be intermediate (0.15–0.25). Convergence profiles will be monotonically decreasing for commuter; oscillating-but-convergent for resident/tourist with budget=1; diverging for tourist with budget=3 (pre-Stage13 baseline comparison).

---

### Trial 3 — Stale GPS Label Persistence

**Research question:** How many steps after a GPS label was last valid does the LLM continue citing it as justification for edge choice?

**Motivation from recorded data:**
In `20260525_111744_both.parquet` at steps 210–220 (agent at 126–132m from destination), the agent repeatedly chose edge 508 citing `"Edge 508 is marked [SHORTEST PATH TO DESTINATION]"` — but `on_proposed_path=False`, meaning the current Dijkstra had updated to a different optimal edge. The LLM was reading stale GPS labels from the `recent_moves` history in its prompt (last 5 mobility events). This drove the agent from 126m to 217m — a **91m divergence caused by a stale label**.

**Design:**
Post-hoc analysis of existing recordings. No new runs required.

**Algorithm:**
For each off-path step where `decision_reason` contains "SHORTEST PATH" or "GPS" or "marked":
1. Identify the step when `dijkstra_edge_id` last changed (tracked via forced-step edge transitions)
2. Count how many subsequent LLM steps cited the old GPS edge
3. Measure Euclidean distance drift during the stale-label period

**Data columns:**
```
step, decision_reason, current_plan_json.on_proposed_path,
current_plan_json.target_edge_id, thought_stream_json[topic=mobility].metadata
```

**Pseudo-code:**
```python
last_gps_edge = None
stale_citation_chain = []

for _, row in gdf.iterrows():
    cp = json.loads(row['current_plan_json'])
    current_dijkstra_edge = cp.get('target_edge_id')
    reason = row['decision_reason'] or ''
    
    if current_dijkstra_edge != last_gps_edge:
        # GPS path updated
        if stale_citation_chain:
            record_chain(stale_citation_chain)
        stale_citation_chain = []
        last_gps_edge = current_dijkstra_edge
    
    if 'SHORTEST PATH' in reason and not cp.get('on_proposed_path', False):
        stale_citation_chain.append(row['step'])
```

**Hypothesis:** The LLM will cite a stale GPS label for 3–8 consecutive steps before either the forced step corrects it or the label drops out of the 5-event `recent_moves` window. Each stale-label episode will correlate with 30–100m of net divergence.

**Research implication:** If confirmed, this identifies a concrete failure mode of using LLM stream memory as a navigation cue. The fix is either: (a) strip GPS labels from the `recent_moves` history in the prompt (only show current step's label), or (b) add a "label expiry" note when the Dijkstra edge changes.

---

### Trial 4 — Need-Driven Detour Authenticity

**Research question:** When a need threshold is crossed, does the agent reliably route to a matching amenity within a physically plausible distance, and does it return to the original destination afterward?

**Motivation:** With pre-Stage13 decay rates (0.015/step), needs went to 0 within 35 steps with no real detour — the agent was "eating" from passing restaurants at 150m. With Stage13 rates (0.003/step) and 30m satisfaction guard, a meaningful detour should now require ~180 steps before hunger peaks and the agent stops at a café within 30m.

**Design:**

| Condition | Decay rate | Satisfaction radius | Expect |
|---|---|---|---|
| Pre-Stage13 baseline | 0.015/step | 150m | Hunger → 0 in ≤35 steps without detour |
| Stage13 calibrated | 0.003/step | 30m | Hunger peaks ~180 steps; detour to café ≤30m away |

**Dependent variables:**
- Steps from start until first `satisfaction_source = amenity`
- Distance from agent to amenity at satisfaction event (`nearby_amenities_json[0].dist`)
- Whether agent returns to original destination after detour (tracked via `target_name` field)
- Need level (hunger) at moment of satisfaction: `needs_json.hunger`

**Data columns:**
```
step, satisfaction_source, satisfaction_reasoning, needs_json,
nearby_amenities_json, target_name, thought_stream_json[topic=amenity_visit]
```

**Analysis:**
```python
# Find first genuine amenity stop
for _, row in gdf.iterrows():
    if row['satisfaction_source'] in ('amenity', 'combined'):
        nearby = json.loads(row['nearby_amenities_json'])
        if nearby and nearby[0].get('dist', 9999) <= 30:
            hunger_at_stop = json.loads(row['needs_json'])['hunger']
            print(f"Step {row['step']}: stopped at {nearby[0]['name']} "
                  f"({nearby[0]['dist']:.0f}m), hunger={hunger_at_stop:.2f}")
            break
```

**Hypothesis:** Stage13 recordings will show first genuine amenity stop after step 150 (±40), with agent within 30m of the amenity and hunger ≥ 0.55 (en_route_stop trigger threshold). Pre-Stage13 recordings will show satisfaction events at steps 2–10, agent 80–150m from amenity, and hunger values near 0 — indicating proximity-without-visit satisfaction that is behaviourally invalid.

---

### Trial 5 — Visual Preference Fingerprinting

**Research question:** When the LLM deviates from the GPS edge, is the chosen edge systematically associated with specific visual scene attributes (crowdedness, vegetation, spatial enclosure, building quality)?

**Motivation:** 134 novelty-driven off-path steps with `perception_available=True` exist in `20260525_111744_both.parquet`. The `decision_reason` texts reference edges as "interesting," "lively," or "having outdoor seating." If the visual scene data on *chosen* edges systematically differs from *rejected* GPS edges, this constitutes evidence that the LLM is making perception-consistent aesthetic choices — not just random deviations.

**Design:**
Post-hoc analysis. For each off-path LLM step with `perception_available=True`:
1. Identify the GPS-labeled edge (from `current_plan_json.target_edge_id`)
2. Retrieve the StreetPLM perception for the agent's position at that step (`street_perception_json`)
3. Extract perception fields: `crowdedness`, `greenery`, `spatial_character`, `lighting`
4. Classify: did the agent reason toward an aesthetic feature (keyword match in `decision_reason`)?

**Data columns:**
```
step, perception_available, street_perception_json,
current_plan_json.on_proposed_path, current_plan_json.target_edge_id,
decision_reason
```

**Coding schema for `decision_reason`:**
```python
AESTHETIC_KEYWORDS = {
    'vegetation': ['tree', 'green', 'park', 'garden', 'shade', 'vegetation'],
    'social':     ['lively', 'active', 'busy', 'outdoor', 'seating', 'cafe'],
    'novelty':    ['new', 'NEW', 'unvisited', 'novel', 'unexplored'],
    'spatial':    ['wide', 'narrow', 'open', 'enclosed', 'plaza'],
    'avoidance':  ['avoid', 'revisited', 'visited', 'already'],
}
```

**Hypothesis:** Novelty/avoidance keywords will dominate (>60% of off-path deviations). Aesthetic keywords (vegetation, social, spatial) will appear in 15–30% of cases, concentrated in steps where `crowdedness` or `greenery` fields in `street_perception_json` score above median. This would be the first quantitative evidence that StreetPLM visual descriptions are causally influencing edge choice.

**Research implication:** If aesthetic keywords appear at random relative to perception content, the LLM is not using visual data — it is pattern-matching on the novelty signal. If they correlate with specific perception fields, the vision-action chain is operational and measurable.

---

### Trial 6 — Budget Reduction: Convergence Rate Before and After Stage 13

**Research question:** Does capping `explore_budget=1` for user-configured destinations produce reliably faster convergence than `explore_budget=3`, and does it do so without eliminating exploratory spatial coverage?

**Motivation:** The "best" recording succeeded partly because `_GETTING_CLOSE=350m` happened to fire, effectively implementing budget=1. Stage 13 applies this cap from step 1. Existing recordings provide a natural pre/post comparison.

**Pre-Stage13 recordings (budget=3 throughout):**
- `20260525_111744_both.parquet`: 387 steps, min 55m, final 396m — failed
- `20260525_120332_both.parquet`: 302 steps, min 118m, final 286m — failed
- `20260525_102539_both.parquet`: 358 steps, min 284m — failed (reported in Stage 10)

**Post-Stage13 recordings:** To be collected after server restart.

**Dependent variables:**
- **Steps to arrival** (primary): steps until `decision_reason` contains "Reached destination"
- **Minimum distance reached**: min(haversine(pos, target)) across all steps
- **Convergence rate**: distance reduction per 50 steps (slope of distance-step curve)
- **Edge diversity preserved**: unique edges / total steps (test that budget=1 doesn't collapse exploration to a single path)

**Data columns:**
```
step, longitude, latitude, target_lon, target_lat,
decision_reason, edge_id, current_plan_json.on_proposed_path
```

**Quantitative target:**
Budget=1 (Stage13) should produce:
- Mean steps to arrival < 180 (vs >300 for budget=3 which never arrived)
- Edge diversity ≥ 0.15 (vs commuter's 0.08 — still exploring, just converging)
- Minimum distance ≤ 10m in ≥4/5 runs (vs 0/3 for pre-Stage13 tourist runs)

---

### Trial 7 — Direction Sense vs GPS: Complementarity or Conflict?

**Research question:** Does adding compass-bearing direction sense (`nav_mode=both`) improve convergence compared to GPS alone (`nav_mode=gps`), or does it introduce conflicting signals that reduce compliance?

**Motivation:** `nav_mode=both` gives the LLM two navigation signals:
1. `[SHORTEST PATH TO DESTINATION]` — graph-optimal (Dijkstra), can route "backwards" in Euclidean space through one-way topology
2. `[bearing: ~SW, dist: 340m]` — Euclidean direction hint, always points toward the destination

These can conflict in Eixample's one-way street topology. When Dijkstra routes the agent NE to eventually reach something SW, the bearing hint says "go SW." The LLM may prefer the bearing hint (intuitive) over the GPS label (topologically correct), causing divergence.

**Design:**

| Condition | `nav_mode` | Expected |
|---|---|---|
| A — GPS only | `gps` | Topologically correct but potentially counterintuitive routing |
| B — Direction only | `direction_sense` | Euclidean bias, no graph knowledge |
| C — Both | `both` | Possible conflict near one-way segments |
| D — Neither | `none` | Baseline free walk |

**Dependent variables:**
- Steps to arrival, minimum distance reached
- Frequency of "topological reversal" events: consecutive forced steps where Euclidean distance increases despite optimal Dijkstra routing
- LLM reasoning keyword: does `decision_reason` cite "bearing" or "direction" on steps where GPS label was available but not followed?

**Data columns:**
```
decision_reason, perception_mode (proxy for nav_mode), longitude, latitude,
target_lon, target_lat, current_plan_json.on_proposed_path
```

**Hypothesis:** `nav_mode=gps` will show better convergence when the Dijkstra path is correct (no topological reversal). `nav_mode=both` may perform worse near the destination when the bearing hint conflicts with the network-forced approach direction. `nav_mode=direction_sense` will converge to within ~100m but will stall when the bearing is correct but no direct edge exists in that compass direction.

---

## 4. Analysis Protocol

### 4.1 Standard Analysis Script

For all trials, the following base analysis applies to each parquet file:

```python
import geopandas as gpd
import pandas as pd
import json, math

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    a = (math.sin((lat2 - lat1) * math.pi / 360) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin((lon2 - lon1) * math.pi / 360) ** 2)
    return 2 * R * math.asin(math.sqrt(a))

def load_recording(path):
    gdf = gpd.read_parquet(path)
    gdf = gdf.drop_duplicates(subset=['agent_id', 'step']).sort_values('step').reset_index(drop=True)
    gdf['needs'] = gdf['needs_json'].apply(json.loads)
    gdf['current_plan'] = gdf['current_plan_json'].apply(json.loads)
    gdf['on_proposed_path'] = gdf['current_plan'].apply(lambda p: p.get('on_proposed_path', False))
    tlat = gdf['target_lat'].dropna().iloc[0]
    tlon = gdf['target_lon'].dropna().iloc[0]
    gdf['dist_to_target'] = gdf.apply(
        lambda r: haversine(r.latitude, r.longitude, tlat, tlon), axis=1
    )
    return gdf

def classify_step(row):
    r = str(row.get('decision_reason', '') or '')
    if 'Forced destination step' in r:
        return 'forced'
    elif row['on_proposed_path']:
        return 'llm_gps'
    elif row['current_plan'].get('goal') == 'move':
        return 'llm_off'
    else:
        return 'explore'

def summary(gdf):
    n = len(gdf)
    gdf['step_type'] = gdf.apply(classify_step, axis=1)
    counts = gdf['step_type'].value_counts()
    return {
        'n_steps': n,
        'arrived': any('Reached destination' in str(r) for r in gdf['decision_reason']),
        'min_dist_m': gdf['dist_to_target'].min(),
        'final_dist_m': gdf['dist_to_target'].iloc[-1],
        'on_path_pct': (gdf['on_proposed_path'].sum() / n) * 100,
        'edge_diversity': gdf['edge_id'].nunique() / n,
        'perc_steps_pct': (gdf['perception_available'].sum() / n) * 100,
        'step_types': counts.to_dict(),
    }
```

### 4.2 Metrics Reference

| Metric | Column(s) | Computation |
|---|---|---|
| GPS compliance rate | `current_plan_json.on_proposed_path` | `mean(on_proposed_path)` across move-steps |
| Edge diversity index | `edge_id` | `nunique(edge_id) / len(gdf)` |
| Convergence rate | `longitude`, `latitude`, `target_lon`, `target_lat` | slope of `dist_to_target` vs `step` (linear regression) |
| Vision defection rate | `perception_available`, `on_proposed_path` | `P(off-path | perc=True)` − `P(off-path | perc=False)` |
| Need satisfaction authenticity | `nearby_amenities_json[0].dist`, `satisfaction_source` | `dist <= 30m` when `satisfaction_source = amenity` |
| Stale GPS citation length | `decision_reason`, `current_plan_json.target_edge_id` | consecutive steps citing obsolete edge |
| Steps to arrival | `decision_reason` | first step containing "Reached destination" |

### 4.3 Existing Recording Inventory (Tourist, May 25 2026)

| File | Steps | Min dist | Arrived | Budget regime | Notes |
|---|---|---|---|---|---|
| `best/20260525_111332_both.parquet` | 123 | 4m | Yes | 3→1→0 (`_GETTING_CLOSE` fired) | Only successful run |
| `20260525_111744_both.parquet` | 387 | 55m | No | 3 throughout | GPS defection + stale label episodes |
| `20260525_120332_both.parquet` | 302 | 118m | No | 3 throughout | High on-path% (44%) but diverges at 118m |
| `20260525_102539_both.parquet` | 358 | 284m | No | 3 throughout | Stage 10 analysis baseline |

All four recordings use the same archetype (tourist), same destination (Verdaguer Café), and same `perception_mode=both`. They form a natural pre-Stage13 control group for Trial 6.

---

## 5. LLM-Based Simulation vs Traditional Systems — Beyond Visual Understanding

A natural question arises: if trajectory simulation is accurate and well-validated, and if visual understanding can be partially encoded through attraction/repulsion field adjustments, what does the LLM layer actually add? The answer goes beyond perception and applies to the foreseeable future of urban simulation as a research and planning instrument.

### 5.1 Zero-Shot Scenario Evaluation

Traditional models — Social Force Models, trajectory prediction networks, RL agents — are calibrated on historical data. They extrapolate forward from patterns that have already been observed. If the future contains something genuinely novel — a post-pandemic street reorganisation, a new type of public space, a policy that has never been implemented — the model applies a learned pattern that no longer applies. It cannot flag this; it simply extrapolates.

An LLM agent does not extrapolate. It evaluates. If you describe a street that has never existed — *"a formerly car-dominated arterial, now a linear park with cycling infrastructure and outdoor market stalls"* — the agent reasons about it using general social and spatial knowledge. It does not need empirical pedestrian data from that street before it can say something coherent about how a tourist or commuter would navigate it.

This is the only class of simulation that can produce behaviourally grounded predictions about spaces that do not yet exist, without first collecting training data from them. For urban planning — where the question is always about a proposed future, not a documented past — this is a structural advantage.

### 5.2 Interpretable Causality at the Decision Level

Traditional ABMs produce behaviour they cannot explain. You can observe that agents cluster near a park, but the model cannot tell you whether that is because of the shade, the seating, the social activity, or the route topology. The rule is hardcoded; the causal attribution is invisible.

Every step in this system produces a natural language justification recorded in `decision_reason`:

> *"Edge 742 is on the shortest path to the destination and passes a café, which aligns with my morning exploration goal and current hunger level."*

This is auditable. Reasons can be tagged, categorised, and counted systematically. Trial 5 (visual preference fingerprinting) is designed exactly for this: isolating which visual features appear as justifications for off-path choices, and whether they correlate with specific StreetPLM field values. A Social Force Model repulsive force coefficient cannot be communicated to a city council; a quoted agent reasoning trace can.

For policy communication, causal interpretability is not a convenience — it is what makes the simulation useful to stakeholders who are not modellers.

### 5.3 Heterogeneous Agent Populations Without Re-Parameterisation

In traditional ABMs, creating a new agent type requires writing new rules or fitting new parameter distributions to empirical data. A mobility-impaired elderly tourist and a student running late are two different rule sets that must be coded explicitly, calibrated separately, and validated independently. If you do not have empirical data for a population group, you cannot model it.

An LLM agent's identity comes from a text description in the prompt. Pace, curiosity, social preferences, physical constraints, cultural context — these emerge from the archetype profile, not from code. You can write a new archetype and observe whether its behaviour is coherent with the description, without implementing a new rule set. This means:

- Populations too small to calibrate empirically (first-time visitors from a specific cultural background, users of a newly introduced mobility device) can be plausibly modelled from description
- Behavioural hypotheses can be tested by perturbing the description rather than rewriting a model
- The same simulation infrastructure handles all archetypes — the differentiation is in the prompt, not the code

### 5.4 Integration of Qualitative and Contextual Knowledge

Traditional models accept structured inputs: coordinates, speeds, densities, force parameters. They cannot process a news article about a street festival, a cultural norm about personal space, a policy document about shared streets, or a temperature forecast that might change whether a pedestrian takes a shaded route. This information exists and shapes real behaviour, but there is no mechanism to introduce it into a parameterised rule system without manually translating it into numerical adjustments.

LLM agents process natural language, so any text-based information can influence behaviour directly. Event descriptions, weather narratives, time-of-day framing, cultural context — all can be injected into the prompt and will shape decisions. This makes the simulation responsive to the full texture of the real world, not only its geometric and physical structure.

### 5.5 The Honest Limitation: Stochasticity and Scale

None of the above advantages are free.

**Stochasticity:** LLM decisions are sampled from a distribution that cannot be directly inspected. The 36% GPS defection rate observed in these recordings — the same prompt producing different choices on different runs — is a direct consequence. Traditional models are deterministic or have well-characterised distributions; LLM decisions require ensemble runs to estimate behavioural tendencies. This complicates statistical comparison and increases the number of recordings needed per trial condition.

**Scale:** A Social Force Model simulates 5,000 agents × 1,000 steps in seconds. One LLM agent × 300 steps costs real API time and money. The system does not scale to crowd dynamics. For emergent physical phenomena — lane formation, arch formation at bottlenecks, wave propagation — traditional physics-based models remain the correct and only practical tool.

**The complementary framing:** LLM-based agents operate at the cognitive level; traditional models operate at the physical level. They answer different questions. The parquet trajectories this system generates are themselves trajectory data — they can be fed into Social Force Model calibration pipelines, used as training data for trajectory prediction networks, or aggregated into flow maps. The relationship is not competitive; it is a pipeline:

```
LLM agent reasoning
    → individual decision at each step
    → parquet-recorded trajectory
    → aggregate flow analysis (traditional tools)
    → policy-relevant spatial insight
```

The LLM layer provides the cognitive grounding for the individual trajectory. Traditional tools then scale those trajectories to the crowd level. Neither step replaces the other.

---

## 6. Framing for Thesis Contribution

This simulation system occupies a position between three research traditions:

**Rule-based ABM** (Mesa, Social Force Model): deterministic, interpretable, no perception  
**RL-based navigation** (CrowdNav, SUMO-RL, FLOW): adaptive, requires training data, no visual reasoning  
**LLM-based reasoning** (this system): zero-shot, visually grounded, verbally interpretable — but stochastic and not scalable to crowds

The vision-action trials are designed to answer the question that neither rule-based ABM nor RL can answer: **does the visual character of an urban street causally influence where a cognitively realistic agent goes?**

If Trial 5 (visual preference fingerprinting) confirms that specific StreetPLM fields predict edge choice, this is a finding with direct implications for urban design: street visual quality is not just an aesthetic consideration but an active driver of pedestrian routing. Designing streets that route pedestrians through green, active, well-lit spaces is not merely pleasant — it is measurably effective as a behavioural intervention.

If Trial 3 (stale GPS label persistence) confirms that LLM stream memory introduces systematic navigation error, this has implications for any system that uses LLM agents for wayfinding — the context window management of prior decisions is as important as the current decision prompt.

The thesis contribution is therefore not the ABM itself, but the measurement framework: parquet-recorded, step-level, perception-annotated trajectories that make the vision-to-action chain auditable, reproducible, and quantitatively comparable to rule-based and RL baselines.
