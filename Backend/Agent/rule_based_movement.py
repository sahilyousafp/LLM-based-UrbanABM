"""
Rule-based movement module for Urban ABM.

This module provides deterministic, rule-based movement logic for agents
when LLM inference is disabled or when using the 'rule_based' perception mode.

Movement Strategy:
- Agents select the least-visited connected edge
- Agents move along edges at a constant speed
- No LLM calls are made in this mode
"""

from shapely.geometry import Point, LineString
from typing import Optional, Tuple, List


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
    
    # Filter out current edge (prevent immediate U-turn)
    candidates = [
        (eid, geom, direction) 
        for eid, geom, direction in connected_edges 
        if eid != agent.current_edge_id
    ]
    
    # If no candidates, allow U-turn
    if not candidates:
        candidates = connected_edges
    
    if not candidates:
        return None
    
    # Sort by global visit count (least visited first)
    candidates.sort(
        key=lambda e: agent.model.edge_visit_count_global.get(e[0], 0)
    )
    
    # Select least-visited edge
    edge_id, edge_geom, direction = candidates[0]
    
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
        
        # Select next edge using rule-based strategy
        result = select_next_edge(agent, connected_edges)
        
        if result:
            edge_id, edge_geom, direction = result
            
            # Update edge visit count
            agent.model.edge_visit_count_global[edge_id] = (
                agent.model.edge_visit_count_global.get(edge_id, 0) + 1
            )
            
            # Update agent state
            agent.previous_edge_id = agent.current_edge_id
            agent.current_edge_id = edge_id
            agent.current_edge_geom = edge_geom
            agent.position_along_edge = 0.0
            
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
                    is_rule_based=True
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
    
    # Increment model step counter
    model.steps += 1
