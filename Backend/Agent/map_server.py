import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root before any local imports
_PROJECT_ROOT = Path(__file__).parent.parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

# Put Backend root on sys.path so cross-module imports work (LLM.*, Memory.*, etc.)
_BACKEND_ROOT = Path(__file__).parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

# Put Environment dir on path so overture_to_duckdb and plugins are importable
_ENV_DIR = _BACKEND_ROOT / "Environment"
if str(_ENV_DIR) not in sys.path:
    sys.path.insert(0, str(_ENV_DIR))

# Put plugins parent on path
_PLUGINS_PARENT = _ENV_DIR
if str(_PLUGINS_PARENT) not in sys.path:
    sys.path.insert(0, str(_PLUGINS_PARENT))

# FastAPI app
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Urban ABM Backend API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialise the global sim state (builds city_model on import)
import state as _state

spawn_seed = os.environ.get("SPAWN_SEED")
if spawn_seed:
    spawn_seed = int(spawn_seed)
    print(f"[INFO] Using spawn seed from environment: {spawn_seed}")
else:
    print(f"[INFO] No SPAWN_SEED environment variable set (random spawning)")

num_agents = int(os.getenv("NUM_AGENTS", 50))
print(f"[INFO] Number of agents from environment: {num_agents}")

_state.sim.initial_num_agents = num_agents
_state.sim.initial_spawn_seed = spawn_seed
_state.sim.reset_model(num_agents=num_agents, spawn_seed=spawn_seed)

# Register all routers
from routers import spatial, agents, simulation, recording, streetview, llm_config, overture, vlm, monitoring

app.include_router(spatial.router)
app.include_router(agents.router)
app.include_router(simulation.router)
app.include_router(recording.router)
app.include_router(streetview.router)
app.include_router(llm_config.router)
app.include_router(overture.router)
app.include_router(vlm.router)
app.include_router(monitoring.router)


@app.get("/")
async def read_root():
    return {
        "status": "running",
        "message": "Urban ABM Backend API",
        "endpoints": [
            "/api/buildings", "/api/walk_network", "/api/roads",
            "/api/agents", "/api/agent/{agent_id}",
            "/api/agent/{agent_id}/summary", "/api/agent/{agent_id}/memory",
            "/api/agent/{agent_id}/stream", "/api/agent/{agent_id}/cognition",
            "/api/agents/summaries", "/api/amenities", "/api/walk_nodes",
            "/api/stats", "/api/tables", "/api/test",
            "/api/streetview_grid", "/api/streetview_grid/image/{filename}",
            "/api/streetview_grid/analysis/{lat}_{lon}",
            "/api/config/frontend", "/api/config/llm (POST)", "/api/llm/stats",
            "/api/step_continuous (POST)", "/api/step (POST)",
            "/api/recording/start (POST)", "/api/recording/stop (POST)", "/api/recording/status",
        ]
    }


if __name__ == "__main__":
    import uvicorn
    _host = os.getenv("HOST", "127.0.0.1")
    _port = int(os.getenv("PORT", "8000"))
    _reload = os.getenv("RELOAD", "true").lower() in ("true", "1", "yes")
    # String import required for reload; direct app avoids double module init
    _target = "map_server:app" if _reload else app
    uvicorn.run(_target, host=_host, port=_port, reload=_reload)
