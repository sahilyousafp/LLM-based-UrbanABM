"""
MobilityBlock — LLM-driven movement decision.
Replaces the random edge selection in CityAgent with an LLM-reasoned choice.
Adapted from AgentSociety's MobilityBlock 3-stage pipeline, simplified for our network model.

Decision pipeline:
  1. Read agent memory (position, needs, cognition, recent stream)
  2. Get candidate edges from model context
  3. LLM selects best candidate given agent profile
  4. Fallback to least-visited edge on LLM failure
  5. Log decision to stream memory
"""
import random
import logging
from typing import Any

from Thinking.block import Block, BlockResult
from Thinking.prompts import mobility_decision_prompt

logger = logging.getLogger(__name__)


class MobilityBlock(Block):
    """Selects the next edge/destination using LLM reasoning."""

    async def run(self, step: int, candidate_edges: list[dict], **kwargs) -> BlockResult:
        """
        Args:
            step: current simulation step
            candidate_edges: list of {"edge_id", "geom", "direction", "amenities", "description"}
        Returns:
            BlockResult with action="move_to_edge", params={"edge_id", "direction", "geom"}
        """
        if not candidate_edges:
            return BlockResult(action="stay", params={}, reasoning="No candidate edges available", fallback=True)

        # Read relevant memory
        position = await self.memory.status.get("position", {})
        needs = await self.memory.status.get("needs", {})
        cognition = await self.memory.status.get("cognition_state", {})
        profile = await self.memory.status.get("agent_profile", {})
        archetype = profile.get("archetype", "resident")

        # Get recent movement history for context
        recent_moves = await self.memory.stream.get_recent("mobility", n=5)
        history_text = self.memory.stream.format_for_prompt(recent_moves)

        # Prepare candidates for prompt (limit to 8 to keep prompt short)
        prompt_candidates = candidate_edges[:8]
        prompt_cands = [
            {
                "edge_id": c["edge_id"],
                "direction": c.get("direction", "forward"),
                "amenities": [a.get("type", "") for a in c.get("amenities", [])],
                "description": c.get("description", ""),
            }
            for c in prompt_cands
        ]

        messages = mobility_decision_prompt(
            archetype=archetype,
            needs=needs,
            cognition=cognition,
            recent_history=history_text,
            current_position=position,
            candidates=prompt_cands,
        )

        response = await self.llm.chat_json(messages)
        chosen_idx = response.get("choice")
        reasoning = response.get("reasoning", "")

        fallback = False
        if chosen_idx is None or not isinstance(chosen_idx, int) or chosen_idx >= len(prompt_cands):
            # Fallback: prefer least-visited edge
            visit_counts = await self.memory.status.get("visited_edges", {})
            candidate_edges_sorted = sorted(
                candidate_edges,
                key=lambda e: visit_counts.get(str(e["edge_id"]), 0)
            )
            chosen = candidate_edges_sorted[0]
            reasoning = "LLM fallback: least-visited edge"
            fallback = True
        else:
            chosen = candidate_edges[chosen_idx]

        chosen_edge_id = chosen["edge_id"]

        # Update visited edges count in memory
        visit_counts = await self.memory.status.get("visited_edges", {})
        visit_counts[str(chosen_edge_id)] = visit_counts.get(str(chosen_edge_id), 0) + 1
        await self.memory.status.update("visited_edges", visit_counts)

        # Update current plan
        await self.memory.status.update("current_plan", {
            "goal": "move",
            "target_edge_id": chosen_edge_id,
        })

        # Log to stream
        amenity_names = [a.get("type", "?") for a in chosen.get("amenities", [])[:3]]
        await self.memory.stream.add(
            topic="mobility",
            step=step,
            description=f"Moved to edge {chosen_edge_id} ({chosen.get('direction','fwd')}). "
                        f"Nearby: {', '.join(amenity_names) if amenity_names else 'none'}. "
                        f"Reason: {reasoning}",
            metadata={"edge_id": chosen_edge_id, "fallback": fallback},
        )

        return BlockResult(
            action="move_to_edge",
            params={
                "edge_id": chosen["edge_id"],
                "direction": chosen.get("direction", "forward"),
                "geom": chosen.get("geom"),
            },
            reasoning=reasoning,
            fallback=fallback,
        )
