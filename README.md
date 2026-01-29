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
Frontend (Leaflet.js) ←→ API (FastAPI) ←→ ABM (Mesa) + LLM (Ollama/Llama 3.1)
                                           ↓
                                      DuckDB (Spatial)
```

## Features

- **500 Agents**: Navigating Barcelona's pedestrian network
- **Real-time LLM**: Natural language agent perspectives
- **Spatial Queries**: DuckDB with spatial extensions
- **Interactive Visualization**: Leaflet.js mapping
- **Decoupled Architecture**: Independent frontend/backend

## Research Applications

- Spatial cognition analysis
- Natural language generation from spatial data
- Agent-based urban simulation
- Human-environment interaction modeling

## Citation

[Add citation information when publishing]

## License

[Add license information]
