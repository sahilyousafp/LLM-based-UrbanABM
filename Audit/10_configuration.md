# 10 — Configuration & Running the System

This is the operational reference. By the time you read this, you should understand all the components that these settings control.

---

## Quick Start

### Option A: One-Click (Windows)
```
double-click  start_system.bat
```
Starts all three servers and opens `http://localhost:8091` in your browser.

### Option B: Manual (any platform)
```bash
# Terminal 1 — Backend API (port 8000)
cd Backend/Agent
python map_server.py

# Terminal 2 — Frontend static server (port 8091)
cd Frontend
python -m http.server 8091

# Terminal 3 — Optional: Agent Lab research harness (port 8100)
cd test
python agent_lab_server.py

# Open in browser:
http://localhost:8091
```

### Frontend TypeScript (development only)
```bash
cd Frontend
npm install        # one-time setup
npm run dev        # watch mode — rebuilds on file change
# OR
npm run build      # production bundle
```

---

## Environment Variables — Complete Reference

Copy `.env.example` to `.env` and edit. The file is read at server startup by `Backend/Agent/map_server.py`.

### LLM Provider

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `LLM_PROVIDER` | str | `ollama` | Provider: `ollama` \| `openai` \| `deepseek` \| `gemini` \| `vllm` \| `lmdeploy` \| `groq` \| `openrouter` \| `docker` |
| `LLM_MODEL` | str | `llama3.1` | Model name for the chosen provider |
| `LLM_API_KEY` | str | *(empty)* | API key (not needed for Ollama) |
| `LLM_BASE_URL` | str | *(empty)* | Override endpoint URL (for vLLM, custom servers) |
| `LLM_TIMEOUT` | int | `60` | Per-request timeout in seconds |
| `LLM_MAX_TOKENS` | int | `256` | Max response tokens per LLM call |
| `LLM_TEMPERATURE` | float | `0.7` | Sampling temperature (0=deterministic, 1=diverse) |
| `LLM_MAX_CONNECTIONS` | int | `100` | AsyncHTTP connection pool size |
| `LLM_MAX_KEEPALIVE_CONNECTIONS` | int | `20` | Keepalive connection pool |
| `LLM_CONNECT_TIMEOUT` | int | `10` | Connection timeout in seconds |
| `LLM_CIRCUIT_BREAKER_THRESHOLD` | int | `5` | Failures before circuit opens |
| `LLM_CIRCUIT_BREAKER_RECOVERY` | int | `30` | Recovery window in seconds |

### API Keys

| Variable | Required for | Where to get it |
|----------|-------------|-----------------|
| `MAPBOX_TOKEN` | Map rendering in frontend | https://account.mapbox.com/ |
| `OPENAI_API_KEY` | OpenAI provider | https://platform.openai.com/api-keys |
| `DEEPSEEK_API_KEY` | DeepSeek provider | https://platform.deepseek.com/ |
| `GEMINI_API_KEY` | Gemini provider | https://aistudio.google.com/app/apikey |
| `HF_TOKEN` | HuggingFace models | https://huggingface.co/settings/tokens |
| `GOOGLE_STREETVIEW_API_KEY` | Street View image download | https://console.cloud.google.com/ (Maps Static API) |
| `GOOGLE_API_KEY` | BigQuery (Overture Maps fallback) | https://console.cloud.google.com/ |
| `GOOGLE_APPLICATION_CREDENTIALS` | BigQuery (service account) | GCP Console → IAM → Service Accounts |
| `GCP_PROJECT_ID` | BigQuery queries | Your GCP project ID |

### Agent Simulation

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `NUM_AGENTS` | int | `50` | Agent count at startup (1–500 recommended) |
| `SPAWN_SEED` | int | *(empty)* | Reproducible spawn positions (empty = random) |
| `LLM_CALLS_PER_STEP` | int | `50` | Agents using LLM per step (0 = fully rule-based) |
| `PERCEPTION_MODE` | str | `both` | `amenities` \| `perception` \| `both` \| `rule_based` |
| `ROUTING_MODE` | str | `all` | `all` (all road classes) \| `footway` (pedestrian only) |

### Database & Data

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `DATABASE_PATH` | path | `../Environment/eixample_overture.duckdb` | Path to main spatial DuckDB (buildings, amenities, walk network) |
| `OVERTURE_RELEASE` | str | `2024-11-13.0` | Overture Maps snapshot version |
| `GTFS_URL` | str | *(empty)* | Transit feed URL (auto-resolved from city bbox if empty) |

### Server

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `HOST` | str | `127.0.0.1` | Bind address |
| `PORT` | int | `8000` | Backend API port |
| `RELOAD` | bool | `false` | Uvicorn auto-reload on file change. **Keep `false` on Windows** — `true` spawns a new worker before the old one releases the DuckDB file lock, causing an `IOException` at startup. |

---

## LLM Provider Setup Matrix

| Provider | Cost | Quality | Setup |
|----------|------|---------|-------|
| **Ollama** (local) | Free | Good | `ollama serve && ollama pull llama3.1` |
| **OpenAI GPT-4o-mini** | ~$0.15/1M tokens | Excellent | Set `OPENAI_API_KEY` |
| **DeepSeek** | ~$0.14/1M tokens | Very good | Set `DEEPSEEK_API_KEY` |
| **Gemini 2.0 Flash** | Free tier available | Good | Set `GEMINI_API_KEY` |
| **vLLM** (local GPU) | Free | Excellent | `pip install vllm && python -m vllm.entrypoints.openai.api_server ...` |
| **Groq** | Free tier | Fast | Set `GROQ_API_KEY` |
| **OpenRouter** | Pay-per-use | Varies | Set `OPENROUTER_API_KEY` |

### Ollama Setup (Recommended for Development)
```bash
# Install from https://ollama.com/download
ollama serve                    # start Ollama server (separate terminal)
ollama pull llama3.1            # 4.7 GB — good general reasoning
ollama pull qwen2.5:7b          # 4.7 GB — excellent JSON compliance
ollama pull qwen2.5:3b          # 2.0 GB — fast, less accurate
```

---

## Perception Mode Comparison

| Mode | DuckDB queries | LLM calls | Latency | Use when |
|------|---------------|-----------|---------|----------|
| `rule_based` | None | None | ~10ms/step | Baseline, debugging, high agent counts |
| `amenities` | Amenity lookup | NeedsBlock (at amenities) | ~50ms/step | Need satisfaction without visual context |
| `perception` | Streetview lookup | NeedsBlock (visual) | ~100ms/step | Visual environment without amenity context |
| `both` | Both | Both | ~150ms/step | **Default** — full realism |

---

## Simulation Speed Tuning

| Setting | Effect | When to use |
|---------|--------|-------------|
| `LLM_CALLS_PER_STEP=0` | ~10ms/step, rule-based | Stress testing, many agents |
| `LLM_CALLS_PER_STEP=10` | ~200ms/step | Development, quick iteration |
| `LLM_CALLS_PER_STEP=50` | ~500ms–2s/step | **Default** |
| `LLM_CALLS_PER_STEP=500` | 5–30s/step | Research, GPU required |
| `NUM_AGENTS=10` | Minimal state | Debugging single agents |
| `NUM_AGENTS=500` | Full simulation | Production / recordings |

---

## Common Debugging Patterns

From `CLAUDE.md` and `Documentation/`:

| Symptom | Where to look | Fix |
|---------|--------------|-----|
| Agent stuck at same position | `GET /api/agent/{id}/memory` → check `destination.target_node` | Clear destination: `POST /api/agent/{id}/clear-destination` |
| No LLM decisions (all fallback) | `GET /api/llm/stats` → check `total_errors` | Check `LLM_PROVIDER` and API key; check Ollama is running |
| Agents clumping at origin | `GET /api/agents` → all at same coords | Check DuckDB `walk_edges` loaded: `GET /api/tables` |
| Slow steps (>5s) | `GET /api/llm/stats` → check `total_latency_ms` | Reduce `LLM_CALLS_PER_STEP` or switch to faster provider |
| No street perception | `GET /api/streetview/stats` → check `results: 0` | Toggle map mode → run Street View download + VLM analysis in the map-mode slots, then click "Sync Results → DB" |
| Perception import fails | `POST /api/streetview/reimport-perception` returns `{ok: false}` | Check that `city_model.perception_con` is not None (backend must be running) — restart backend if needed |
| `Cannot open file … being used by another process` | DuckDB IOException at startup | Set `RELOAD=false` in `.env`; kill any stale Python process holding the .duckdb file |
| Disconnected network | Agents can't reach destination | Run `Backend/Environment/network_connector.py` |

---

## Port Summary

| Port | Service | Started by |
|------|---------|-----------|
| `8000` | FastAPI backend | `python Backend/Agent/map_server.py` |
| `8091` | Frontend static server | `python -m http.server 8091` |
| `8100` | Agent Lab (optional) | `python test/agent_lab_server.py` |
| `11434` | Ollama (if local) | `ollama serve` |

---

## Documentation Index

| File | Purpose |
|------|---------|
| `README.md` | Project overview + quick start |
| `SYSTEM_DOCUMENTATION.md` | Full architecture reference (32 KB) |
| `CONFIGURATION_GUIDE.md` | All config options (15 KB) |
| `DUCKDB_INSPECTION_GUIDE.md` | Database queries + schema inspection |
| `GCP_BIGQUERY_ACCESS_GUIDE.md` | Overture Maps via BigQuery |
| `OSM_vs_OVERTURE_BIGQUERY.md` | Data source comparison analysis |
| `Backend/LLM/SETUP_GUIDE.md` | LLM provider step-by-step setup |
| `benchmark/README.md` | Benchmark methodology + rubric |
| `Documentation/Research_Questions.md` | Core research hypotheses |

---

## External References

| Resource | URL |
|----------|-----|
| Ollama download | https://ollama.com/download |
| OpenAI API keys | https://platform.openai.com/api-keys |
| DeepSeek platform | https://platform.deepseek.com/ |
| Google AI Studio (Gemini) | https://aistudio.google.com/app/apikey |
| Mapbox account (token) | https://account.mapbox.com/ |
| Google Street View Static API | https://developers.google.com/maps/documentation/streetview/ |
| GCP Console (BigQuery credentials) | https://console.cloud.google.com/ |
| HuggingFace tokens | https://huggingface.co/settings/tokens |

---

**You have now read the full codebase audit.** Return to [`00_overview.md`](00_overview.md) for the architecture diagram, or jump to any specific layer using the links at the bottom of each file.
