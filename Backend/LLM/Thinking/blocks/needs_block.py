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
DECAY_RATES = {
    "hunger": 0.015,   # Hunger increases ~1.5% per step — noticeable buildup
    "energy": 0.010,   # Energy depletes ~1.0% per step — meaningful changes
    "social": 0.010,   # Social need increases ~1.0% per step — visible growth
    "comfort": 0.015,  # Comfort erodes ~1.5% per step — matches the calibrated visual restoration scale
}

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

    async def run(self, step: int, nearby_amenities: Optional[list] = None, street_perception: Optional[dict] = None, **kwargs) -> BlockResult:
        """
        Args:
            step: current simulation step
            nearby_amenities: list of {"name", "type", "dist"} from model query
            street_perception: dict with scene_overview, buildings, vegetation, etc. from visual analysis
        """
        needs = await self.memory.status.get("needs", {})
        profile = await self.memory.status.get("agent_profile", {})
        archetype = profile.get("archetype", "resident")
        cognition = await self.memory.status.get("cognition_state", {})

        # Decay needs each step
        needs["hunger"] = min(1.0, needs.get("hunger", 0.5) + DECAY_RATES["hunger"])
        needs["energy"] = max(0.0, needs.get("energy", 1.0) - DECAY_RATES["energy"])
        needs["social"] = min(1.0, needs.get("social", 0.5) + DECAY_RATES["social"])
        needs["comfort"] = max(0.0, needs.get("comfort", 0.7) - DECAY_RATES["comfort"])

        visited_amenity = None
        llm_used = False
        satisfaction_source = "none"
        satisfaction_reasoning = "Needs updated via decay only"

        # 1. Visual satisfaction evaluation (every 5 steps to avoid fully cancelling decay)
        if street_perception and step % 5 == 0:
            visual_result = await self._evaluate_visual_satisfaction(
                archetype=archetype,
                needs=needs,
                cognition=cognition,
                street_perception=street_perception,
            )
            if visual_result:
                needs = visual_result["needs"]
                satisfaction_source = "visual"
                satisfaction_reasoning = visual_result.get("reasoning", "Visual environment affected needs")
                llm_used = visual_result.get("llm_used", False)

        # 2. Amenity satisfaction evaluation (if at an amenity)
        if nearby_amenities:
            closest = nearby_amenities[0]  # Already sorted by distance
            amenity_type = closest.get("type", "").lower()
            amenity_name = closest.get("name", "Unknown")

            amenity_result = await self._evaluate_amenity_satisfaction(
                archetype=archetype,
                needs=needs,
                cognition=cognition,
                amenity_name=amenity_name,
                amenity_type=amenity_type,
                street_perception=street_perception,
            )
            if amenity_result:
                needs = amenity_result["needs"]
                satisfaction_source = "amenity" if satisfaction_source == "none" else "combined"
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
        )
        response = await self.llm.chat_json(messages)

        if response and all(k in response for k in ("hunger_delta", "energy_delta", "social_delta")):
            needs["hunger"] = max(0.0, min(1.0, needs["hunger"] - response["hunger_delta"]))
            needs["energy"] = max(0.0, min(1.0, needs["energy"] + response["energy_delta"]))
            needs["social"] = max(0.0, min(1.0, needs["social"] - response["social_delta"]))
            needs["comfort"] = max(0.0, min(1.0, needs.get("comfort", 0.7) + response.get("comfort_delta", 0.0)))
            return {
                "needs": needs,
                "reasoning": response.get("reasoning", "Visual environment evaluated"),
                "llm_used": True,
            }
        return None

    async def _evaluate_amenity_satisfaction(
        self,
        archetype: str,
        needs: dict,
        cognition: dict,
        amenity_name: str,
        amenity_type: str,
        street_perception: Optional[dict] = None,
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
        )
        response = await self.llm.chat_json(messages)

        if response and all(k in response for k in ("hunger_delta", "energy_delta", "social_delta")):
            needs["hunger"] = max(0.0, min(1.0, needs["hunger"] - response["hunger_delta"]))
            needs["energy"] = max(0.0, min(1.0, needs["energy"] + response["energy_delta"]))
            needs["social"] = max(0.0, min(1.0, needs["social"] - response["social_delta"]))
            needs["comfort"] = max(0.0, min(1.0, needs.get("comfort", 0.7) + response.get("comfort_delta", 0.0)))
            return {
                "needs": needs,
                "reasoning": response.get("activity", f"Visited {amenity_name}"),
                "activity": response.get("activity", f"Visited {amenity_name}"),
                "llm_used": True,
            }
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
