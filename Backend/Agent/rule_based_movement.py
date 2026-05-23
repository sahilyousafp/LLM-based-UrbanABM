"""
Rule-based movement module for Urban ABM.

This module provides deterministic, rule-based movement logic for agents
when LLM inference is disabled or when using the 'rule_based' perception mode.

Movement Strategy:
- Agents select the least-visited connected edge
- Agents move along edges at a constant speed
- No LLM calls are made in this mode
"""

import random
import logging
from shapely.geometry import Point, LineString
from typing import Optional, Tuple, List

logger = logging.getLogger(__name__)


def _filter_by_plan_avoid(
    agent, edges: List[Tuple[str, LineString, str]]
) -> List[Tuple[str, LineString, str]]:
    """
    Filter connected edges based on plan's perception_avoid keywords.
    Hard filter: remove edges whose perception matches any avoid keyword.
    If all edges filtered out, return originals (forced deviation).
    """
    plan = agent.memory.status._data.get("plan", {})
    current_phase = plan.get("current_phase")
    if not current_phase:
        return edges
    avoid_list = current_phase.get("perception_avoid", [])
    if not avoid_list:
        return edges

    filtered = []
    for entry in edges:
        edge_id, geom, direction = entry
        midpoint = Point(geom.coords[len(geom.coords) // 2])
        perception = agent.model.get_nearby_perception(midpoint)
        if not perception:
            filtered.append(entry)
            continue
        # Concatenate all perception text fields for substring matching
        perception_text = " ".join(str(v) for v in perception.values())
        has_avoided = any(
            ak.lower() in perception_text.lower() for ak in avoid_list
        )
        if not has_avoided:
            filtered.append(entry)

    if not filtered:
        # Forced deviation — no valid edges, use original candidates
        current_phase["forced_deviation"] = True
        current_phase["deviation_reason"] = (
            "All edges filtered by perception_avoid — forced deviation"
        )
        return edges

    if len(filtered) < len(edges):
        current_phase["forced_deviation"] = False
        current_phase["deviation_reason"] = (
            f"Filtered {len(edges) - len(filtered)} edge(s) with avoided qualities"
        )

    return filtered


def select_next_edge(
    agent,
    connected_edges: List[Tuple[str, LineString, str]]
) -> Optional[Tuple[str, LineString, str]]:
    """
    Select the next edge for agent movement using least-visited strategy.
    
    This is a deterministic rule-based algorithm that:
    1. Filters out the current edge (no U-turns)
    2. Sorts remaining edges by global visit count
    3. Returns the least-visited edge
    
    Parameters
    ----------
    agent : CityAgent
        The agent selecting the next edge
    connected_edges : List[Tuple[str, LineString, str]]
        List of (edge_id, edge_geometry, direction) tuples
        
    Returns
    -------
    Optional[Tuple[str, LineString, str]]
        Selected edge tuple or None if no edges available
    """
    if not connected_edges:
        return None
    
    # Filter out current edge and previous edge (prevent immediate U-turn and backtracking)
    candidates = [
        entry for entry in connected_edges
        if entry[0] != agent.current_edge_id and entry[0] != agent.previous_edge_id
    ]
    
    # If no candidates, allow U-turn
    if not candidates:
        candidates = connected_edges
    
    if not candidates:
        return None

    # Shortest-path navigation biased by archetype adherence
    destination = agent.memory.status._data.get("destination") or {}
    target_node = destination.get("target_node")
    archetype = agent.memory.status._data.get("agent_profile", {}).get("archetype", "resident")
    adherence = agent.model.ARCHETYPE_PATH_ADHERENCE.get(archetype, 0.5)

    if target_node is not None and random.random() < adherence:
        current_end = agent.current_edge_geom.coords[-1]
        current_node = (round(current_end[0], 6), round(current_end[1], 6))

        # Check if we've arrived at the target
        if current_node == target_node:
            # Clear target so we don't bounce back and forth forever
            destination["target_node"] = None
            agent.memory.status._data["destination"] = destination
        else:
            next_node = agent.model.dijkstra_next_node(current_node, target_node)
            if next_node:
                for e in candidates:
                    eid, geom, direction = e[0], e[1], e[2]
                    end = geom.coords[-1] if direction == "forward" else geom.coords[0]
                    if (round(end[0], 6), round(end[1], 6)) == next_node:
                        return eid, geom, direction

    # Fallback: least-visited edge
    candidates.sort(
        key=lambda e: agent.model.edge_visit_count_global.get(e[0], 0)
    )

    edge_id, edge_geom, direction = candidates[0][0], candidates[0][1], candidates[0][2]
    
    # Reverse geometry if moving in reverse direction
    if direction == "reverse":
        edge_geom = LineString(list(edge_geom.coords)[::-1])
    
    return edge_id, edge_geom, direction


def move_along_edge(agent, distance: float) -> None:
    """
    Move an agent along its current edge by a given distance.
    
    Updates the agent's position_along_edge parameter and geometry.
    Handles edge completion (position >= 1.0) by clamping to endpoint.
    
    Parameters
    ----------
    agent : CityAgent
        The agent to move
    distance : float
        Distance to move along the edge (0.0 to 1.0)
    """
    if agent.current_edge_geom is None:
        return
    
    # Update position along edge
    agent.position_along_edge = min(agent.position_along_edge + distance, 1.0)
    
    # Interpolate position on edge geometry
    coords = list(agent.current_edge_geom.coords)
    if len(coords) >= 2:
        # Determine which segment we're on
        idx = min(
            int(agent.position_along_edge * (len(coords) - 1)), 
            len(coords) - 2
        )
        # Interpolate within segment
        frac = (agent.position_along_edge * (len(coords) - 1)) - idx
        x1, y1 = coords[idx]
        x2, y2 = coords[idx + 1]
        agent.geometry = Point(
            x1 + (x2 - x1) * frac,
            y1 + (y2 - y1) * frac
        )


def make_decision(agent) -> None:
    """
    Complete rule-based movement decision for an agent.
    
    This function:
    1. Moves the agent along current edge
    2. Selects next edge if at intersection (position >= 1.0)
    3. Updates edge visit counts
    4. Logs movement to tracker (if available)
    
    No LLM calls are made. This is a fully deterministic movement system.
    
    Parameters
    ----------
    agent : CityAgent
        The agent making the movement decision
    """
    if agent.current_edge_geom is None:
        return
    
    # Move along current edge
    move_along_edge(agent, agent.move_speed)
    
    # Check if we've reached the end of the edge
    if agent.position_along_edge >= 1.0:
        # Get connected edges from endpoint
        end_point = Point(agent.current_edge_geom.coords[-1])
        connected_edges = agent.model.find_connected_edges(end_point)
        
        # Apply plan perception_avoid hard filter
        connected_edges = _filter_by_plan_avoid(agent, connected_edges)

        # Select next edge using rule-based strategy
        result = select_next_edge(agent, connected_edges)
        
        if result:
            edge_id, edge_geom, direction = result
            
            # Check if this edge is on the proposed Dijkstra path
            destination = agent.memory.status._data.get("destination") or {}
            target_node = destination.get("target_node")
            on_path = False
            if target_node:
                current_end = agent.current_edge_geom.coords[-1]
                current_node = (round(current_end[0], 6), round(current_end[1], 6))
                next_node = agent.model.dijkstra_next_node(current_node, target_node)
                if next_node:
                    end = edge_geom.coords[-1] if direction == "forward" else edge_geom.coords[0]
                    end_key = (round(end[0], 6), round(end[1], 6))
                    on_path = (end_key == next_node)

            # Update edge visit count
            agent.model.edge_visit_count_global[edge_id] = (
                agent.model.edge_visit_count_global.get(edge_id, 0) + 1
            )
            
            # Update agent state
            agent.previous_edge_id = agent.current_edge_id
            agent.current_edge_id = edge_id
            agent.current_edge_geom = edge_geom
            agent.position_along_edge = 0.0
            
            # Update geometry to the start of the new edge (prevents one-frame discontinuity)
            agent.geometry = Point(edge_geom.coords[0])

            # Update proposed path if deviated
            if not on_path and target_node:
                current_node = (round(agent.current_edge_geom.coords[-1][0], 6),
                                round(agent.current_edge_geom.coords[-1][1], 6))
                proposed_path = agent.model._compute_proposed_path(current_node, destination)
                agent.memory.status._data["proposed_path"] = proposed_path
            
            # Log to tracker
            if hasattr(agent.model, 'tracker') and agent.model.tracker:
                agent.model.tracker.log_movement(
                    agent_id=agent.unique_id,
                    step_number=agent.model.steps,
                    longitude=agent.geometry.x,
                    latitude=agent.geometry.y,
                    edge_id=edge_id,
                    position_along_edge=0.0,
                    speed=agent.move_speed,
                )
        else:
            # No connected edges - stay at endpoint
            agent.position_along_edge = 1.0


def step_all_agents(model) -> None:
    """
    Execute rule-based movement for all agents in the model.

    This is the main entry point for rule-based simulation steps.
    Call this instead of individual agent.step() when using rule_based mode.

    Parameters
    ----------
    model : CityModel
        The city model containing all agents
    """
    for agent in model.city_agents:
        make_decision(agent)
