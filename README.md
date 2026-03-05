# LLM-Based Urban Agent-Based Model

A research system integrating Large Language Models with spatial Agent-Based Modeling for urban cognition studies.

## Quick Start

### Prerequisites
1. Python 3.9+
2. Ollama (https://ollama.ai/download)

### Installation
```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start Ollama
ollama serve

# 3. Pull language model
ollama pull llama3.1

# 4. Start backend
cd Backend\Agent
python map_server.py

# 5. Open frontend
Open Frontend\index.html in browser
```

## Documentation

**Main Documentation:** `SYSTEM_DOCUMENTATION.md`
- Complete system architecture
- Implementation details
- Research applications
- Performance analysis

**Module-Specific:**
- `Backend/Agent/BACKEND_README.md` - API endpoints
- `Backend/LLM/README.md` - LLM integration
- `Backend/LLM/SETUP_GUIDE.md` - Detailed setup
- `Frontend/README.md` - Frontend usage

## System Overview

```
Frontend (Leaflet.js) ←→ API (FastAPI) ←→ ABM (Mesa) + LLM (Ollama/OpenAI/vLLM)
                                           ↓              ↓
                                      DuckDB (Spatial)  Memory + Thinking Blocks
```

## Features

- **500 Agents**: Navigating Barcelona's pedestrian network with LLM-driven decisions
- **Memory Module**: Per-agent KVMemory (status) + StreamMemory (event log)
- **Thinking Blocks**: MobilityBlock (LLM movement), NeedsBlock (decay + satisfaction), CognitionBlock (attitude updates)
- **Provider-Agnostic LLM**: Supports Ollama, OpenAI, vLLM, DeepSeek via OpenAI-compatible API
- **Agent Archetypes**: Resident, Commuter, Tourist, Student — each with distinct LLM-guided behaviour
- **Spatial Queries**: DuckDB with spatial extensions
- **Interactive Visualization**: Leaflet.js mapping
- **Benchmark Suite**: 5 Jupyter notebooks comparing DB engines, LLM providers, map datasets, VLMs

## Configuration

Copy `scripts/.env.example` to `scripts/.env` and configure:

```env
LLM_PROVIDER=ollama          # ollama | openai | deepseek | vllm
LLM_MODEL=llama3.1
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=                 # required for openai/deepseek
LLM_CALLS_PER_STEP=50        # max agents calling LLM per step (cost control)
```

## Benchmark Suite

```
benchmark/
├── 01_database_comparison.ipynb       # DuckDB vs SQLite vs PostgreSQL+PostGIS
├── 02_llm_provider_comparison.ipynb   # Ollama vs vLLM vs GPT-4o
├── 03_map_data_comparison.ipynb       # Overture vs OSM
├── 04_vlm_perception_comparison.ipynb # PerceptionLM vs LLaVA vs GPT-4o-Vision
└── 05_system_integration_benchmark.ipynb  # End-to-end latency + humanistic scoring
```

## Research Applications

- Spatial cognition analysis
- Natural language generation from spatial data
- Agent-based urban simulation
- Human-environment interaction modeling

## Citation

[Add citation information when publishing]

## License

[Add license information]
