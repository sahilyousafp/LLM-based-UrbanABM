# 00 — Project Overview

**Read this first.** This document answers *what* the system is and *why* it is built the way it is, before you look at any code.

---

## Research Question

> Can large language models drive realistic, individuated pedestrian behaviour in an urban agent-based simulation — and can the quality of that behaviour be rigorously measured?

The project simulates up to 500 autonomous pedestrians walking through **Barcelona's Eixample district**. Each agent has a persistent memory, emotional state, and a daily plan. At every step it uses an LLM to decide where to walk next, based on its archetype, current needs, and what it can see around it.

---

## Three-Tier Architecture

```
┌─────────────────────────────────────────────────────┐
│  Frontend  (Preact + Zustand + Mapbox GL + Three.js)│
│  Vite dev server / static build · localhost:8091    │
│  • Interactive panels (3/4/5) + modal workflows     │
│  • Live agent visualization on map                  │
│  • Thought stream, needs bars, perception view      │
└────────────────────┬────────────────────────────────┘
                     │  HTTP / REST (JSON + GeoJSON)
┌────────────────────▼────────────────────────────────┐
│  Backend API  (FastAPI + Uvicorn)                   │
│  http://localhost:8000                               │
│  Backend/Agent/map_server.py  ← entry point (~90L)  │
│  Backend/Agent/routers/       ← 9 domain routers    │
│  Backend/Agent/state.py       ← SimState singleton  │
│  • ~65 REST endpoints                               │
│  • Owns the simulation model                        │
│  • Streams agent state to frontend                  │
└────────┬────────────────────────────────────────────┘
         │
  ┌──────▼──────────────────────────────────────────┐
  │  Simulation Layer                               │
  │                                                 │
  │  Mesa ABM (model/)            LLM Client        │
  │  ┌─────────────────┐    ┌─────────────────────┐ │
  │  │  CityModel      │    │  AsyncOpenAI-compat  │ │
  │  │  500 CityAgents │◄──►│  Ollama / OpenAI /  │ │
  │  │  Dijkstra graph │    │  DeepSeek / Gemini   │ │
  │  └────────┬────────┘    └─────────────────────┘ │
  │           │                                      │
  │  ┌────────▼──────────────────────────────────────┐  │
  │  │  DuckDB + Spatial Extension (two files)     │  │
  │  │                                             │  │
  │  │  eixample_overture.duckdb  (read-write)     │  │
  │  │    buildings · amenities · walk_edges       │  │
  │  │    ext_weather · ext_transit_stops          │  │
  │  │                                             │  │
  │  │  perception.duckdb  (read-write, separate)  │  │
  │  │    streetview_perception                    │  │
  │  └─────────────────────────────────────────────┘  │
  └───────────────────────────────────────────────────┘
```

---

## Four Agent Archetypes

Every agent is assigned one of four archetypes at spawn. The archetype controls the LLM system prompt, daily plan, navigation budget, and memory consolidation interval.

| Archetype | Behaviour | Daily Plan Focus | Explore Budget |
|-----------|-----------|-----------------|----------------|
| **Resident** | Familiar routes, comfort-seeking, home-anchored | Home → errands → social → home | 1 free step |
| **Commuter** | Efficient, direct, time-conscious | Home → office → lunch → office → home | 0 (pure Dijkstra) |
| **Tourist** | Exploratory, curiosity-driven, novelty-biased | Hotel → landmarks → cafes → shops | 3 free steps |
| **Student** | Social, budget-conscious, energy-conserving | Dorm → library → cafe → park → social | 2 free steps |

"Explore budget" = how many free LLM-chosen steps occur before one forced Dijkstra step toward the destination. Commuters always take the shortest path; tourists wander widely.

---

## Key Design Decisions

### Why DuckDB?
Benchmark 01 (`benchmark/01_database_comparison.ipynb`) compared DuckDB, SQLite+SpatiaLite, and PostgreSQL+PostGIS. DuckDB was fastest for spatial queries against the ~8 MB Eixample dataset — sub-millisecond amenity lookups, no server process, single file. See `01_data_pipeline.md`.

### Why Mesa?
[Mesa](https://mesa.readthedocs.io/) is the standard Python ABM framework. It provides the Model/Agent/Scheduler pattern, handles the step loop, and integrates with [mesa-geo](https://mesa-geo.readthedocs.io/) for spatially-aware agents (geometry, CRS, GeoJSON export). Every `CityAgent` is a `mesa_geo.GeoAgent`.

### Why OpenAI-compatible LLM abstraction?
Almost every modern LLM server (Ollama, vLLM, DeepSeek, Gemini, Groq, OpenRouter) exposes an OpenAI-compatible `/v1/chat/completions` endpoint. Using the `openai` Python SDK as the sole client means swapping providers requires only a `.env` change — no code changes. See `05_llm_integration.md`.

### Why async?
500 agents × 3 LLM blocks = up to 1,500 concurrent API calls per step. `asyncio.gather` runs all agents in parallel, reducing step latency from ~500s (sequential) to ~0.5–2s. See `02_simulation_core.md`.

---

## Repository Layout

```
LLM_Based_UrbanABM/
├── Backend/
│   ├── Agent/           ← FastAPI server + Mesa model
│   │   ├── map_server.py    ← entry point
│   │   ├── state.py         ← SimState singleton
│   │   ├── paths.py         ← path constants
│   │   ├── db.py            ← DuckDB helper
│   │   ├── model/           ← CityModel + CityAgent package
│   │   └── routers/         ← 9 FastAPI domain routers
│   ├── LLM/             ← LLM client, memory, thinking blocks
│   └── Environment/     ← Spatial data pipelines + DuckDB files
├── Frontend/            ← Preact + Zustand + Vite + Mapbox + Three.js UI
├── benchmark/           ← 5 Jupyter evaluation notebooks
├── scripts/             ← Street View analysis pipeline
├── test/                ← Agent Lab research harness (port 8100)
├── Documentation/       ← Extended guides
├── Audit/               ← THIS FOLDER (step-by-step codebase guide)
├── .env.example         ← All environment variable defaults
├── CLAUDE.md            ← Project instructions for Claude Code
└── start_system.bat     ← One-click launcher (Windows)
```

---

## External References

| Resource | URL |
|----------|-----|
| Mesa ABM framework | https://mesa.readthedocs.io/ |
| mesa-geo spatial agents | https://mesa-geo.readthedocs.io/ |
| FastAPI | https://fastapi.tiangolo.com/ |
| Overture Maps Foundation | https://overturemaps.org/ |
| DuckDB | https://duckdb.org/ |
| OSMnx (OpenStreetMap) | https://osmnx.readthedocs.io/ |
| Mapbox GL JS | https://docs.mapbox.com/mapbox-gl-js/ |
| Three.js | https://threejs.org/docs/ |

---

**Next:** [`01_data_pipeline.md`](01_data_pipeline.md) — where the spatial data comes from and how it is stored.
