from fastapi import APIRouter, Body
from state import sim

router = APIRouter()


@router.post("/api/step")
async def step_simulation():
    await sim.city_model.async_step()
    return {
        "step": sim.city_model.steps,
        "time_of_day": sim.city_model.time_of_day,
        "time_locked": sim.city_model.time_is_locked,
        "agents": len(sim.city_model.city_agents),
        "llm_stats": sim.city_model.llm_client.stats(),
    }


@router.post("/api/step_continuous")
async def step_continuous():
    await sim.city_model.async_step()
    agents_data = []
    for agent in sim.city_model.city_agents:
        needs = {}
        try:
            needs = await agent.memory.status.get("needs", {})
        except Exception:
            pass
        agents_data.append({
            "id": agent.unique_id,
            "lon": agent.geometry.x,
            "lat": agent.geometry.y,
            "nearby_count": len(agent.nearby_amenities),
            "needs": needs,
        })
    return {
        "step": sim.city_model.steps,
        "time_of_day": sim.city_model.time_of_day,
        "time_locked": sim.city_model.time_is_locked,
        "agents": agents_data,
        "llm_stats": sim.city_model.llm_client.stats(),
    }


@router.post("/api/time_override")
async def set_time_override(payload: dict = Body(...)):
    phase = payload.get("phase")
    try:
        sim.city_model.set_time_override(phase)
    except ValueError as e:
        return {"error": str(e)}
    return {
        "time_of_day": sim.city_model.time_of_day,
        "locked": sim.city_model.time_is_locked,
    }


@router.get("/api/test")
async def test_agents():
    return {
        "agent_count": len(sim.city_model.city_agents),
        "sample_agent": {
            "id": sim.city_model.city_agents[0].unique_id if sim.city_model.city_agents else None,
            "coords": [sim.city_model.city_agents[0].geometry.x, sim.city_model.city_agents[0].geometry.y] if sim.city_model.city_agents else None
        } if sim.city_model.city_agents else "No agents"
    }
