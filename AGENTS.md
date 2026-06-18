# AGENTS.md — LLM-Based UrbanABM

## Entrypoints

- **Main API server:** `Backend/Agent/map_server.py` — FastAPI, port 8000
- **Agent lab (research harness):** `test/agent_lab_server.py` — FastAPI, port 8100
- **Start scripts:** `start_system.bat` (both servers + frontend), `start_backend.bat` (backend only)

## Startup

```bash
start_system.bat          # One-click: map server + lab server + open frontend
# Or manually:
python Backend/Agent/map_server.py   # Port 8000
python test/agent_lab_server.py      # Port 8100
# Frontend: open Frontend/index.html (no build step)
```

`start_backend.bat` parses `.env` manually via `for` loop before launching. `map_server.py` also calls `load_dotenv()` — `.env` must exist at project root.

## .env essentials

```env
LLM_PROVIDER=ollama|openai|gemini|vllm|deepseek|lmdeploy
LLM_MODEL=qwen2.5-coder:3b
LLM_API_KEY=...
LLM_BASE_URL=http://localhost:8002/v1  # needed for vllm/lmdeploy/custom
NUM_AGENTS=15              # 1–100 recommended
LLM_CALLS_PER_STEP=20      # 0 = fully rule-based
PERCEPTION_MODE=both       # amenities | perception | both | rule_based
SPAWN_SEED=42              # omit for random
DATABASE_PATH=..\Environment\eixample_overture.duckdb
```

See `.env.example` and `Backend/LLM/llm_config.py` (all provider endpoint defaults).

## Model structure

- `Backend/Agent/model.py` — `CityModel` + `CityAgent` (uses Overture Maps DuckDB)
- `Backend/Agent/OSM_model.py` — same API, uses OSM DuckDB
- Two DuckDB sources: `eixample_overture.duckdb` (default), `eixample_osm.duckdb`

## Simulation loop

`CityModel.async_step()` runs all agents concurrently via `asyncio.gather`.
Per agent, `BlockDispatcher.run()` fires in order:
1. **NeedsBlock** — rule-based decay each step; LLM at amenities only
2. **CognitionBlock** — updates mood/curiosity/fatigue; LLM every 10 steps
3. **PlanBlock** — resolves targets, filters candidate edges
4. **MobilityBlock** — LLM chooses next edge from candidates

**LLM budget guard:** `LLM_CALLS_PER_STEP` caps how many agents call LLM for mobility per step. Remaining agents use rule-based (least-visited edge). When `PERCEPTION_MODE=rule_based`, no LLM is used at all.

## Agent properties

- 4 archetypes assigned round-robin: `resident`, `commuter`, `tourist`, `student`
- Memory: `KVMemory` (async key-value for state) + `StreamMemory` (append-only event log with topics: `mobility`, `amenity_visit`, `cognition`, `needs`)
- Per-step perception query only returns static data (amenities from DuckDB + pre-computed VLM scene analysis from JSON/DuckDB)
- **Agents do not see each other** — no inter-agent perception or communication

## LLM client

`Backend/LLM/llm_client.py` wraps `openai.AsyncOpenAI`. Provider-agnostic. Features:
- Retry (3 attempts), token tracking, JSON mode
- Circuit breaker: `LLM_CIRCUIT_BREAKER_THRESHOLD=5`, `LLM_CIRCUIT_BREAKER_RECOVERY=30`
- Connection pool: `LLM_MAX_CONNECTIONS=100`, `LLM_MAX_KEEPALIVE_CONNECTIONS=20`
- Hot-swap at runtime: `POST /api/config/llm?provider=vllm&model=...&api_key=...`

## Tracking / recording

- `agent_tracker.AgentTracker` — per-step agent state → DuckDB (`tracking_data/agent_tracking.duckdb`)
- `geoparquet_recorder.GeoParquetRecorder` — full agent state → GeoParquet files (for QGIS/ML)
- Both are optional; enabled via API endpoints

## Street view perception

VLM scene analysis is pre-computed (Qwen2.5-VL) and stored in DuckDB/JSON. Not real-time. The `streetview_analysis/` pipeline runs offline. Primary source: `test/StreetPLM/`. Falls back to `Backend/Environment/output/`.

## Testing / research

- `test/agent_lab_server.py` — isolated research harness that reuses all Backend modules unmodified
- `test/spatial_memory.PerceptionDiary` — episodic recording (in-memory, not in production)
- `test/README.md` has full lab docs, endpoint reference, and experiment workflows

## Benchmark notebooks

`benchmark/` has 5 independent Jupyter notebooks. Run in order or standalone. No CI.

## Key constraints

- **Platform:** Windows (`start_*.bat`, backslash paths in `.env`)
- **No linter/formatter/typechecker config** found in this repo
- **No test framework** configured (no pytest, no tox)
- **No CI pipelines**
- `opencode.json` sets edit and bash to `"ask"` by default
- Agents spawn from the same starting edge by default; `SPAWN_SEED` controls reproducibility
- `Backend/LLM/SETUP_GUIDE.md` has detailed provider setup instructions
- `SYSTEM_DOCUMENTATION.md` has full architecture details
