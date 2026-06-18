"""
NeedsBlock — manages agent needs (hunger, energy, social).
Rule-based decay each step; LLM evaluates satisfaction from visual environment and amenities.
Adapted from AgentSociety's NeedsBlock pattern.
"""
import logging
from typing import Optional

from LLM.Thinking.block import Block, BlockResult
from LLM.Thinking.prompts import needs_evaluation_prompt, visual_satisfaction_prompt

logger = logging.getLogger(__name__)

# Need decay rates per step (subtracted each simulation step)
# Each step ≈ 30s of walking. Rates calibrated so hunger fills in ~2h (240 steps),
# energy drains in ~3h, social/comfort shift noticeably over 2–3h walks.
DECAY_RATES = {
    "hunger": 0.003,   # 0→full in ~333 steps ≈ ~2.8h walking
    "energy": 0.003,   # full→0 in ~333 steps ≈ ~2.8h walking
    "social": 0.003,   # 0→full in ~333 steps ≈ ~2.8h walking
    "comfort": 0.003,  # full→0 in ~333 steps ≈ ~2.8h walking
}

# Agent must be within this distance (metres) of an amenity to receive satisfaction
AMENITY_SATISFACTION_RADIUS_M = 30

# Amenity type to needs mapping (rule-based fallback)
AMENITY_NEED_MAP = {
    "restaurant": {"hunger": 0.4, "energy": 0.1, "social": 0.1, "comfort": 0.05},
    "cafe":        {"hunger": 0.2, "energy": 0.15, "social": 0.2, "comfort": 0.08},
    "bar":         {"hunger": 0.1, "energy": 0.0, "social": 0.35, "comfort": 0.05},
    "supermarket": {"hunger": 0.35, "energy": 0.0, "social": 0.0, "comfort": 0.02},
    "pharmacy":    {"hunger": 0.0, "energy": 0.1, "social": 0.0, "comfort": 0.03},
    "park":        {"hunger": 0.0, "energy": 0.25, "social": 0.15, "comfort": 0.10},
    "library":     {"hunger": 0.0, "energy": 0.1, "social": 0.05, "comfort": 0.08},
    "gym":         {"hunger": 0.0, "energy": -0.1, "social": 0.1, "comfort": 0.04},  # Gym costs energy
}


class NeedsBlock(Block):
    """Decays and updates agent needs each step."""

    async def run(self, step: int, nearby_amenities: Optional[list] = None, street_perception: Optional[dict] = None, nearby_agents: Optional[list] = None, **kwargs) -> BlockResult:
        """
        Args:
            step: current simulation step
            nearby_amenities: list of {"name", "type", "dist"} from model query
            street_perception: dict with scene_overview, buildings, vegetation, etc. from visual analysis
            nearby_agents: list of {"id", "archetype", "dist_m"} agents within ~55m
        """
        time_of_day = kwargs.get("time_of_day", "")
        needs = await self.memory.status.get("needs", {})
        profile = await self.memory.status.get("agent_profile", {})
        archetype = profile.get("archetype", "resident")
        cognition = await self.memory.status.get("cognition_state", {})

        # Log perception to stream whenever the agent enters a new scene.
        # Uses _last_scene_key for deduplication — identical scenes are not re-logged,
        # preventing the 100-entry stream buffer from filling with duplicate data.
        if street_perception:
            scene_key = (street_perception.get("scene_overview") or "")[:80]
            last_key = await self.memory.status.get("_last_scene_key", "")
            if scene_key and scene_key != last_key:
                await self.memory.stream.add(
                    topic="perception",
                    step=step,
                    description=street_perception.get("scene_overview", ""),
                    metadata={
                        "buildings":           street_perception.get("buildings", ""),
                        "vegetation":          street_perception.get("vegetation", ""),
                        "pedestrian_activity": street_perception.get("pedestrian_activity", ""),
                        "lighting_atmosphere": street_perception.get("lighting_atmosphere", ""),
                        "spatial_enclosure":   street_perception.get("spatial_enclosure", ""),
                        f"as_{archetype}":     street_perception.get(f"as_{archetype}", ""),
                    },
                )
                await self.memory.status.update("_last_scene_key", scene_key)

        # Weather multipliers from ext_weather snapshot (loaded at model init)
        weather = self.context.get("current_weather", {})
        _rain_mult = 1.5 if weather.get("rain_mm", 0) > 1.0 else 1.0
        _heat_mult = 1.3 if weather.get("temp_c", 20) > 30 else 1.0
        _wind_mult = 1.2 if weather.get("wind_ms", 0) > 8 else 1.0

        # Cross-need coupling multipliers (computed before decay so they use pre-step values)
        _cur_hunger  = needs.get("hunger", 0.5)
        _cur_energy  = needs.get("energy", 1.0)
        _cur_comfort = needs.get("comfort", 0.7)
        # hunger > 0.5 → up to ×1.3 faster energy drain (starving = less fuel for walking)
        _hunger_energy_mult = 1.0 + 0.3 * max(0.0, (_cur_hunger - 0.5) / 0.5)
        # comfort < 0.5 → up to ×1.2 faster energy drain (discomfort is tiring)
        _comfort_energy_mult = 1.0 + 0.2 * max(0.0, (0.5 - _cur_comfort) / 0.5)
        # energy < 0.3 → up to ×1.2 faster hunger rise (exhaustion amplifies hunger signals)
        _energy_hunger_mult = 1.0 + 0.2 * max(0.0, (0.3 - _cur_energy) / 0.3)
        # energy < 0.3 → crowd social bonus scales down to zero (too tired to engage)
        _social_energy_scale = min(1.0, _cur_energy / 0.3) if _cur_energy < 0.3 else 1.0

        # Decay needs each step (weather + cross-need coupling)
        needs["hunger"] = min(1.0, _cur_hunger + DECAY_RATES["hunger"] * _energy_hunger_mult)
        needs["energy"] = max(0.0, _cur_energy - DECAY_RATES["energy"] * _heat_mult * _hunger_energy_mult * _comfort_energy_mult)
        needs["social"] = min(1.0, needs.get("social", 0.5) + DECAY_RATES["social"])
        needs["comfort"] = max(0.0, _cur_comfort - DECAY_RATES["comfort"] * _rain_mult * _wind_mult)

        visited_amenity = None
        llm_used = False
        sources = []  # accumulates all satisfaction sources this step
        satisfaction_reasoning = "Needs updated via decay only"

        # 0. Crowd social bonus — proximity to other agents reduces social need (rule-based)
        if nearby_agents:
            crowd_bonus = min(0.05, len(nearby_agents) * 0.01) * _social_energy_scale
            needs["social"] = max(0.0, needs["social"] - crowd_bonus)
            sources.append("crowd")
            satisfaction_reasoning = f"crowd nearby ({len(nearby_agents)} agents), social -{crowd_bonus:.2f}"
            await self.memory.stream.add(
                topic="needs",
                step=step,
                description=f"Crowd nearby: {len(nearby_agents)} agent(s) within 55m. Social need reduced by {crowd_bonus:.2f}.",
                metadata={"crowd_count": len(nearby_agents), "crowd_bonus": crowd_bonus},
            )

        # 1. Visual satisfaction evaluation (every 5 steps to avoid fully cancelling decay)
        if street_perception and step % 5 == 0:
            visual_result = await self._evaluate_visual_satisfaction(
                archetype=archetype,
                needs=needs,
                cognition=cognition,
                street_perception=street_perception,
                time_of_day=time_of_day,
            )
            if visual_result:
                needs = visual_result["needs"]
                sources.append("visual")
                satisfaction_reasoning = visual_result.get("reasoning", "Visual environment affected needs")
                llm_used = visual_result.get("llm_used", False)
                await self.memory.stream.add(
                    topic="perception",
                    step=step,
                    description=f"Visual environment affected needs: {satisfaction_reasoning}",
                    metadata={"source": "visual_satisfaction", "llm_used": llm_used},
                )

        # 2. Amenity satisfaction evaluation (only if agent is physically at the amenity)
        # nearby_amenities is sorted by dist; require the closest to be within
        # AMENITY_SATISFACTION_RADIUS_M so passing restaurants don't satisfy hunger.
        if nearby_amenities:
            closest = nearby_amenities[0]  # Already sorted by distance
            if closest.get("dist", 9999) <= AMENITY_SATISFACTION_RADIUS_M:
                amenity_type = closest.get("type", "").lower()
                amenity_name = closest.get("name", "Unknown")

                amenity_result = await self._evaluate_amenity_satisfaction(
                    archetype=archetype,
                    needs=needs,
                    cognition=cognition,
                    amenity_name=amenity_name,
                    amenity_type=amenity_type,
                    street_perception=street_perception,
                    time_of_day=time_of_day,
                )
                if amenity_result:
                    needs = amenity_result["needs"]
                    sources.append("amenity")
                    satisfaction_reasoning = amenity_result.get("reasoning", f"Visited {amenity_name}")
                    visited_amenity = {"name": amenity_name, "type": amenity_type}
                    llm_used = amenity_result.get("llm_used", False) or llm_used

                    # Log amenity visit to stream
                    await self.memory.stream.add(
                        topic="amenity_visit",
                        step=step,
                        description=f"{amenity_result.get('activity', f'Visited {amenity_name}')}. Needs after: hunger={needs['hunger']:.2f}, "
                                    f"energy={needs['energy']:.2f}, social={needs['social']:.2f}, comfort={needs['comfort']:.2f}",
                        metadata={"amenity": visited_amenity, "llm_used": llm_used},
                    )

                    # Append to visited amenities list (keep last 20)
                    visited_list = await self.memory.status.get("visited_amenities", [])
                    visited_list.append({**visited_amenity, "step": step})
                    if len(visited_list) > 20:
                        visited_list = visited_list[-20:]
                    await self.memory.status.update("visited_amenities", visited_list)

        satisfaction_source = "+".join(sources) if sources else "none"

        await self.memory.status.update("needs", needs)
        await self.memory.status.update("satisfaction_source", satisfaction_source)
        await self.memory.status.update("satisfaction_reasoning", satisfaction_reasoning)

        return BlockResult(
            action="needs_updated",
            params={
                "needs": needs,
                "visited_amenity": visited_amenity,
                "satisfaction_source": satisfaction_source,
            },
            reasoning=f"Needs decayed; {satisfaction_source}: {satisfaction_reasoning}",
            fallback=not llm_used,
        )

    async def _evaluate_visual_satisfaction(
        self,
        archetype: str,
        needs: dict,
        cognition: dict,
        street_perception: dict,
        time_of_day: str = "",
    ) -> Optional[dict]:
        """
        Evaluate how the visual street environment affects agent needs.
        Uses LLM to assess buildings, vegetation, pedestrian activity, lighting.
        Returns updated needs with reasoning.
        """
        messages = visual_satisfaction_prompt(
            archetype=archetype,
            needs=needs,
            cognition=cognition,
            street_perception=street_perception,
            time_of_day=time_of_day,
        )
        response = await self.llm.chat_json(messages)

        if response and all(k in response for k in ("hunger_delta", "energy_delta", "social_delta")):
            try:
                needs["hunger"] = max(0.0, min(1.0, needs["hunger"] - float(response["hunger_delta"])))
                needs["energy"] = max(0.0, min(1.0, needs["energy"] + float(response["energy_delta"])))
                needs["social"] = max(0.0, min(1.0, needs["social"] - float(response["social_delta"])))
                needs["comfort"] = max(0.0, min(1.0, needs.get("comfort", 0.7) + float(response.get("comfort_delta", 0.0))))
                return {
                    "needs": needs,
                    "reasoning": response.get("reasoning", "Visual environment evaluated"),
                    "llm_used": True,
                }
            except (ValueError, TypeError):
                pass
        return None

    async def _evaluate_amenity_satisfaction(
        self,
        archetype: str,
        needs: dict,
        cognition: dict,
        amenity_name: str,
        amenity_type: str,
        street_perception: Optional[dict] = None,
        time_of_day: str = "",
    ) -> Optional[dict]:
        """
        Evaluate how visiting this amenity affects agent needs.
        Uses LLM with cognition state and surrounding context.
        Falls back to rule-based AMENITY_NEED_MAP if LLM fails.
        """
        messages = needs_evaluation_prompt(
            archetype=archetype,
            needs=needs,
            cognition=cognition,
            amenity_name=amenity_name,
            amenity_type=amenity_type,
            street_perception=street_perception,
            time_of_day=time_of_day,
        )
        response = await self.llm.chat_json(messages)

        if response and all(k in response for k in ("hunger_delta", "energy_delta", "social_delta")):
            try:
                needs["hunger"] = max(0.0, min(1.0, needs["hunger"] - float(response["hunger_delta"])))
                needs["energy"] = max(0.0, min(1.0, needs["energy"] + float(response["energy_delta"])))
                needs["social"] = max(0.0, min(1.0, needs["social"] - float(response["social_delta"])))
                needs["comfort"] = max(0.0, min(1.0, needs.get("comfort", 0.7) + float(response.get("comfort_delta", 0.0))))
                return {
                    "needs": needs,
                    "reasoning": response.get("activity", f"Visited {amenity_name}"),
                    "activity": response.get("activity", f"Visited {amenity_name}"),
                    "llm_used": True,
                }
            except (ValueError, TypeError):
                pass
        else:
            # Rule-based fallback
            deltas = AMENITY_NEED_MAP.get(amenity_type, {})
            needs["hunger"] = max(0.0, min(1.0, needs["hunger"] - deltas.get("hunger", 0)))
            needs["energy"] = max(0.0, min(1.0, needs["energy"] + deltas.get("energy", 0)))
            needs["social"] = max(0.0, min(1.0, needs["social"] - deltas.get("social", 0)))
            needs["comfort"] = max(0.0, min(1.0, needs.get("comfort", 0.7) + deltas.get("comfort", 0)))
            activity = f"Visited {amenity_name} ({amenity_type})"
            return {
                "needs": needs,
                "reasoning": activity,
                "activity": activity,
                "llm_used": False,
            }
