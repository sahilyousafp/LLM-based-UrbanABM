"""
MobilityBlock — LLM-driven movement with guaranteed destination enforcement.

Decision pipeline:
  1. Read agent memory (position, needs, cognition, explore_steps counter)
  2. Compute Dijkstra shortest path to target node
  3. Check exploration budget: if free steps exhausted, FORCE Dijkstra (no LLM)
  4. Otherwise let LLM choose freely — destination still shown as primary goal
  5. Fallback to Dijkstra (then least-visited) on LLM failure
  6. Log decision to stream memory

Exploration budgets (free steps before one forced Dijkstra step):
  commuter: 0  — always Dijkstra
  resident: 1  — F→D→F→D
  student:  2  — FF→D→FF→D
  tourist:  3  — FFF→D→FFF→D
"""
import logging
from typing import Any

from LLM.Thinking.block import Block, BlockResult
from LLM.Thinking.prompts import mobility_decision_prompt

logger = logging.getLogger(__name__)

# Free LLM-choice steps allowed before one forced Dijkstra step toward destination
_EXPLORE_BUDGET = {"commuter": 0, "resident": 1, "student": 2, "tourist": 3}


class MobilityBlock(Block):
    """Selects the next edge/destination using LLM reasoning with hard Dijkstra enforcement."""

    async def run(self, step: int, candidate_edges: list[dict], **kwargs) -> BlockResult:
        if not candidate_edges:
            return BlockResult(action="stay", params={}, reasoning="No candidate edges available", fallback=True)

        street_perception = kwargs.get("street_perception")
        nearby_agents = kwargs.get("nearby_agents")

        position = await self.memory.status.get("position", {})
        needs = await self.memory.status.get("needs", {})
        cognition = await self.memory.status.get("cognition_state", {})
        profile = await self.memory.status.get("agent_profile", {})
        archetype = profile.get("archetype", "resident")
        preferences = profile.get("preferences", [])
        destination = await self.memory.status.get("destination", {})

        explore_budget = _EXPLORE_BUDGET.get(archetype, 1)
        explore_steps = await self.memory.status.get("explore_steps", 0)
        model = self.context.get("model")
        target_node = destination.get("target_node") if destination else None
        current_node = position.get("current_node")

        # --- User-configured destination: cap budget regardless of distance ---
        # A tourist who has explicitly entered a destination (like typing an address
        # into Google Maps) navigates with intent — budget=3 causes a diverging random
        # walk that never converges. Cap at 1 so the F→D cycle makes net progress.
        # Plan-assigned destinations keep the full archetype budget (free exploration).
        if destination and destination.get("source") == "user_configured" and not destination.get("visited"):
            explore_budget = min(explore_budget, 1)

        # --- Distance-based budget reduction ---
        # Shrink further as the agent closes in: pure Dijkstra in the final approach.
        if destination and not destination.get("visited") and destination.get("lon"):
            import math as _math
            _default = {"tourist": 60, "resident": 40, "student": 50}.get(archetype, 50)
            _ALMOST_THERE = getattr(model, "nav_compass_dist", _default)
            dlon = (destination["lon"] - position.get("lon", 0)) * 111320 * _math.cos(_math.radians(position.get("lat", 0)))
            dlat = (destination["lat"] - position.get("lat", 0)) * 110540
            dist_to_dest = _math.sqrt(dlon ** 2 + dlat ** 2)
            if dist_to_dest <= _ALMOST_THERE:
                explore_budget = 0          # pure Dijkstra in final approach

        # --- Dijkstra path computation ---
        dijkstra_edge_id = None
        dijkstra_edge_direction = None
        dijkstra_edge_data = None
        steps_to_destination = None

        if target_node and model and current_node:
            # Check arrival
            if current_node == target_node:
                destination["target_node"] = None
                destination["visited"] = True   # suppresses dist_m anchoring in prompt
                await self.memory.status.update("destination", destination)
                await self.memory.status.update("explore_steps", 0)
                await self.memory.stream.add(
                    topic="mobility", step=step,
                    description="Reached destination — stopping.",
                )
                return BlockResult(action="stay", params={}, reasoning="Reached destination")

            steps_to_destination = model.dijkstra_hops(current_node, target_node)
            next_node = model.dijkstra_next_node(current_node, target_node)
            if next_node:
                # Search ALL edges from the current node — not just candidate_edges.
                # candidate_edges has the previous edge removed (anti-backtrack filter),
                # but the Dijkstra-optimal step may require backtracking (network topology,
                # dead-end stubs, one-way connectors). If we only search candidate_edges,
                # dijkstra_edge_data stays None → force_dijkstra becomes False →
                # commuters (budget=0) and budget-exhausted steps silently free-explore.
                all_node_edges = model.node_to_edges.get(current_node, [])
                for entry in all_node_edges:
                    eid, geom, direction = entry[0], entry[1], entry[2]
                    end = geom.coords[-1] if direction == "forward" else geom.coords[0]
                    if (round(end[0], 6), round(end[1], 6)) == next_node:
                        dijkstra_edge_id = eid
                        dijkstra_edge_direction = direction
                        # Prefer the richer candidate dict (has amenities/perception)
                        for c in candidate_edges:
                            if c["edge_id"] == eid:
                                dijkstra_edge_data = c
                                break
                        else:
                            # Edge exists in network but was filtered from candidates.
                            # Build a minimal dict so forced Dijkstra steps can use it.
                            dijkstra_edge_data = {
                                "edge_id": eid,
                                "geom": geom,
                                "direction": direction,
                                "amenities": [],
                                "perception": None,
                                "description": f"{direction} edge",
                            }
                        break

        # --- Exploration budget enforcement ---
        # Force Dijkstra when budget is exhausted; otherwise let LLM choose freely.
        # This guarantees the agent always makes periodic progress toward its destination.
        force_dijkstra = (explore_steps >= explore_budget) and dijkstra_edge_data is not None

        if force_dijkstra:
            chosen = dijkstra_edge_data
            reasoning = f"Forced destination step after {explore_steps} free exploration step(s)"
            await self.memory.status.update("explore_steps", 0)

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
                metadata={
                    "edge_id": chosen_edge_id,
                    "fallback": False,
                    "on_path": True,
                    "perception_available": street_perception is not None,
                    "data_sources": "[forced-dijkstra]",
                },
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

        # --- LLM-driven free exploration step ---
        free_steps_remaining = explore_budget - explore_steps - 1  # after this step
        await self.memory.status.update("explore_steps", explore_steps + 1)

        recent_moves = await self.memory.stream.get_recent("mobility", n=5)
        history_text = self.memory.stream.format_for_prompt(recent_moves)

        # Read plan state for context
        plan = await self.memory.status.get("plan", {})
        current_phase = plan.get("current_phase")
        perc_mode = getattr(
            self.context.get("model") if self.context else None,
            "perception_mode", "both"
        )
        nav_mode = getattr(
            self.context.get("model") if self.context else None,
            "nav_mode", "both"
        )
        plan_context = None
        if current_phase:
            plan_context = {
                "goal": current_phase.get("goal", ""),
                "time_of_day": current_phase.get("time_of_day", ""),
                "active_target": current_phase.get("active_target"),
                "perception_preferences": (
                    current_phase.get("perception_preferences", [])
                    if perc_mode in ("perception", "both") else []
                ),
                "perception_avoid": (
                    current_phase.get("perception_avoid", [])
                    if perc_mode in ("perception", "both") else []
                ),
            }

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

        visit_counts = await self.memory.status.get("visited_edges", {})

        messages = mobility_decision_prompt(
            archetype=archetype,
            needs=needs,
            cognition=cognition,
            recent_history=history_text,
            current_position=position,
            candidates=prompt_cands,
            street_perception=street_perception,
            destination=destination,
            path_hint_edge_id=None,           # suppress GPS label on free steps — compass direction used instead
            path_hint_direction=dijkstra_edge_direction,
            next_waypoint={"lon": next_node[0], "lat": next_node[1]} if next_node else None,
            preferences=preferences,
            explore_budget=explore_budget,
            free_steps_remaining=free_steps_remaining,
            plan_context=plan_context,
            visited_counts=visit_counts,
            steps_to_destination=steps_to_destination,
            nav_mode=nav_mode,
            nearby_agents=nearby_agents,
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
        perception_tag = "[perception+amenity]" if street_perception and amenity_names else (
            "[perception]" if street_perception else "[amenity]" if amenity_names else "[no-data]"
        )
        await self.memory.stream.add(
            topic="mobility", step=step,
            description=f"Moved to edge {chosen_edge_id} ({chosen.get('direction','fwd')}). "
                        f"Nearby: {', '.join(amenity_names) if amenity_names else 'none'}. "
                        f"Reason: {reasoning}",
            metadata={
                "edge_id": chosen_edge_id,
                "fallback": fallback,
                "on_path": is_on_path,
                "perception_available": street_perception is not None,
                "data_sources": perception_tag,
            },
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
