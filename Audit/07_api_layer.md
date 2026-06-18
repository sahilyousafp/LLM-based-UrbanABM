# 07 — API Layer

The FastAPI server (`Backend/Agent/map_server.py`) is the bridge between the simulation and the frontend. This document lists every endpoint, its response shape, and the patterns used across the API.

**Key files:**
- `Backend/Agent/map_server.py` — entry point only (~90 lines): loads `.env`, creates `FastAPI` app, registers all routers
- `Backend/Agent/routers/` — 9 domain router modules (one per concern)
- `Backend/Agent/state.py` — `SimState` singleton that owns the `CityModel` and all job registries

---

## Server Setup

```python
# map_server.py — entry point only
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import state as _state

app = FastAPI(title="Urban ABM Backend API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], ...)

# Build CityModel at import time (before uvicorn accepts requests)
_state.sim.reset_model(num_agents=num_agents, spawn_seed=spawn_seed)

# Register all 9 domain routers
from routers import spatial, agents, simulation, recording, streetview, llm_config, overture, vlm, monitoring
app.include_router(spatial.router)      # /api/{buildings,walk_network,roads,...}
app.include_router(agents.router)       # /api/agents, /api/agent/{id}/*
app.include_router(simulation.router)   # /api/step, /api/step_continuous
app.include_router(recording.router)    # /api/recording/*
app.include_router(streetview.router)   # /api/streetview/*, /api/streetview_grid/*
app.include_router(llm_config.router)   # /api/config/*, /api/llm/*, /api/ollama/*
app.include_router(overture.router)     # /api/overture/*, /api/zone/*, /api/external/*
app.include_router(vlm.router)          # /api/vlm/*
app.include_router(monitoring.router)   # /api/time_stats
```

The model is owned by `SimState` — a singleton imported as `sim` from `state.py`. All routers share it via `from state import sim`. This replaces the old `global city_model` pattern.

### DuckDB Connection Sharing

On Windows, DuckDB holds an OS-level exclusive lock per file per process. Opening a second connection to the same `.duckdb` file raises an `IOException`. To avoid this, all router endpoints that query the main spatial DB use `db.py`:

```python
# Backend/Agent/db.py
def get_db_connection() -> _SharedConProxy:
    """Returns a proxy to city_model.con. Never opens a second file handle."""
    from state import sim
    con = getattr(sim.city_model, "con", None) if sim.city_model else None
    if con is not None:
        return _SharedConProxy(con)   # .close() is a no-op
    fallback = duckdb.connect(str(DB_PATH))  # only during startup before model ready
    ...
```

`_SharedConProxy` forwards `execute()` / `executemany()` to the shared connection and makes `.close()` a no-op — callers keep the `try/finally con.close()` pattern but no file handle is ever released prematurely.

For **perception queries**, routers use `sim.city_model.perception_con` directly — this is a separate file (`perception.duckdb`) with its own independent file handle.

```python
# Any router
from state import sim

sim.city_model          # CityModel instance
sim.sv_jobs             # streetview download jobs dict
sim.analyze_jobs        # VLM analysis jobs dict
sim.overture_jobs       # Overture pipeline jobs dict
sim.ext_jobs            # external plugin jobs dict
sim.reset_model(**kw)   # atomically rebuild CityModel
```

---

## Endpoint Reference

### Health & Configuration

| Method | Path | Response |
|--------|------|----------|
| `GET` | `/` | API root, list of all endpoints |
| `GET` | `/api/config/frontend` | `{mapbox_token, llm_provider, llm_model, num_agents, available_providers: []}` |
| `GET` | `/api/tables` | `[{name, row_count}, ...]` — DuckDB table inventory |
| `GET` | `/api/stats` | `{table_counts: {}, spatial_bbox: {w,s,e,n}}` |
| `GET` | `/api/zone/current` | `{bbox: [w,s,e,n]}` — user-drawn or auto-detected zone |

---

### Spatial Data (GeoJSON)

| Method | Path | Response |
|--------|------|----------|
| `GET` | `/api/buildings` | GeoJSON FeatureCollection (Polygon, properties: name, height, building_type) |
| `GET` | `/api/amenities` | GeoJSON FeatureCollection (Point, properties: name, amenity, id) |
| `GET` | `/api/walk_network` | GeoJSON FeatureCollection (LineString, properties: id, road_class, name, direction) |
| `GET` | `/api/walk_network/classes` | Same + `{road_class_counts: {footway: N, ...}}` |
| `GET` | `/api/walk_network/candidates` | `?bbox=w,s,e,n&spacing=200` → sampled Street View candidate points along edges |
| `GET` | `/api/walk_nodes` | GeoJSON FeatureCollection (Point, first 500 junction nodes) |
| `GET` | `/api/roads` | Drive edges (fallback if drive_edges missing) |

---

### Agents (Batch)

| Method | Path | Request | Response |
|--------|------|---------|----------|
| `GET` | `/api/agents` | — | GeoJSON FeatureCollection: all agent positions + archetype + nearby_count |
| `POST` | `/api/agents/respawn` | `{num_agents, gender, age}` | `{status, num_agents}` |
| `POST` | `/api/agents/respawn_advanced` | see below | `{status, num_agents}` |
| `GET` | `/api/agents/summaries` | — | LLM narratives for first 10 agents |

**respawn_advanced body:**
```json
{
  "num_agents": 50,
  "spawn_mode": "random",        // "random"|"click"|"poi"|"home_work"
  "archetype_mix": {
    "resident": 0.3,
    "commuter": 0.3,
    "tourist": 0.2,
    "student": 0.2
  },
  "spawn_points": [[lon, lat], ...],   // for "click" mode
  "gender": "mixed",
  "age_range": [20, 65]
}
```

---

### Agent Detail

| Method | Path | Response |
|--------|------|----------|
| `GET` | `/api/agent/{id}` | `{id, lon, lat, archetype, nearby_amenities: [], street_perception: {}}` |
| `GET` | `/api/agent/{id}/memory` | `{status: {all KV keys}, stream: {all topics}}` |
| `GET` | `/api/agent/{id}/stream` | `?topic=mobility&n=20` → `[{topic, step, description, metadata}, ...]` |
| `GET` | `/api/agent/{id}/cognition` | `{cognition_state, needs, destination, plan_phase}` |
| `GET` | `/api/agent/{id}/summary` | `{"summary": "LLM-generated 2-3 sentence narrative"}` |
| `GET` | `/api/agent/{id}/perception-text` | `{perception: {}, nearest_image_url: "..."}` |
| `GET` | `/api/agent/{id}/path-adherence` | `{adherence_pct: float, on_path: N, off_path: N}` |
| `GET` | `/api/agent/{id}/narrative-compare` | `{generic: "...", history_aware: "..."}` |
| `GET` | `/api/agent/{id}/results-summary` | `{vision: "...", mobility: "...", amenity_visit: "...", cognition: "...", needs: "..."}` |

---

### Simulation Control

| Method | Path | Response |
|--------|------|----------|
| `POST` | `/api/step` | `{step: int, time_of_day: str, agents: int, llm_stats: {}}` |
| `POST` | `/api/step_continuous` | `{step, time_of_day, agents: [{id, lon, lat, nearby_count, needs}], llm_stats}` |

The frontend uses `/api/step_continuous` exclusively — it returns agent positions immediately, avoiding a second `/api/agents` call.

---

### LLM Configuration

| Method | Path | Request | Response |
|--------|------|---------|----------|
| `GET` | `/api/llm/stats` | — | `{total_calls, total_input_tokens, total_output_tokens, total_latency_ms, errors, fallbacks}` |
| `POST` | `/api/config/llm` | `{provider, model, base_url?, api_key?}` | `{status, provider, model}` |
| `GET` | `/api/ollama/models` | — | `{models: ["llama3.1", "qwen2.5", ...]}` |
| `GET` | `/api/config/perception-mode` | — | `{mode: "both"}` |
| `POST` | `/api/config/perception-mode` | `{mode: "amenities"|"perception"|"both"|"rule_based"}` | `{status, mode}` |
| `GET` | `/api/config/archetypes` | — | `{resident: {nav_mode: "both"}, commuter: {...}, ...}` |

---

### Street View & VLM

| Method | Path | Notes |
|--------|------|-------|
| `POST` | `/api/streetview/download` | Starts background download job. Body: `{zone_bbox, spacing_m}` → `{job_id, total_candidates}` |
| `GET` | `/api/streetview/download/status/{job_id}` | `{status, completed, total, errors}` |
| `GET` | `/api/streetview/stats` | `{images: N, results: M}` |
| `DELETE` | `/api/streetview/images/unanalyzed` | Removes images with no VLM result |
| `POST` | `/api/streetview/analyze` | Starts background VLM analysis. Body: `{images: [keys]}` → `{job_id}` |
| `GET` | `/api/streetview/analyze/status/{job_id}` | `{status, done, total, log}` |
| `POST` | `/api/streetview/reimport-perception` | Re-parse all `*_analysis.json` → reload `perception.duckdb`. Returns `{ok, records_in_table}`. Parses files in a thread, writes via `city_model.perception_con` — no file-lock conflict. |
| `GET` | `/api/streetview_grid` | GeoJSON: all captured points (analysed + image-only) |
| `GET` | `/api/streetview_grid/image/{filename}` | Serves JPEG directly |
| `GET` | `/api/streetview_grid/json/{filename}` | Serves analysis JSON |
| `GET` | `/api/streetview_grid/analysis/{lat}_{lon}` | Perception dict from `perception.duckdb` for a coordinate (uses `city_model.perception_con`) |

---

### Recording

| Method | Path | Request | Response |
|--------|------|---------|----------|
| `POST` | `/api/recording/start` | `{session_name, include_thoughts, include_perception}` | `{session_id, status}` |
| `POST` | `/api/recording/stop` | — | `{session_id, total_steps, output_path}` |
| `GET` | `/api/recording/status` | — | `{is_recording, session_name, steps_recorded}` |

Recording saves full agent state (position, needs, cognition, decision reasoning) to GeoParquet files — compatible with QGIS, GeoPandas, and ML pipelines.

---

### Monitoring

| Method | Path | Response |
|--------|------|----------|
| `GET` | `/api/time_stats` | `{morning: {total, by_topic: {}, samples: []}, afternoon: {...}, evening: {...}, night: {...}}` |

`monitoring.py` aggregates every agent's recent stream events (last 50 each) into the four time-of-day phases (24 steps per phase, cycling morning → afternoon → evening → night). Used for thesis-level behavioural analysis — e.g. "do tourists generate more `amenity_visit` events in the afternoon?"

---

## Background Job Pattern

Long-running operations (download, VLM analysis) use a background job pattern. Job registries live on `SimState` so they survive across router module reloads:

```python
import asyncio, uuid
from state import sim

@router.post("/api/streetview/download")
async def start_download(body: DownloadRequest):
    job_id = str(uuid.uuid4())
    sim.sv_jobs[job_id] = {"status": "running", "completed": 0, "total": 0}
    
    async def _run():
        ...
        sim.sv_jobs[job_id]["status"] = "done"
    
    asyncio.create_task(_run())          # non-blocking
    return {"job_id": job_id, "status": "started"}

@router.get("/api/streetview/download/status/{job_id}")
async def download_status(job_id: str):
    return sim.sv_jobs.get(job_id, {"status": "not_found"})
```

Job registry per domain:

| Registry | Domain |
|----------|--------|
| `sim.sv_jobs` | Street View image downloads |
| `sim.analyze_jobs` | VLM analysis runs |
| `sim.overture_jobs` | Overture Maps pipeline |
| `sim.ext_jobs` | External data plugin downloads |

The frontend polls the status endpoint at 2s intervals until `"status": "done"`.

---

## Data Format Reference

### Agent GeoJSON (from `/api/agents`)
```json
{
  "type": "FeatureCollection",
  "features": [{
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [2.162, 41.386]},
    "properties": {
      "id": 42,
      "archetype": "tourist",
      "nearby_count": 3
    }
  }]
}
```

### Step Response (from `/api/step_continuous`)
```json
{
  "step": 47,
  "time_of_day": "afternoon",
  "agents": [
    {"id": 0, "lon": 2.161, "lat": 41.385, "nearby_count": 2,
     "needs": {"hunger": 0.4, "energy": 0.7, "social": 0.3, "comfort": 0.8}}
  ],
  "llm_stats": {"total_calls": 2350, "total_input_tokens": 141000, ...}
}
```

---

## External References

| Resource | URL |
|----------|-----|
| FastAPI documentation | https://fastapi.tiangolo.com/ |
| FastAPI background tasks | https://fastapi.tiangolo.com/tutorial/background-tasks/ |
| GeoJSON specification (RFC 7946) | https://datatracker.ietf.org/doc/html/rfc7946 |
| GeoParquet specification | https://geoparquet.org/ |
| Uvicorn ASGI server | https://www.uvicorn.org/ |

---

**Next:** [`08_frontend.md`](08_frontend.md) — the Preact/Zustand/Vite frontend, its stores, and the map panels.
