# LLM-Based Urban Agent-Based Model

> **Branch:** `open-city-integration` — LLM-driven pedestrian simulation of Barcelona's Eixample district.

500 autonomous agents navigate the Eixample street network. Each agent has **persistent memory**, **dynamic needs**, and makes **movement decisions via an LLM** that reasons about its archetype, current state, and the urban environment it perceives.

---

## How It Works

### Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Frontend — Leaflet.js (Frontend/index.html)            │
│  Interactive map: agents, buildings, walk network,      │
│  amenities. Click any agent to inspect its memory.      │
└────────────────────────┬────────────────────────────────┘
                         │  HTTP / REST
                         ▼
┌─────────────────────────────────────────────────────────┐
│  API Server — FastAPI  (Backend/Agent/map_server.py)    │
│  /api/step  →  awaits CityModel.async_step()           │
│  /api/agent/{id}/memory  /stream  /cognition           │
│  /api/agent/{id}/summary  (LLM narrative)              │
│  /api/llm/stats  /api/config/llm                       │
└────────────────────────┬────────────────────────────────┘
                         │
          ┌──────────────┴─────────────────┐
          ▼                                ▼
┌─────────────────────┐        ┌───────────────────────────┐
│  Mesa ABM           │        │  LLM Client               │
│  CityModel          │        │  Backend/LLM/llm_client.py│
│  500 × CityAgent    │        │  AsyncOpenAI-compatible   │
│                     │        │  Ollama / OpenAI / vLLM   │
└──────────┬──────────┘        └───────────────────────────┘
           │  per agent
           ▼
┌─────────────────────────────────────────────────────────┐
│  Agent State (per-agent, in-memory)                     │
│                                                         │
│  Memory                                                 │
│  ├─ KVMemory  — structured current state                │
│  │  (position, needs, archetype, visited_edges, …)      │
│  └─ StreamMemory — append-only event log                │
│     (mobility decisions, amenity visits, cognition)     │
│                                                         │
│  BlockDispatcher  (runs each simulation step)           │
│  ├─ NeedsBlock    → need decay + LLM satisfaction eval  │
│  ├─ CognitionBlock → LLM mood/curiosity/fatigue (×10)  │
│  └─ MobilityBlock → LLM picks next street edge         │
└──────────────────────────────┬──────────────────────────┘
                               │
                               ▼
                  ┌────────────────────────┐
                  │  DuckDB + Spatial      │
                  │  Overture Maps / OSM   │
                  │  buildings, walk_edges │
                  │  amenities, walk_nodes │
                  └────────────────────────┘
```

---

### Per-Step Agent Decision Loop

Every simulation step, `CityModel.async_step()` runs all 500 agents concurrently via `asyncio.gather`. For each agent, `BlockDispatcher.run()` fires three blocks in sequence:

| # | Block | Runs | LLM? | What it does |
|---|---|---|---|---|
| 1 | **NeedsBlock** | Every step | At amenities only | Decays hunger/energy/social each step. When agent is near an amenity, LLM evaluates how much each need is satisfied. Falls back to a rule table (`AMENITY_NEED_MAP`). |
| 2 | **CognitionBlock** | Every step | Every 10 steps | Accumulates fatigue between LLM calls. Every 10 steps, LLM reads the agent's recent experience stream and updates mood, curiosity, and fatigue. |
| 3 | **MobilityBlock** | Every step | Yes (budget-guarded) | Reads memory (needs, cognition, profile, recent 5 moves), annotates each candidate edge with nearby amenity types, and asks LLM to choose. Falls back to least-visited edge if LLM budget is exhausted or response fails. |

**LLM budget guard:** `LLM_CALLS_PER_STEP` (default 50) caps how many agents call LLM for mobility per step. The remaining agents use rule-based least-visited-edge movement. This keeps step latency predictable.

---

### Agent Archetypes

Agents are assigned round-robin from four archetypes. Archetype shapes the LLM prompt so decisions reflect realistic human behaviour:

| Archetype | Preferred destinations | Movement style |
|---|---|---|
| **Resident** | supermarket, pharmacy, park | Familiar routes, need-driven |
| **Commuter** | transport stops, cafés | Direct, efficient, time-sensitive |
| **Tourist** | attractions, restaurants, new streets | Exploratory, curiosity-driven |
| **Student** | cafés, library, park, social spaces | Budget-conscious, social, energetic |

---

### Agent Memory Schema

**KVMemory** — structured current state (async key-value with lock):

| Key | Type | Description |
|---|---|---|
| `position` | dict | `lon`, `lat`, `edge_id` — current location |
| `needs` | dict | `hunger`, `energy`, `social` — float 0–1 |
| `agent_profile` | dict | `archetype`, `age`, `preferences` |
| `visited_edges` | dict | `{edge_id: visit_count}` |
| `visited_amenities` | list | Last 20 amenity visits with step number |
| `current_plan` | dict | `goal`, `target_edge_id` |
| `cognition_state` | dict | `mood`, `curiosity`, `fatigue` |

**StreamMemory** — append-only event log partitioned by topic:

| Topic | Written by | Contains |
|---|---|---|
| `mobility` | MobilityBlock | Edge chosen, nearby amenities, LLM reasoning |
| `amenity_visit` | NeedsBlock | Amenity visited, need deltas, activity description |
| `cognition` | CognitionBlock | Mood change, summary of LLM reflection |
| `needs` | NeedsBlock | Need state after decay (implicit via amenity_visit) |

---

## Quick Start

### Prerequisites

- Python 3.10+
- One of:
  - **Ollama** (local, free): https://ollama.ai/download
  - **OpenAI API key** (cloud)
  - **vLLM** server (local GPU)

### 1 — Clone and install

```bash
git clone https://github.com/sahilyousafp/LLM-based-UrbanABM.git
cd LLM-based-UrbanABM
git checkout open-city-integration
pip install -r requirements.txt
```

### 2 — Configure LLM

```bash
copy scripts\.env.example scripts\.env
```

Edit `scripts\.env`:

```env
# Local Ollama (default — no API key needed)
LLM_PROVIDER=ollama
LLM_MODEL=llama3.1
LLM_CALLS_PER_STEP=50      # agents using LLM per step (0 = all rule-based)

# OR: OpenAI
# LLM_PROVIDER=openai
# LLM_MODEL=gpt-4o-mini
# LLM_API_KEY=sk-...
```

### 3 — Start Ollama (if using local LLM)

```bash
ollama serve
ollama pull llama3.1        # first time only (~4.7 GB)
```

### 4 — Start the system

**Windows — one-click:**
```
double-click start_system.bat
```

**Manual:**
```bash
cd Backend\Agent
python map_server.py
# → API running at http://localhost:8000
# Then open Frontend\index.html in a browser
```

### 5 — Simulate

- Click **Step** in the frontend to advance one simulation step
- Click any agent dot to see its ID, archetype, needs, and nearby amenities
- Use the API directly:

```bash
# Advance simulation
curl -X POST http://localhost:8000/api/step

# Inspect agent 42's full memory
curl http://localhost:8000/api/agent/42/memory

# See agent 42's movement + cognition log
curl http://localhost:8000/api/agent/42/stream

# LLM token usage
curl http://localhost:8000/api/llm/stats
```

---

## API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/api/step` | POST | Advance simulation one step (async, all agents) |
| `/api/step_continuous` | POST | Step + return all agent positions |
| `/api/agents` | GET | All agents as GeoJSON (with archetype) |
| `/api/agent/{id}` | GET | Agent position + nearby amenities |
| `/api/agent/{id}/summary` | GET | LLM narrative of what the agent is experiencing |
| `/api/agent/{id}/memory` | GET | Full KVMemory + StreamMemory snapshot |
| `/api/agent/{id}/stream` | GET | Event log (`?topic=mobility&n=20`) |
| `/api/agent/{id}/cognition` | GET | Cognition state, needs, archetype, current plan |
| `/api/agents/summaries` | GET | LLM summaries for first 10 agents |
| `/api/llm/stats` | GET | Token counts, call count, avg latency |
| `/api/config/llm` | POST | Hot-swap LLM provider/model at runtime |
| `/api/buildings` | GET | Buildings GeoJSON |
| `/api/walk_network` | GET | Walk edges GeoJSON |
| `/api/amenities` | GET | Amenity points GeoJSON |
| `/api/stats` | GET | Database row counts + bounding box |

---

## LLM Provider Configuration

The system uses `openai.AsyncOpenAI` which is compatible with any OpenAI-format API:

| Provider | `LLM_PROVIDER` | `LLM_BASE_URL` | `LLM_API_KEY` |
|---|---|---|---|
| Ollama (local) | `ollama` | *(auto: localhost:11434/v1)* | *(not needed)* |
| OpenAI | `openai` | *(auto: api.openai.com/v1)* | `sk-...` |
| vLLM (local GPU) | `custom` | `http://localhost:8001/v1` | `vllm` |
| DeepSeek | `deepseek` | *(auto: api.deepseek.com/v1)* | your key |

Hot-swap at runtime without restarting:
```bash
curl -X POST "http://localhost:8000/api/config/llm?provider=openai&model=gpt-4o-mini&api_key=sk-..."
```

To run **fully rule-based** (no LLM, ~10ms/step):
```env
LLM_CALLS_PER_STEP=0
```

---

## Project Structure

```
LLM_Based_UrbanABM/
├── Backend/
│   ├── Agent/
│   │   ├── map_server.py       # FastAPI server — entry point
│   │   ├── model.py            # CityModel + CityAgent (Overture data)
│   │   └── OSM_model.py        # CityModel + CityAgent (OSM data)
│   ├── LLM/
│   │   ├── llm_config.py       # LLMConfig dataclass, from_env()
│   │   └── llm_client.py       # Async provider-agnostic LLM client
│   ├── Memory/
│   │   ├── kv_memory.py        # KVMemory — async key-value store
│   │   ├── stream_memory.py    # StreamMemory — append-only event log
│   │   └── memory.py           # Memory facade (status + stream)
│   ├── Thinking/
│   │   ├── block.py            # Block base class + BlockResult
│   │   ├── prompts.py          # All LLM prompt templates
│   │   ├── dispatcher.py       # BlockDispatcher + LLM budget guard
│   │   └── blocks/
│   │       ├── mobility_block.py   # LLM movement decision
│   │       ├── needs_block.py      # Need decay + satisfaction
│   │       └── cognition_block.py  # Mood/curiosity/fatigue updates
│   └── Environment/
│       ├── eixample_overture.duckdb
│       └── eixample_osm.duckdb
├── Frontend/
│   └── index.html              # Leaflet.js interactive map
├── benchmark/
│   ├── 01_database_comparison.ipynb      # DuckDB vs SQLite vs PostgreSQL
│   ├── 02_llm_provider_comparison.ipynb  # Ollama vs vLLM vs GPT-4o
│   ├── 03_map_data_comparison.ipynb      # Overture vs OSM
│   ├── 04_vlm_perception_comparison.ipynb # PerceptionLM vs LLaVA vs GPT-4o-V
│   └── 05_system_integration_benchmark.ipynb # End-to-end latency + humanistic scoring
├── scripts/
│   ├── .env.example            # Shared environment template
│   └── streetview_analysis/
│       ├── run_analysis.py     # Street View pipeline entry point
│       ├── viewer.html         # Result viewer
│       └── README.md           # How to run the Street View pipeline
├── requirements.txt
├── start_system.bat            # Windows one-click launcher
└── README.md
```

---

## Benchmark Suite

Five Jupyter notebooks in `benchmark/` evaluate all major technology choices. Each is runnable independently. Metrics: **agent response latency** (ms/step) and **humanistic accuracy** (archetype-appropriate decisions).

| Notebook | Compares | Key question |
|---|---|---|
| `01_database_comparison` | DuckDB vs SQLite+SpatiaLite vs PostgreSQL+PostGIS | Fastest spatial query for 500 agents? |
| `02_llm_provider_comparison` | Ollama vs vLLM vs GPT-4o-mini vs GPT-4o | Best latency/accuracy trade-off? |
| `03_map_data_comparison` | Overture Maps vs OpenStreetMap | Better POI coverage and network completeness? |
| `04_vlm_perception_comparison` | PerceptionLM vs LLaVA vs GPT-4o-Vision vs MiniCPM-V | Best street environment understanding? |
| `05_system_integration_benchmark` | LLM-driven vs rule-based agents | Humanistic scoring across 5 dimensions |

**Humanistic scoring dimensions** (benchmark 05):
- **Diversity** — unique edges / total steps
- **Archetype consistency** — destination choices match declared archetype
- **Amenity plausibility** — visited amenity matches current need state
- **Spatial realism** — trajectory entropy vs pedestrian norms
- **Decision coherence** — LLM reasoning consistency across steps

---

## Documentation

- `SYSTEM_DOCUMENTATION.md` — full system architecture and implementation details
- `DUCKDB_INSPECTION_GUIDE.md` — how to inspect the spatial database
- `GCP_BIGQUERY_ACCESS_GUIDE.md` — Overture Maps via BigQuery
- `Backend/LLM/SETUP_GUIDE.md` — backend LLM setup for Ollama and vLLM
- `scripts/streetview_analysis/README.md` — setup and usage for the Street View analysis pipeline
- `benchmark/README.md` — benchmark setup and rubric

## Citation

[Add citation information when publishing]

## License

[Add license information]
