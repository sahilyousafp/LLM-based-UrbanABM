# Urban ABM Backend — `Backend/Agent`

FastAPI server providing agent-based simulation, spatial queries, LLM-driven agent cognition, and Street View perception for the Urban ABM system.

---

## File Structure

```
Backend/Agent/
├── map_server.py          # Entry point: app + CORS + router registration (~90 lines)
├── paths.py               # All path constants (DB_PATH, SV_OUTPUT_DIR, etc.)
├── db.py                  # get_db_connection() helper
├── state.py               # SimState singleton — owns city_model, job registries
│
├── model/                 # Mesa simulation model (split from original model.py)
│   ├── __init__.py        # Re-exports: CityAgent, CityModel
│   ├── agent.py           # CityAgent — pedestrian agent, LLM/rule-based step
│   └── city_model.py      # CityModel — network graph, pathfinding, agent spawning
│
├── routers/               # FastAPI APIRouter modules (one per domain)
│   ├── spatial.py         # /api/{buildings, walk_network, roads, amenities, …}
│   ├── agents.py          # /api/agents, /api/agent/{id}/*
│   ├── simulation.py      # /api/step, /api/step_continuous, /api/test
│   ├── recording.py       # /api/recording/*
│   ├── streetview.py      # /api/streetview/*, /api/streetview_grid/*, /api/assets/*
│   ├── llm_config.py      # /api/config/*, /api/llm/*, /api/ollama/*, /api/container/*
│   ├── overture.py        # /api/overture/*, /api/zone/*, /api/external/*, /api/database/*
│   └── vlm.py             # /api/vlm/*
│
├── agent_tracker.py       # DuckDB movement/decision logger
├── geoparquet_recorder.py # GeoParquet agent trajectory export
├── rule_based_movement.py # Deterministic fallback movement heuristic
└── debug_server.py        # Dev tool: DuckDB table inspection (port 8100)
```

---

## Setup

```bash
pip install -r requirements.txt
```

## Running

```bash
cd Backend/Agent
python map_server.py
# Server starts at http://127.0.0.1:8000
```

Or via the project root launcher:

```
start_system.bat   # Windows one-click
```

---

## API Endpoints

### Spatial Data

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/buildings` | All buildings as GeoJSON polygons |
| GET | `/api/walk_network` | Pedestrian network edges as GeoJSON |
| GET | `/api/walk_network/classes` | Walk edges with `road_class` property + per-class counts |
| GET | `/api/walk_network/candidates` | Sample points along edges for Street View capture |
| GET | `/api/roads` | Drive network as GeoJSON |
| GET | `/api/amenities` | Points of interest as GeoJSON |
| GET | `/api/walk_nodes` | Network intersection nodes (sample, max 500) |
| GET | `/api/stats` | Row counts + bounding box for each DB table |
| GET | `/api/tables` | All DuckDB table names with row counts |

### Agent Management

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/agents` | All agents as GeoJSON with archetype/gender/age |
| POST | `/api/agents/respawn` | Rebuild simulation with new agent count |
| POST | `/api/agents/respawn_advanced` | Spawn by mode: `click`, `random`, `poi`, `home_work` |
| GET | `/api/agents/summaries` | LLM narratives for first 10 agents |
| GET | `/api/agent/{id}` | Agent details + nearby amenities + street perception |
| GET | `/api/agent/{id}/summary` | LLM-generated narrative for one agent |
| GET | `/api/agent/{id}/memory` | Full KV + stream memory snapshot |
| GET | `/api/agent/{id}/stream` | Recent stream events (optional `?topic=&n=` filters) |
| GET | `/api/agent/{id}/cognition` | Needs, cognition state, current plan |
| GET | `/api/agent/{id}/perception-text` | Street perception at agent's location |
| GET | `/api/agent/{id}/path-adherence` | Dijkstra adherence % from mobility stream |
| GET | `/api/agent/{id}/narrative-compare` | Generic vs history-aware LLM narratives |
| GET | `/api/agent/{id}/results-summary` | Per-topic LLM summaries (vision, mobility, needs, …) |

### Simulation Control

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/step` | Advance simulation one step (async LLM) |
| POST | `/api/step_continuous` | Step + return all agent positions |
| GET | `/api/test` | Health check — returns agent count |

### Recording (GeoParquet)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/recording/start` | Begin recording agent states to Parquet |
| POST | `/api/recording/stop` | Stop and export session |
| GET | `/api/recording/status` | Current session stats |
| GET | `/api/recording/list` | All saved Parquet files |
| GET | `/api/recording/download/{path}` | Download a Parquet file |
| GET | `/api/recording/load` | Load recording as agent trajectories JSON |

### Street View

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/streetview/download` | Start background GSV image download job |
| GET | `/api/streetview/download/status/{job_id}` | Poll download progress |
| DELETE | `/api/streetview/download/{job_id}` | Cancel download job |
| GET | `/api/streetview/stats` | Image + result file counts |
| DELETE | `/api/streetview/images/unanalyzed` | Remove JPEGs with no VLM result |
| POST | `/api/streetview/analyze` | Start background VLM analysis job |
| GET | `/api/streetview/analyze/status/{job_id}` | Poll analysis progress |
| GET | `/api/streetview_grid` | GeoJSON of all analyzed + image-only locations |
| GET | `/api/streetview_grid/image/{filename}` | Serve a Street View JPEG |
| GET | `/api/streetview_grid/json/{filename}` | Serve an analysis JSON file |
| GET | `/api/streetview_grid/analysis/{lat}_{lon}` | All perception fields for a coordinate |
| GET | `/api/assets/agents/{filename}` | Serve GLB agent character models |

### LLM & Runtime Config

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/config/llm` | Hot-swap LLM provider/model at runtime |
| GET/POST | `/api/config/perception-mode` | Get or set agent perception mode |
| GET | `/api/config/archetypes` | Archetype nav_mode from plans.json |
| GET | `/api/config/frontend` | Non-secret config for the frontend |
| GET | `/api/llm/stats` | Token usage + latency statistics |
| GET | `/api/ollama/models` | Locally installed Ollama model names |
| POST | `/api/container/start` | Launch vLLM or LMDeploy Docker container |

### Overture Maps & External Data

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/overture/download` | Start Overture Maps download for a bbox |
| GET | `/api/overture/status/{job_id}` | Poll download progress |
| POST | `/api/overture/save/{job_id}` | Append or save downloaded data |
| GET | `/api/zone/current` | Current bbox (user-drawn → data extent → default) |
| GET | `/api/external/sources` | List available data plugins |
| POST | `/api/external/{source}/download` | Start plugin data download |
| GET | `/api/external/{source}/status/{job_id}` | Poll plugin download |
| DELETE | `/api/external/{source}` | Drop plugin table from DB |
| POST | `/api/database/upload` | Upload a new DuckDB file and reload model |

### VLM Benchmark

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/vlm/benchmark` | Benchmark results from notebook 04 |
| POST | `/api/vlm/benchmark` | Merge a model result into the benchmark file |
| GET | `/api/vlm/compare-images/{filename}` | Serve annotated benchmark images |
| GET | `/api/vlm/barcelona-image` | Serve the Barcelona reference image |
| GET | `/api/vlm/analysis-outputs` | All VLM analysis JSONs from benchmark outputs |

---

## Key Modules

### `state.py` — SimState Singleton

Owns all mutable singletons. Routers import `sim` from here instead of using `global` keywords:

```python
from state import sim

sim.city_model          # CityModel instance
sim.sv_jobs             # streetview download jobs dict
sim.analyze_jobs        # VLM analysis jobs dict
sim.overture_jobs       # Overture pipeline jobs dict
sim.ext_jobs            # external plugin jobs dict
sim.get_zone_bbox()     # last drawn bbox
sim.set_zone_bbox(bbox)
sim.reset_model(**kwargs)  # rebuild CityModel atomically
```

### `model/` — Mesa Simulation

- **`model/agent.py`** — `CityAgent`: perception, LLM dispatcher, edge traversal, memory
- **`model/city_model.py`** — `CityModel`: network graph (NetworkX + DuckDB), Dijkstra routing, agent spawning, async step loop
- **`model/__init__.py`** — re-exports both so `from model import CityModel, CityAgent` still works

### Perception Modes

Set via `POST /api/config/perception-mode` or `PERCEPTION_MODE` env var:

| Mode | Behaviour |
|------|-----------|
| `amenities` | DuckDB amenity queries only |
| `perception` | Street View scene analysis only |
| `both` | Amenities + perception (default) |
| `rule_based` | No LLM — deterministic least-visited movement |

### LLM Budget Guard

`LLM_CALLS_PER_STEP` (default 50): only N agents per step call the LLM for movement. The rest use the `rule_based_movement.py` fallback. Prevents runaway latency and token cost.
