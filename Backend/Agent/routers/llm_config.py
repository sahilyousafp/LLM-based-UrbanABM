import asyncio
import logging
import os
import sys

from fastapi import APIRouter, Body
from pydantic import BaseModel

from state import sim

logger = logging.getLogger(__name__)
router = APIRouter()


class LLMConfigRequest(BaseModel):
    provider: str
    model: str
    base_url: str = ""


class ContainerStartRequest(BaseModel):
    provider: str


_CONTAINER_BAT: dict[str, str] = {
    "lmdeploy": "start_lmdeploy_qwen.bat",
    "vllm":     "start_vllm_docker.bat",
}


@router.post("/api/config/llm")
async def update_llm_config(req: LLMConfigRequest):
    # Keys are never accepted from the frontend — read from .env only
    os.environ["LLM_PROVIDER"] = req.provider
    os.environ["LLM_MODEL"] = req.model
    os.environ["LLM_BASE_URL"] = req.base_url

    from LLM.llm_config import LLMConfig
    from LLM.llm_client import LLMClient
    new_config = LLMConfig.from_env()
    sim.city_model.llm_client = LLMClient(new_config)
    for agent in sim.city_model.city_agents:
        agent.dispatcher.llm = sim.city_model.llm_client
        agent.dispatcher.needs_block.llm = sim.city_model.llm_client
        agent.dispatcher.cognition_block.llm = sim.city_model.llm_client
        agent.dispatcher.mobility_block.llm = sim.city_model.llm_client
    return {"status": "updated", "provider": req.provider, "model": req.model}


@router.post("/api/config/perception-mode")
async def update_perception_mode(mode: str = Body(..., embed=True)):
    if mode not in ["amenities", "perception", "both", "rule_based"]:
        return {"error": "Invalid mode. Must be 'amenities', 'perception', 'both', or 'rule_based'"}
    sim.city_model.perception_mode = mode
    print(f"Agent perception mode updated to: {mode}")
    return {"status": "updated", "mode": mode}


@router.get("/api/config/perception-mode")
async def get_perception_mode():
    return {"mode": sim.city_model.perception_mode}


@router.get("/api/config/archetypes")
async def get_archetype_configs():
    from LLM.Thinking.blocks.plan_block import load_plans_json
    plans = load_plans_json()
    result = {}
    for arch, data in plans.items():
        if arch.startswith("_"):
            continue
        result[arch] = {
            "nav_mode":    data.get("nav_mode", "both"),
            "gps_dist":    data.get("gps_dist", 120),
            "compass_dist": data.get("compass_dist", 60),
        }
    return result


@router.post("/api/config/archetype-nav")
async def save_archetype_nav(body: dict = Body(...)):
    import json
    from pathlib import Path
    from LLM.Thinking.blocks.plan_block import load_plans_json

    arch         = body.get("archetype", "")
    nav_mode     = body.get("nav_mode", "both")
    gps_dist     = int(body.get("gps_dist", 120))
    compass_dist = int(body.get("compass_dist", 60))

    if nav_mode not in ("gps", "direction_sense", "both", "none"):
        return {"error": f"Invalid nav_mode: {nav_mode}"}

    plans_path = Path(__file__).parent.parent.parent / "LLM" / "Thinking" / "plans.json"
    local_path = plans_path.with_name("plans.local.json")
    plans = load_plans_json(plans_path)

    if arch not in plans:
        return {"error": f"Unknown archetype: {arch}"}

    plans[arch]["nav_mode"]     = nav_mode
    plans[arch]["gps_dist"]     = gps_dist
    plans[arch]["compass_dist"] = compass_dist

    with open(local_path, "w", encoding="utf-8") as f:
        json.dump(plans, f, indent=2)

    logger.info(f"Saved nav config for '{arch}': nav_mode={nav_mode}, gps={gps_dist}m, compass={compass_dist}m")
    return {"ok": True, "archetype": arch, "nav_mode": nav_mode,
            "gps_dist": gps_dist, "compass_dist": compass_dist}


@router.get("/api/config/frontend")
async def get_frontend_config():
    return {
        "mapbox_token": os.environ.get("MAPBOX_TOKEN", ""),
        "llm_provider": os.environ.get("LLM_PROVIDER", "ollama"),
        "llm_model": os.environ.get("LLM_MODEL", ""),
        "num_agents": int(os.environ.get("NUM_AGENTS", 50)),
        "available_providers": [
            {"id": "gemini", "name": "Google Gemini 2.0 Flash Lite", "description": "Fast, efficient cloud LLM by Google — no local setup required"},
            {"id": "ollama", "name": "Ollama (Local)", "description": "Local LLM via Ollama — no GPU required"},
            {"id": "vllm", "name": "vLLM (Docker GPU)", "description": "High-performance GPU inference via vLLM Docker"},
            {"id": "lmdeploy", "name": "LMDeploy (Docker GPU)", "description": "High-performance GPU inference via LMDeploy Docker"},
        ],
    }


@router.get("/api/llm/stats")
async def get_llm_stats():
    return sim.city_model.llm_client.stats()


@router.get("/api/ollama/models")
async def get_ollama_models():
    ollama_base = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{ollama_base}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            names = [m["name"] for m in data.get("models", [])]
            return {"models": names}
    except Exception as exc:
        logger.warning(f"Could not reach Ollama at {ollama_base}: {exc}")
        return {"models": [], "error": str(exc)}


@router.post("/api/container/start")
async def start_container(req: ContainerStartRequest):
    from pathlib import Path
    bat_name = _CONTAINER_BAT.get(req.provider)
    if not bat_name:
        return {"status": "error", "message": f"No Docker startup script for provider '{req.provider}'"}
    bat = Path(__file__).parent.parent.parent / "LLM" / bat_name
    if not bat.exists():
        return {"status": "error", "message": f"{bat_name} not found"}
    try:
        if sys.platform == "win32":
            proc = await asyncio.create_subprocess_exec(
                "cmd.exe", "/c", str(bat),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                str(bat),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        ok = proc.returncode == 0
        return {"status": "ok" if ok else "error", "output": stdout.decode(errors="replace")[-2000:]}
    except asyncio.TimeoutError:
        return {"status": "starting", "message": "Container launch initiated (takes ~30s to be ready)"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
