"""
MobilityBlock — LLM-driven movement decision with hard path adherence enforcement.

Decision pipeline:
  1. Read agent memory (position, needs, cognition, recent stream)
  2. Compute Dijkstra shortest path to target node
  3. Roll adherence dice: if succeeds, force the shortest-path edge (no LLM)
  4. If roll fails, let LLM choose freely — destination still shown as primary goal
  5. Fallback to Dijkstra (then least-visited) on LLM failure
  6. Log decision to stream memory
"""
import random
import logging
from typing import Any

from LLM.Thinking.block import Block, BlockResult
from LLM.Thinking.prompts import mobility_decision_prompt

logger = logging.getLogger(__name__)

_ADHERENCE = {"commuter": 1.0, "resident": 0.8, "student": 0.5, "tourist": 0.35}


class MobilityBlock(Block):
    """Selects the next edge/destination using LLM reasoning with hard Dijkstra enforcement."""

    async def run(self, step: int, candidate_edges: list[dict], **kwargs) -> BlockResult:
        if not candidate_edges:
            return BlockResult(action="stay", params={}, reasoning="No candidate edges available", fallback=True)

        street_perception = kwargs.get("street_perception")

        position = await self.memory.status.get("position", {})
        needs = await self.memory.status.get("needs", {})
        cognition = await self.memory.status.get("cognition_state", {})
        profile = await self.memory.status.get("agent_profile", {})
        archetype = profile.get("archetype", "resident")
        preferences = profile.get("preferences", [])
        destination = await self.memory.status.get("destination", {})

        adherence = _ADHERENCE.get(archetype, 0.5)
        model = self.context.get("model")
        target_node = destination.get("target_node") if destination else None
        current_node = position.get("current_node")

        # --- Dijkstra path computation ---
        dijkstra_edge_id = None
        dijkstra_edge_data = None

        if target_node and model and current_node:
            # Check arrival
            if current_node == target_node:
                destination["target_node"] = None
                await self.memory.status.update("destination", destination)
                await self.memory.stream.add(
                    topic="mobility", step=step,
                    description="Reached destination — stopping.",
                )
                return BlockResult(action="stay", params={}, reasoning="Reached destination")

            next_node = model.dijkstra_next_node(current_node, target_node)
            if next_node:
                for c in candidate_edges:
                    geom = c.get("geom")
                    if geom:
                        direction = c.get("direction", "forward")
                        end = geom.coords[-1] if direction == "forward" else geom.coords[0]
                        if (round(end[0], 6), round(end[1], 6)) == next_node:
                            dijkstra_edge_id = c["edge_id"]
                            dijkstra_edge_data = c
                            break

        # --- Hard adherence enforcement ---
        adherence_roll = random.random()
        should_follow_path = adherence_roll < adherence

        if should_follow_path and dijkstra_edge_data is not None:
            chosen = dijkstra_edge_data
            reasoning = f"Path adherence roll {adherence_roll:.2f} < {adherence:.1f} — following shortest path"

            chosen_edge_id = chosen["edge_id"]
            visit_counts = await self.memory.status.get("visited_edges", {})
            visit_counts[str(chosen_edge_id)] = visit_counts.get(str(chosen_edge_id), 0) + 1
            await self.memory.status.update("visited_edges", visit_counts)

            await self.memory.status.update("current_plan", {
                "goal": "move",
                "target_edge_id": chosen_edge_id,
                "on_proposed_path": True,
            })

            amenity_names = [a.get("type", "?") for a in chosen.get("amenities", [])[:3]]
            await self.memory.stream.add(
                topic="mobility", step=step,
                description=f"Moved to edge {chosen_edge_id} ({chosen.get('direction','fwd')}). "
                            f"Nearby: {', '.join(amenity_names) if amenity_names else 'none'}. "
                            f"Reason: {reasoning}",
                metadata={"edge_id": chosen_edge_id, "fallback": False, "on_path": True},
            )

            return BlockResult(
                action="move_to_edge",
                params={
                    "edge_id": chosen["edge_id"],
                    "direction": chosen.get("direction", "forward"),
                    "geom": chosen.get("geom"),
                    "on_proposed_path": True,
                },
                reasoning=reasoning,
                fallback=False,
            )

        # --- LLM-driven decision (adherence roll failed — agent may take a detour) ---
        recent_moves = await self.memory.stream.get_recent("mobility", n=5)
        history_text = self.memory.stream.format_for_prompt(recent_moves)

        prompt_candidates = candidate_edges[:8]
        prompt_cands = [
            {
                "edge_id": c["edge_id"],
                "direction": c.get("direction", "forward"),
                "amenities": [a.get("type", "") for a in c.get("amenities", [])],
                "perception": c.get("perception", ""),
                "description": c.get("description", ""),
            }
            for c in prompt_candidates
        ]

        messages = mobility_decision_prompt(
            archetype=archetype,
            needs=needs,
            cognition=cognition,
            recent_history=history_text,
            current_position=position,
            candidates=prompt_cands,
            street_perception=street_perception,
            destination=destination,
            path_hint_edge_id=dijkstra_edge_id,
            preferences=preferences,
            adherence=adherence,
            adherence_failed=True,
        )

        response = await self.llm.chat_json(messages)
        chosen_idx = response.get("choice")
        reasoning = response.get("reasoning", "")

        fallback = False
        if chosen_idx is None or not isinstance(chosen_idx, int) or chosen_idx >= len(prompt_cands):
            if dijkstra_edge_data is not None:
                chosen = dijkstra_edge_data
                reasoning = "LLM fallback: following Dijkstra toward destination"
            else:
                visit_counts = await self.memory.status.get("visited_edges", {})
                candidate_edges_sorted = sorted(
                    candidate_edges,
                    key=lambda e: visit_counts.get(str(e["edge_id"]), 0)
                )
                chosen = candidate_edges_sorted[0]
                reasoning = "LLM fallback: least-visited edge (no destination set)"
            fallback = True
        else:
            chosen = candidate_edges[chosen_idx]

        chosen_edge_id = chosen["edge_id"]
        is_on_path = (chosen_edge_id == dijkstra_edge_id)

        visit_counts = await self.memory.status.get("visited_edges", {})
        visit_counts[str(chosen_edge_id)] = visit_counts.get(str(chosen_edge_id), 0) + 1
        await self.memory.status.update("visited_edges", visit_counts)

        await self.memory.status.update("current_plan", {
            "goal": "move",
            "target_edge_id": chosen_edge_id,
            "on_proposed_path": is_on_path,
        })

        amenity_names = [a.get("type", "?") for a in chosen.get("amenities", [])[:3]]
        await self.memory.stream.add(
            topic="mobility", step=step,
            description=f"Moved to edge {chosen_edge_id} ({chosen.get('direction','fwd')}). "
                        f"Nearby: {', '.join(amenity_names) if amenity_names else 'none'}. "
                        f"Reason: {reasoning}",
            metadata={"edge_id": chosen_edge_id, "fallback": fallback, "on_path": is_on_path},
        )

        return BlockResult(
            action="move_to_edge",
            params={
                "edge_id": chosen["edge_id"],
                "direction": chosen.get("direction", "forward"),
                "geom": chosen.get("geom"),
                "on_proposed_path": is_on_path,
            },
            reasoning=reasoning,
            fallback=fallback,
        )
