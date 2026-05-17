# Spatial Cognition Lab

An **isolated research environment** for testing agent understanding of spatial parameters and revealing LLM capabilities and limitations in urban agent simulation.

## What This Tests

The lab is designed to answer core questions about agent cognition:

1. **Does the agent use image descriptions to make decisions?** → YES (LLM mode uses VLM-derived scene text in movement prompt)
2. **Does the agent follow shortest paths?** → YES (Dijkstra is computed, but the LLM may deviate based on archetype adherence)
3. **Does the agent remember where it's been?** → NO (past perceptions are not stored; only last 5 mobility events in stream)
4. **Does the agent's narrative reflect its experiences?** → SOMETIMES (depends on whether history is in the prompt)

The **Narrative Lab** tab directly exposes this gap: compare the generic narrative (current state only) with the history-aware narrative (includes perception diary). This difference shows what information the agent actually uses.

## Setup

### Prerequisites

- Ollama running with `qwen2.5-coder:3b` pulled: `ollama pull qwen2.5-coder:3b`
- Python dependencies: `pip install -r ../../requirements.txt`
- `MAPBOX_TOKEN` in root `.env`
- Eixample DuckDB at `Backend/Environment/eixample_overture.duckdb`

### Run

```bash
cd /path/to/repo
python test/agent_lab_server.py
```

Boots on `http://127.0.0.1:8100`.

Open `test/Frontend/agent_lab.html` in your browser (no build step required).

## How to Use

### Basic Workflow

1. **Pick start and target** on the map (green/orange pins)
2. **Select archetype** (resident, commuter, student, tourist)
3. **Configure agent** → agent spawns and starts perceiving
4. **Step or Run** to move the agent
5. **Watch 4 tabs** for different views of agent behavior

### Tabs Explained

**Movement** (default)
- Agent position and ID
- Cognition state (mood, fatigue, curiosity)
- Needs bars (energy, hunger, social, comfort)
- Current narrative (LLM-generated summary)
- Current perception (scene description + nearby street image)

**Spatial Experience**
- Perception diary: scrollable timeline of scenes the agent has passed through
- Visited amenities: distinct amenity encounters with step count
- Current scene: what the agent sees right now

**Agent Mind**
- Path adherence: % of steps following Dijkstra shortest path (by archetype)
- Needs over time: mini sparklines showing how needs evolve
- Thought stream: mobility, cognition, needs decisions from stream memory

**Narrative Lab** (the research tool)
- **Left column**: Generic narrative (current state only, no history)
- **Right column**: History-aware narrative (includes perception diary)
- **Comparison**: side-by-side reveals what information drives narrative specificity
- **Memory audit**: raw counts (diary entries, visited amenities, visited edges)
- **Raw prompt** (toggle): shows exact LLM prompt sent (for debugging)

## Key Endpoints (for scripting)

| Endpoint | Returns |
|---|---|
| `GET /api/agent/{id}/perception-diary` | Full episodic timeline (step, position, perception snapshot) |
| `GET /api/agent/{id}/narrative?include_history=true` | History-aware narrative |
| `GET /api/agent/{id}/narrative-compare` | Generic + history-aware side-by-side |
| `GET /api/agent/{id}/path-adherence` | % steps following Dijkstra + log |
| `GET /api/agent/{id}/spatial-stats` | Aggregated stats (busy%, vegetation%, amenity count) |
| `GET /api/agent/{id}/memory-audit` | Complete memory dump (diary, visited_edges, visited_amenities) |
| `POST /api/config/perception-mode` | Switch mode mid-run (both/perception/amenities/rule_based) |

## What Gets Recorded

### DuckDB (test/tracking_data/agent_lab.duckdb)

- `agent_movements` table: every step with lon, lat, edge_id, needs values
- `agent_decisions` table: (if enabled) decision details

### GeoParquet (test/tracking_data/agent_recording_*.parquet)

- Full agent state per step (needs, cognition, perception, thoughts)
- Spatial geometry per record
- Optional: thought-stream events and perception snapshots

### In-Memory (PerceptionDiary)

- Episodic diary: position, perception, nearby amenities, needs at each step
- Path adherence log: which steps followed Dijkstra vs deviated
- Visited amenities: deduped list of encountered POIs

## Key Architectural Notes

### Why Narratives Differ

- **Generic prompt**: Uses only archetype, current needs, current position, current perception, nearby amenities
- **History-aware prompt**: Adds the last 10 perception diary entries (scene_overview + amenities + needs at each step)

The LLM difference reveals:
- If history-aware is much better: the agent benefits from episodic memory (not implemented in production)
- If they're similar: the LLM's narrative is generic regardless (LLM limitation, not agent limitation)
- If history-aware is worse: over-context can hurt (rare)

### Archetype Adherence & Path Following

Expected path adherence (% following Dijkstra):
- **Commuter** (1.0 adherence): ~95-100%
- **Resident** (0.8): ~80-90%
- **Student** (0.5): ~50-60%
- **Tourist** (0.2): ~20-30%

Low adherence means the LLM prefers scenic/social routes over efficiency. This is the archetype system working as designed.

### Perception Mode Impact

- **both** (LLM + amenity): Full LLM decision with scene + amenities + path hint
- **perception** (LLM only): LLM uses scene but no amenities as fallback
- **amenities** (amenity only): No LLM, pure proximity-based selection
- **rule_based**: Deterministic Dijkstra + least-visited edge

Switch modes mid-run to see live behavioral differences.

## Spatial Memory: What's Stored vs. Not

### Stored (in-memory diary)

✅ Position + perception snapshot per step
✅ Nearby amenities at each step
✅ Needs values at each step
✅ Edge visits (counted in agent memory)
✅ Last 5 mobility stream events

### Not Stored (dead code / limitations)

❌ Historical perception values (only current nearest point fetched)
❌ Past amenity encounters (declared in KV schema but never written)
❌ Revisit recognition (no mechanism to check if agent has been to a node before)
❌ Spatial patterns (no clustering or heat map of visited regions)

The PerceptionDiary fixes this gap at the test level—it's where you can see what *should* be remembered but isn't in production.

## Typical Experiment

**Test hypothesis: "Agents with history awareness generate more specific narratives"**

```
1. Configure tourist agent, pick residential start + market target
2. Run 30 steps (mix of quiet residential + busy commercial)
3. Tab → Narrative Lab
4. Compare generic vs. history-aware narratives
5. Check memory audit: diary has 30 entries
6. Read raw prompt (toggle) to see exactly what went to the LLM
7. If history-aware mentions specific streets/areas: archetype works
8. If both are vague: LLM limitation, not agent limitation
```

## Troubleshooting

| Issue | Solution |
|---|---|
| Agent doesn't move | Check perception_mode is "both"; run `/api/step_continuous` again |
| Narrative is blank | Wait a step for perception to fetch; check browser console |
| Planned path not visible | Verify target_node is set; check map layer is enabled |
| Recording fails | Ensure `test/tracking_data/` exists |
| Diary is empty | First step records, check `/api/agent/{id}/perception-diary` response |

## Files

```
test/
├── agent_lab_server.py           Main server (port 8100)
├── spatial_memory.py             PerceptionDiary episodic store
├── .env.test                      LLM config + SPATIAL_MEMORY_DEPTH=50
└── Frontend/
    └── agent_lab.html            4-tab interactive UI
```

## Design Notes

The test harness is **isolated** but **reuses all Backend modules unchanged**:
- `CityModel`, `CityAgent`, `BlockDispatcher` — zero modifications
- `Memory` (KV + stream) — unchanged, just populated via diary
- `LLMClient` — unchanged, called with enriched prompts
- `Tracker` & `GeoParquetRecorder` — unchanged, alternative DB path

The only new code is:
- `spatial_memory.PerceptionDiary` — episodic recording
- `agent_lab_server.py` — ported endpoints + new narrative logic
- `agent_lab.html` — 4-tab UI + narrative comparison widget

No Backend files are modified. This makes the test hermetically sealed and repeatable.
