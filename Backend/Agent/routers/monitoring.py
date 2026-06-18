import asyncio

from fastapi import APIRouter

from state import sim

router = APIRouter()

_TIME_PHASES = ["morning", "afternoon", "evening", "night"]
_STEPS_PER_PHASE = 24


def _phase_from_step(step: int) -> str:
    return _TIME_PHASES[(step // _STEPS_PER_PHASE) % len(_TIME_PHASES)]


@router.get("/api/time_stats")
async def get_time_stats():
    """Aggregate agent stream events by time-of-day phase for thesis monitoring."""
    phases = {
        p: {"total": 0, "by_topic": {}, "samples": []}
        for p in _TIME_PHASES
    }

    async def collect_agent(agent):
        try:
            return await agent.memory.stream.get_recent_all(n=50)
        except Exception:
            return []

    all_results = await asyncio.gather(
        *[collect_agent(a) for a in sim.city_model.city_agents]
    )

    for nodes in all_results:
        for node in nodes:
            phase = _phase_from_step(node.step)
            bucket = phases[phase]
            bucket["total"] += 1
            bucket["by_topic"][node.topic] = bucket["by_topic"].get(node.topic, 0) + 1
            if len(bucket["samples"]) < 3:
                bucket["samples"].append(node.description[:120])

    return {
        "phases": phases,
        "current_phase": _phase_from_step(sim.city_model.steps),
        "steps_per_phase": _STEPS_PER_PHASE,
        "total_steps": sim.city_model.steps,
    }
