"""
NeedsBlock — manages agent needs (hunger, energy, social).
Rule-based decay each step; LLM evaluates satisfaction only when visiting an amenity.
Adapted from AgentSociety's NeedsBlock pattern.
"""
import logging
from typing import Optional

from LLM.Thinking.block import Block, BlockResult
from LLM.Thinking.prompts import needs_evaluation_prompt

logger = logging.getLogger(__name__)

# Need decay rates per step (subtracted each simulation step)
DECAY_RATES = {
    "hunger": 0.005,   # Hunger increases ~0.5% per step
    "energy": 0.003,   # Energy depletes ~0.3% per step
    "social": 0.002,   # Social need increases ~0.2% per step
}

# Amenity type to needs mapping (rule-based fallback)
AMENITY_NEED_MAP = {
    "restaurant": {"hunger": 0.4, "energy": 0.1, "social": 0.1},
    "cafe": {"hunger": 0.2, "energy": 0.15, "social": 0.2},
    "bar": {"hunger": 0.1, "energy": 0.0, "social": 0.35},
    "supermarket": {"hunger": 0.35, "energy": 0.0, "social": 0.0},
    "pharmacy": {"hunger": 0.0, "energy": 0.1, "social": 0.0},
    "park": {"hunger": 0.0, "energy": 0.25, "social": 0.15},
    "library": {"hunger": 0.0, "energy": 0.1, "social": 0.05},
    "gym": {"hunger": 0.0, "energy": -0.1, "social": 0.1},  # Gym costs energy
}


class NeedsBlock(Block):
    """Decays and updates agent needs each step."""

    async def run(self, step: int, nearby_amenities: Optional[list] = None, **kwargs) -> BlockResult:
        """
        Args:
            step: current simulation step
            nearby_amenities: list of {"name", "type", "dist"} from model query
        """
        needs = await self.memory.status.get("needs", {})
        profile = await self.memory.status.get("agent_profile", {})
        archetype = profile.get("archetype", "resident")

        # Decay needs each step
        needs["hunger"] = min(1.0, needs.get("hunger", 0.5) + DECAY_RATES["hunger"])
        needs["energy"] = max(0.0, needs.get("energy", 1.0) - DECAY_RATES["energy"])
        needs["social"] = min(1.0, needs.get("social", 0.5) + DECAY_RATES["social"])

        visited_amenity = None
        llm_used = False

        # If at an amenity, evaluate satisfaction
        if nearby_amenities:
            closest = nearby_amenities[0]  # Already sorted by distance
            amenity_type = closest.get("type", "").lower()
            amenity_name = closest.get("name", "Unknown")

            # Try LLM evaluation for richer satisfaction modeling
            messages = needs_evaluation_prompt(
                archetype=archetype,
                needs=needs,
                amenity_name=amenity_name,
                amenity_type=amenity_type,
            )
            response = await self.llm.chat_json(messages)

            if response and all(k in response for k in ("hunger_delta", "energy_delta", "social_delta")):
                needs["hunger"] = max(0.0, min(1.0, needs["hunger"] - response["hunger_delta"]))
                needs["energy"] = max(0.0, min(1.0, needs["energy"] + response["energy_delta"]))
                needs["social"] = max(0.0, min(1.0, needs["social"] - response["social_delta"]))
                activity = response.get("activity", f"Visited {amenity_name}")
                llm_used = True
            else:
                # Rule-based fallback
                deltas = AMENITY_NEED_MAP.get(amenity_type, {})
                needs["hunger"] = max(0.0, min(1.0, needs["hunger"] - deltas.get("hunger", 0)))
                needs["energy"] = max(0.0, min(1.0, needs["energy"] + deltas.get("energy", 0)))
                needs["social"] = max(0.0, min(1.0, needs["social"] - deltas.get("social", 0)))
                activity = f"Visited {amenity_name} ({amenity_type})"

            visited_amenity = {"name": amenity_name, "type": amenity_type}

            # Log amenity visit to stream
            await self.memory.stream.add(
                topic="amenity_visit",
                step=step,
                description=f"{activity}. Needs after: hunger={needs['hunger']:.2f}, "
                            f"energy={needs['energy']:.2f}, social={needs['social']:.2f}",
                metadata={"amenity": visited_amenity, "llm_used": llm_used},
            )

            # Append to visited amenities list (keep last 20)
            visited_list = await self.memory.status.get("visited_amenities", [])
            visited_list.append({**visited_amenity, "step": step})
            if len(visited_list) > 20:
                visited_list = visited_list[-20:]
            await self.memory.status.update("visited_amenities", visited_list)

        await self.memory.status.update("needs", needs)

        return BlockResult(
            action="needs_updated",
            params={
                "needs": needs,
                "visited_amenity": visited_amenity,
            },
            reasoning=f"Needs decayed; {'visited ' + (visited_amenity or {}).get('name', '') if visited_amenity else 'no amenity visit'}",
            fallback=not llm_used,
        )
