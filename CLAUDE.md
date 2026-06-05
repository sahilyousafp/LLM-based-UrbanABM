# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Quick Start Commands

### Installation
```bash
pip install -r requirements.txt
```

### Running the System

**One-click (Windows):**
```
double-click start_system.bat
```

**Manual (all platforms):**
```bash
# Terminal 1: Backend API server (port 8000)
cd Backend/Agent
python map_server.py

# Terminal 2: Frontend static server (port 8091)
cd Frontend
python -m http.server 8091

# Terminal 3: Optional — Agent Lab inspection tool (port 8100)
cd test
python agent_lab_server.py

# Then open http://localhost:8091 in a browser
```

### Frontend Development
```bash
cd Frontend
npm install              # One-time setup
npm run dev             # Watch + bundle TypeScript/Three.js
npm run build           # Production bundle
```

### LLM Provider Setup
Before running, configure `.env` in the project root (copy from `.env.example`):

```env
# Local Ollama (free, recommended for development)
LLM_PROVIDER=ollama
LLM_MODEL=llama3.1
LLM_CALLS_PER_STEP=50   # Budget: agents calling LLM per step

# OR: Cloud provider (OpenAI, DeepSeek, etc.)
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
LLM_API_KEY=sk-...
```

If using **Ollama locally**, first run:
```bash
ollama serve  # In separate terminal
ollama pull llama3.1
```

To run **fully rule-based** (no LLM, ~10ms/step):
```env
LLM_CALLS_PER_STEP=0
```

### Simulation API
```bash
# Advance one step
curl -X POST http://localhost:8000/api/step

# Inspect agent memory
curl http://localhost:8000/api/agent/42/memory

# See agent's movement log
curl http://localhost:8000/api/agent/42/stream

# LLM token usage stats
curl http://localhost:8000/api/llm/stats
```

---

## Architecture Overview

### Three-Tier System

```
Frontend (Leaflet.js map + Three.js 3D agents)
         ↓ HTTP/REST
FastAPI Backend (map_server.py)
         ↓
┌────────────────────────────┬──────────────────────┐
│ Mesa ABM Model             │ LLM Client           │
│ (CityModel + 500 agents)   │ (Ollama/OpenAI/etc)  │
└────────────┬───────────────┴──────────┬───────────┘
             │ per-agent per-step      │
        ┌────▼────┐              ┌─────▼──────┐
        │ Memory  │              │ Thinking   │
        │ System  │              │ Blocks     │
        └────┬────┘              └─────┬──────┘
             │                         │
             └────────────┬───────────┘
                          ▼
                    DuckDB Spatial
                (buildings, roads, amenities, network)
```

### Simulation Loop (Every Step)

Each agent runs `BlockDispatcher.run()` in parallel via `asyncio.gather`:

1. **NeedsBlock** — Decay hunger/energy/social. At amenities, LLM evaluates need satisfaction.
2. **CognitionBlock** — Every 10 steps, LLM updates mood/curiosity/fatigue from recent experience.
3. **MobilityBlock** — LLM (budget-guarded) chooses next street edge, or fallback to least-visited rule.

**LLM budget guard** (`LLM_CALLS_PER_STEP`, default 50): Only 50 agents per step call LLM for movement. Rest use rule-based fallback. Keeps step latency predictable (~100–300ms).

### Agent Archetypes

Round-robin assignment shapes LLM decisions:
- **Resident** — familiar routes, need-driven
- **Commuter** — efficient, direct, time-conscious
- **Tourist** — exploratory, curious, new streets
- **Student** — budget-conscious, social, energetic

---

## Code Structure

### Backend (`Backend/`)

| Directory | Purpose |
|-----------|---------|
| `Agent/` | FastAPI server entry point (`map_server.py`), Mesa model (`model.py`), agent tracking |
| `LLM/` | LLM integration: config (`llm_config.py`), async client (`llm_client.py`), prompt templates |
| `Memory/` | Agent memory: KVMemory (key-value store), StreamMemory (event log), Memory facade |
| `Thinking/` | Decision blocks: base Block class, BlockDispatcher (runs blocks + budget guard), prompt generation |
| `Thinking/blocks/` | Three decision blocks: mobility_block.py, needs_block.py, cognition_block.py |
| `Environment/` | Spatial data pipelines: Overture Maps download (overture_to_duckdb.py), OSM import (osm_to_duckdb.py), DuckDB databases |

### Key Backend Files

- **`Backend/Agent/map_server.py`** — FastAPI app. Loads `.env`, initializes CityModel, exposes `/api/*` endpoints.
- **`Backend/Agent/model.py`** — `CityModel` (Mesa model, async step loop) and `CityAgent` (individual agents, memory, behaviors).
- **`Backend/LLM/llm_client.py`** — Async OpenAI-compatible client. Abstracts Ollama, OpenAI, vLLM, DeepSeek, etc.
- **`Backend/LLM/prompts.py`** — All LLM prompt templates. Edit here to change decision reasoning.
- **`Backend/Memory/kv_memory.py`** — Thread-safe async key-value store. Schema: position, needs, visited_edges, cognition_state, etc.
- **`Backend/Memory/stream_memory.py`** — Append-only event log partitioned by topic (mobility, amenity_visit, cognition).
- **`Backend/Thinking/dispatcher.py`** — BlockDispatcher: runs all three blocks per agent, manages LLM budget.

### Frontend (`Frontend/`)

- **`index.html`** — Leaflet.js map, agent visualization, control panel (Step button, agent inspection).
- **`src/main.ts`** — TypeScript entry. Fetches `/api/agents` GeoJSON, renders agent dots/info popups.
- **`style.css`** — Map styling.
- **`reference/Characters.blend`** — Blender 3D character models (for future 3D rendering).

### Benchmarks (`benchmark/`)

Five Jupyter notebooks evaluating technology choices:
1. **01_database_comparison.ipynb** — DuckDB vs SQLite+SpatiaLite vs PostgreSQL+PostGIS
2. **02_llm_provider_comparison.ipynb** — Ollama vs vLLM vs GPT-4o
3. **03_map_data_comparison.ipynb** — Overture Maps vs OpenStreetMap
4. **04_vlm_perception_comparison.ipynb** — Vision model comparison (Qwen)
5. **05_system_integration_benchmark.ipynb** — LLM-driven vs rule-based agents (humanistic scoring)

---

## Key Architectural Patterns

### Memory System

**KVMemory** — async key-value store with per-key locking. Schema keys:

```python
{
    "position": {"lon": float, "lat": float, "edge_id": int},
    "needs": {"hunger": 0–1, "energy": 0–1, "social": 0–1, "comfort": 0–1},
    "visited_edges": {edge_id: visit_count},
    "visited_amenities": [{name, type, lon, lat}],
    "agent_profile": {"archetype": str, "age": int, "preferences": list},
    "cognition_state": {"mood": str, "curiosity": 0–1, "fatigue": 0–1},
    "destination": {"name": str|None, "amenity_type": str|None, "target_node": tuple|None}
}
```

**StreamMemory** — append-only event log by topic:
- `mobility` — edge chosen, nearby amenities, LLM reasoning
- `amenity_visit` — amenity visited, need deltas
- `cognition` — mood changes, LLM reflection summary
- `needs` — need state snapshots

Access via `agent.memory.stream_memory.get_recent(topic, n)`.

### LLM Integration

All LLM calls go through `Backend/LLM/llm_client.py` (AsyncOpenAI-compatible). Supports:
- **Ollama** (local, free) — auto-connects to `localhost:11434/v1`
- **OpenAI** — uses `OPENAI_API_KEY`
- **vLLM** (local GPU) — custom endpoint via `LLM_BASE_URL`
- **DeepSeek**, **Gemini**, etc. — OpenAI-format APIs

**Budget Guard** in `BlockDispatcher`:
- Per step, only `LLM_CALLS_PER_STEP` agents (default 50) call LLM for movement.
- Remaining agents use rule-based fallback (least-visited edge heuristic).
- Prevents runaway token cost and latency spikes.

### Block Pattern

All decision blocks inherit from `Backend/Thinking/block.py`:

```python
class Block(ABC):
    async def run(self, agent: CityAgent, context: SimulationContext) -> BlockResult:
        """Returns BlockResult(success: bool, data: dict, notes: str)"""
```

Blocks are stateless; state lives in `agent.memory`. Each block:
1. Reads from memory
2. Makes a decision (via LLM or rules)
3. Writes result to memory stream
4. Returns BlockResult

---

## Development Guidelines

### When Adding Features

1. **Spatial data** — add to `Backend/Environment/overture_to_duckdb.py` or `osm_to_duckdb.py`, query via DuckDB in `model.py`.
2. **Agent behavior** — add blocks in `Backend/Thinking/blocks/`, edit prompts in `Backend/LLM/prompts.py`, update BlockDispatcher order if needed.
3. **Frontend visuals** — edit `Frontend/src/main.ts` or `Frontend/index.html`, rebuild with `npm run build`.
4. **New API endpoints** — add routes to `Backend/Agent/map_server.py`, wrap with CORS if frontend needs it.
5. **Memory schema** — extend `DEFAULT_SCHEMA` in `Backend/Memory/kv_memory.py` if agents need new state keys.

### Common Debugging Patterns

- **Agent stuck in loop?** — Check `destination` and `plan` fields in `/api/agent/{id}/memory`.
- **Unfair archetype?** — Adjust prompts in `Backend/LLM/prompts.py` or archetype-to-preference mapping in `model.py`.
- **Slow queries?** — Profile with `Backend/Environment/verify_db.py`; index walk_edges and amenities if needed.
- **LLM budget too tight?** — Increase `LLM_CALLS_PER_STEP` in `.env`, or reduce `LLM_MAX_TOKENS`.
- **Network gaps?** — Run `Backend/Environment/network_connector.py` to bridge disconnected walk_edges.

### Testing

Currently limited formal test suite. Manual testing via:
- Frontend: Click agents, inspect memory via popups or API.
- API: Use `curl` or Postman to call `/api/step`, `/api/agent/{id}/memory`, etc.
- Benchmarks: Run Jupyter notebooks in `benchmark/` to validate latency and decision quality.

Consider adding:
- Unit tests for decision blocks (mock memory and LLM responses).
- Integration tests for multi-step agent trajectories (start → destination → end).
- Snapshot tests for prompts (ensure prompt format doesn't drift).

---

## Environment Variables

Key `.env` settings (copy from `.env.example` and edit):

```env
# ─── LLM ──────────────────────────────────
LLM_PROVIDER=ollama                    # Provider type
LLM_MODEL=llama3.1                     # Model name
LLM_API_KEY=                           # Not needed for Ollama
LLM_TIMEOUT=60                         # Seconds
LLM_MAX_TOKENS=256                     # Per response
LLM_TEMPERATURE=0.7                    # Sampling randomness
LLM_CALLS_PER_STEP=50                  # Budget guard (0 = all rule-based)

# ─── Agent ────────────────────────────────
NUM_AGENTS=50                          # 1–500 recommended
SPAWN_SEED=                            # Leave empty for random
PERCEPTION_MODE=both                   # amenities|perception|both|rule_based

# ─── Data ─────────────────────────────────
DATABASE_PATH=../Environment/eixample_overture.duckdb  # Spatial data
OVERTURE_RELEASE=2024-11-13.0         # Overture Maps snapshot version

# ─── Server ───────────────────────────────
HOST=127.0.0.1
PORT=8000
RELOAD=true
```

---

## Documentation References

- **README.md** — high-level overview, quick start, API reference
- **SYSTEM_DOCUMENTATION.md** — full architecture and implementation details
- **DUCKDB_INSPECTION_GUIDE.md** — how to query spatial database
- **GCP_BIGQUERY_ACCESS_GUIDE.md** — Overture Maps via BigQuery
- **Backend/LLM/SETUP_GUIDE.md** — LLM provider setup (Ollama, vLLM, etc.)
- **benchmark/README.md** — benchmark methodology and rubric
- **scripts/streetview_analysis/README.md** — Street View perception pipeline

---

## Tips for Productive Work

1. **Understand the async model** — `CityModel.async_step()` runs all 500 agents concurrently. Blocks must be non-blocking and memory-safe.
2. **Edit prompts carefully** — `Backend/LLM/prompts.py` controls all LLM reasoning. A prompt tweak can dramatically change agent behavior.
3. **Budget the LLM calls** — 50 agents/step × 3 blocks (potentially) = 150 LLM calls/step. At 20 tokens/call ≈ 3,000 tokens/step. Plan accordingly.
4. **Use the API for debugging** — Faster than UI for repeated inspection: `curl http://localhost:8000/api/agent/42/stream?n=10`.
5. **Run benchmarks after major changes** — `benchmark/05_system_integration_benchmark.ipynb` measures humanistic quality impact.
6. **Check git diffs on Memory/prompts.py** — These are high-leverage changes that affect agent behavior across all steps.
