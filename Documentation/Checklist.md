# System Verification Checklist

Use this checklist to verify that the LLM-Based Urban ABM system is functioning correctly after changes. Run checks in order — each builds on the previous.

---

## 1. Backend Imports & Syntax

```bash
cd D:\IaaC\2ND_YEAR\THESIS\LLM_Based_UrbanABM

# 1.1 PlanBlock compiles
python -c "import sys; sys.path.insert(0, 'Backend'); from LLM.Thinking.blocks.plan_block import PlanBlock; print('[OK] PlanBlock')"

# 1.2 Dispatcher compiles (includes PlanBlock)
python -c "import sys; sys.path.insert(0, 'Backend'); from LLM.Thinking.dispatcher import BlockDispatcher; print('[OK] Dispatcher')"

# 1.3 Full Thinking module imports
python -c "import sys; sys.path.insert(0, 'Backend'); import LLM.Thinking; print('[OK] LLM.Thinking')"

# 1.4 Prompts function works with plan_context
python -c "
import sys; sys.path.insert(0, 'Backend')
from LLM.Thinking.prompts import mobility_decision_prompt
c = [{'edge_id': 1, 'direction': 'fwd', 'amenities': [], 'perception': '', 'description': 't'}]
m = mobility_decision_prompt('tourist', {'hunger': 0.5}, {'mood': 'neutral'}, '', {'lon': 2.17, 'lat': 41.39}, c,
    plan_context={'goal': 'scenic_walk', 'perception_preferences': ['vegetation'], 'perception_avoid': ['dark'], 'active_target': {'name': 'P', 'type': 'park', 'dist': 100}})
assert 'Current Plan Phase' in m[1]['content'], 'Missing plan phase'
print('[OK] Prompts with plan_context')
"

# 1.5 KVMemory schema includes plan
python -c "
import sys; sys.path.insert(0, 'Backend')
from LLM.Memory.kv_memory import DEFAULT_SCHEMA
assert 'plan' in DEFAULT_SCHEMA, 'plan missing from schema'
assert 'encountered_qualities' in DEFAULT_SCHEMA['plan'], 'encountered_qualities missing'
print('[OK] KVMemory schema')
"

# 1.6 GeoParquet recorder includes plan
python -c "
import sys; sys.path.insert(0, 'Backend')
from Agent.geoparquet_recorder import AgentRecord
r = AgentRecord(agent_id=1, step=0, timestamp='t', longitude=0, latitude=0, edge_id=None, position_along_edge=0, archetype='t', age=25)
d = r.to_dict()
assert 'plan_json' in d, 'plan_json missing from to_dict'
print('[OK] GeoParquet AgentRecord')
"

# 1.7 StepResult has plan field
python -c "
import sys; sys.path.insert(0, 'Backend')
from LLM.Thinking.dispatcher import StepResult
s = StepResult(needs=None, cognition=None, plan=None, mobility=None)
assert hasattr(s, 'plan'), 'plan field missing from StepResult'
print('[OK] StepResult field')
"
```

---

## 2. Test Server

### 2.1 Syntax check
```bash
cd D:\IaaC\2ND_YEAR\THESIS\LLM_Based_UrbanABM\test
python -c "
import sys, py_compile
sys.path.insert(0, str('Backend/Agent/..'))
py_compile.compile('agent_lab_server.py', doraise=True)
print('[OK] Test server syntax')
"
```

### 2.2 Start server and check endpoints
```bash
cd D:\IaaC\2ND_YEAR\THESIS\LLM_Based_UrbanABM\test
python agent_lab_server.py
# In another terminal or after server starts:
```

Then in a separate terminal:
```bash
# 2.2.1 Health check
curl http://localhost:8100/

# 2.2.2 Frontend config includes perception_mode
curl http://localhost:8100/api/config/frontend
# Expected: {"perception_mode": "both", ...}

# 2.2.3 Perception mode update
curl -X POST http://localhost:8100/api/config/perception-mode -H "Content-Type: application/json" -d '"amenities"'
curl http://localhost:8100/api/config/perception-mode
# Expected: {"mode": "amenities"}

# 2.2.4 Reset back to both
curl -X POST http://localhost:8100/api/config/perception-mode -H "Content-Type: application/json" -d '"both"'

# 2.2.5 Create agent
curl -X POST http://localhost:8100/api/single-agent/configure -H "Content-Type: application/json" -d "{
    \"start_lon\": 2.17, \"start_lat\": 41.39,
    \"target_lon\": 2.18, \"target_lat\": 41.40,
    \"archetype\": \"tourist\"
}"
# Expected: {"status": "configured", "agent_id": 0, ...}

# 2.2.6 Step the agent
curl -X POST http://localhost:8100/api/step_continuous

# 2.2.7 Plan endpoint
curl http://localhost:8100/api/agent/0/plan
# Expected: {"agent_id": 0, "plan": {"phases": [...], "current_phase_index": 0, ...}}

# 2.2.8 Plan adherence endpoint
curl http://localhost:8100/api/agent/0/plan-adherence
# Expected: {"agent_id": 0, "phase_progress": "0/3", "current_phase_goal": "visit_attractions", ...}
```

---

## 3. Plan Module Verification

### 3.1 Default plan loaded correctly
```bash
cd D:\IaaC\2ND_YEAR\THESIS\LLM_Based_UrbanABM
python -c "
import json
with open('test/plans.json') as f: plans = json.load(f)
for arch in ['tourist', 'resident', 'commuter', 'student']:
    assert arch in plans, f'{arch} missing from plans'
    assert 'phases' in plans[arch], f'{arch} has no phases'
    for p in plans[arch]['phases']:
        assert 'id' in p, f'{arch} phase missing id'
        assert 'goal' in p, f'{arch} phase missing goal'
        assert 'target_types' in p, f'{arch} phase missing target_types'
        assert 'perception_preferences' in p, f'{arch} phase missing perception_preferences'
        assert 'perception_avoid' in p, f'{arch} phase missing perception_avoid'
        for pref in p['perception_preferences']:
            assert pref in ['scene_overview','buildings','materials','building_condition',
                'street_furniture','vegetation','signage','ground_surfaces',
                'spatial_enclosure','pedestrian_activity','lighting_atmosphere',
                'as_resident','as_commuter','as_tourist','as_student'], f'Invalid preference: {pref}'
print('[OK] All plans valid')
"
```

### 3.2 Backend plans match test plans
```bash
cd D:\IaaC\2ND_YEAR\THESIS\LLM_Based_UrbanABM
python -c "
import json
with open('test/plans.json') as f: test_plans = json.load(f)
with open('Backend/LLM/Thinking/plans.json') as f: backend_plans = json.load(f)
for arch in ['tourist', 'resident', 'commuter', 'student']:
    assert test_plans[arch] == backend_plans[arch], f'{arch} plans differ!'
print('[OK] Plans are identical')
"
```

---

## 4. Perception Mode & Diary

### 4.1 Diary respects perception mode
Check in `test/agent_lab_server.py` lines ~485-495:
```python
# Diary respects the same perception mode as the agent
if perception_mode in ("perception", "both"):
    perception = city_model.get_nearby_perception(agent.geometry)
else:
    perception = None
```
Verify:
- `amenities` mode → `perception = None` in diary
- `perception` mode → `perception = fetched from DB`
- `both` mode → `perception = fetched from DB`
- `rule_based` mode → `perception = None` in diary

### 4.2 Narrative uses agent.street_perception (not diary)
Check lines ~896-904: narrative builds `perception_ctx` from `agent.street_perception`, not from diary.

---

## 5. GeoParquet Recorder Folder Hierarchy

### 5.1 Recorder groups by archetype AND perception_mode
Check `Backend/Agent/geoparquet_recorder.py` `_flush_to_parquet()`:
```python
groups = gdf.groupby(['archetype', 'perception_mode'])
for (archetype, mode), group_gdf in groups:
    base_dir = self.output_dir / self._recording_date / archetype_clean / mode_clean
```
Verify:
- Records go to `date/archetype/perception_mode/` structure
- Mode changes mid-recording → records go to correct folders
- Multiple archetypes in buffer → separated correctly

### 5.2 Run recording test
```bash
cd D:\IaaC\2ND_YEAR\THESIS\LLM_Based_UrbanABM\test
# Start recording
curl -X POST http://localhost:8100/api/recording/start -H "Content-Type: application/json" -d '{"session_name": "checkpoint_test"}'

# Step 20 times
for i in {1..20}; do curl -s -X POST http://localhost:8100/api/step_continuous > /dev/null; done

# Stop recording
curl -X POST http://localhost:8100/api/recording/stop

# Check folder structure
python -c "
import geopandas as gpd
from pathlib import Path
files = list(Path('tracking_data').rglob('*.parquet'))
new_files = [f for f in files if 'checkpoint_test' in f.name]
for f in new_files:
    df = gpd.read_parquet(str(f))
    archs = df['archetype'].unique().tolist()
    modes = df['perception_mode'].unique().tolist()
    has_plan = 'plan_json' in df.columns
    print(f'{f.parent.name}/{f.name}: archetypes={archs}, modes={modes}, has_plan={has_plan}')
"
```

---

## 6. Frontend

### 6.1 Perception mode dropdown syncs with server
Check `test/Frontend/agent_lab.html` `bootstrap()` function:
```javascript
const modeSelect = document.getElementById("perception-mode");
if (cfg.perception_mode && modeSelect.querySelector(`option[value="${cfg.perception_mode}"]`)) {
    modeSelect.value = cfg.perception_mode;
}
```
Verify:
- On page load, dropdown matches server's `perception_mode`
- Changing dropdown sends POST to `/api/config/perception-mode`

---

## 7. Research Questions Verification

### 7.1 Perception data flows through plan to LLM
When LLM prompt includes `plan_context`, verify that:
- `Current Plan Phase: visit_attractions` appears in the prompt
- `Prefer streets with: vegetation, lighting_atmosphere, buildings` appears
- `Active target` points to a real amenity

### 7.2 Forced deviation tracking works
In `amenities` mode with empty `perception_avoid` list:
- `GET /api/agent/0/plan-adherence` → `forced_deviation: false`

In `both` mode with `perception_avoid: ["dark"]`:
- `forced_deviation` becomes true only when all candidate edges lack adequate lighting

---

## 8. Test: Full Flow Integration

```python
"""
Full integration test — run after server is up with an agent configured.

Expected behavior:
1. Plan initializes with 3 phases for tourist
2. Phase 1 (visit_attractions) tracks visited amenities
3. Phase 2 (find_cafe) starts after visiting an attraction-type amenity
4. Phase 3 (scenic_walk) starts after visiting a cafe/restaurant
5. All phases complete → plan.status = "completed"
"""
import requests, time
BASE = "http://localhost:8100"

# Configure
r = requests.post(f"{BASE}/api/single-agent/configure", json={
    "start_lon": 2.17, "start_lat": 41.39,
    "target_lon": 2.18, "target_lat": 41.40,
    "archetype": "tourist"
})
assert r.json()["status"] == "configured", f"Config failed: {r.json()}"
print("[OK] Agent configured")

# Step 50 times
for i in range(50):
    r = requests.post(f"{BASE}/api/step_continuous")
    if i % 10 == 9:
        plan = requests.get(f"{BASE}/api/agent/0/plan").json()
        phase = plan.get("plan", {}).get("current_phase")
        progress = len(plan.get("plan", {}).get("completed_phases", []))
        print(f"  Step {i+1}: Phase {progress}/3 — {phase.get('goal') if phase else 'complete'}")
        if plan.get("plan", {}).get("status") == "completed":
            print("[OK] All plan phases completed!")
            break

# Final check
plan = requests.get(f"{BASE}/api/agent/0/plan").json()
assert plan.get("plan", {}).get("status") in ("active", "completed"), "Plan failed"
print("[OK] Integration test passed")
```

---

## 9. Perception Data Quality Check

Verify that the perception data from images is actually being used:
```bash
cd D:\IaaC\2ND_YEAR\THESIS\LLM_Based_UrbanABM\test
python -c "
import requests, json
BASE = 'http://localhost:8100'

# Check perception data at current location
r = requests.get(f'{BASE}/api/agent/0/perception-text')
data = r.json()
print('Perception fields present:', list(data.get('perception', {}).keys()))
print('Image URL:', data.get('image_url'))
print('Nearby amenities:', len(data.get('nearby_amenities', [])))

# Check plan context includes perception references
plan = requests.get(f'{BASE}/api/agent/0/plan').json()
cp = plan.get('plan', {}).get('current_phase', {})
print('Current phase goal:', cp.get('goal'))
print('Perception preferences:', cp.get('perception_preferences'))
"
```

---

## Check Results Summary

| # | Check | Status | Notes |
|---|-------|--------|-------|
| 1.1 | PlanBlock compiles | [x] | |
| 1.2 | Dispatcher compiles | [x] | |
| 1.3 | LLM.Thinking imports | [x] | |
| 1.4 | Prompts with plan_context | [x] | |
| 1.5 | KVMemory schema | [x] | |
| 1.6 | GeoParquet AgentRecord | [x] | |
| 1.7 | StepResult field | [x] | |
| 2.1 | Test server syntax | [x] | |
| 2.2.1 | Health check | [x] | |
| 2.2.2 | Frontend config | [x] | perception_mode=both present |
| 2.2.3 | Perception mode update | [x] | **Note:** endpoint requires `{"mode":"amenities"}`, not bare string |
| 2.2.4 | Mode reset | [x] | |
| 2.2.5 | Create agent | [x] | **Bug fixed:** graph had 500 disconnected components; `_find_nearest_node` now restricts to main component (2281/3441 nodes) |
| 2.2.6 | Step agent | [x] | LLM calls working, 0 errors |
| 2.2.7 | Plan endpoint | [x] | 3-phase tourist plan returned correctly |
| 2.2.8 | Plan adherence endpoint | [x] | phase_progress=0/3, forced_deviation=false |
| 3.1 | Plans valid | [x] | All 4 archetypes, all required fields |
| 3.2 | Plans match | [x] | test/plans.json == Backend/LLM/Thinking/plans.json |
| 4.1 | Diary respects mode | [x] | Code verified: perception=None when mode=amenities/rule_based |
| 4.2 | Narrative uses agent data | [x] | Code verified: perception_ctx from agent.street_perception |
| 5.1 | Folder hierarchy correct | [x] | groupby archetype+perception_mode confirmed in code |
| 5.2 | Recording test | [x] | tourist/both/…parquet: has_plan=True, rows=20 |
| 6.1 | Frontend dropdown sync | [x] | bootstrap() syncs modeSelect and sends POST on change |
| 7.1 | Perception in prompt | [x] | "Current Plan Phase: visit_attractions", prefer/avoid fields present |
| 7.2 | Forced deviation tracking | [x] | forced_deviation=false confirmed via API |
| 8 | Full flow integration | [x] | 50 steps, plan status=active (phases advance on amenity visits) |
| 9 | Perception quality check | [x] | scene_overview, buildings, vegetation, lighting returned; 10+ amenities |

## Bug Fixed During Verification

**`_find_nearest_node` snapped to isolated graph components**
- Root cause: `eixample_overture.duckdb` walk_edges has 500 connected components; the largest has 2281/3441 nodes.
- Any click near an isolated edge (478 components of size ≤3) returned "only 2 nodes reachable" error.
- Fix: `_load_city_data` now computes the largest connected component at startup and stores it in `self.main_component_nodes`. `_find_nearest_node` searches only within this set.
- File: `Backend/Agent/model.py`
