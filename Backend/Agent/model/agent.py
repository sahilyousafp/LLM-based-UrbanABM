import asyncio
import random
import sys
from collections import deque
from pathlib import Path

import mesa_geo as mg
from shapely.geometry import Point, LineString

# Ensure Backend root is on sys.path for LLM.* imports
_BACKEND_ROOT = Path(__file__).parent.parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from LLM.Memory.memory import Memory
from LLM.Memory.stream_memory import MemoryNode
from LLM.Thinking.dispatcher import BlockDispatcher
from LLM.Thinking.blocks.plan_block import load_plans_json


class CityAgent(mg.GeoAgent):
    """A pedestrian agent with LLM-driven movement decisions."""

    ARCHETYPES = ["resident", "commuter", "tourist", "student"]

    def __init__(self, model, geometry, crs="EPSG:4326", edge_id=None, edge_geom=None,
                 archetype="resident", target_info=None, home_info=None,
                 gender="unknown", age=None):
        super().__init__(model=model, geometry=geometry, crs=crs)
        self.agent_type = "CityAgent"
        self.nearby_amenities = []
        self.street_perception = None

        self.current_edge_id = edge_id
        self.current_edge_geom = edge_geom
        self.previous_edge_id = None
        self.position_along_edge = 0.0
        self.move_speed = random.uniform(0.10, 0.20)

        self.memory = Memory(agent_id=self.unique_id)
        ctx = {"model": model, "current_weather": getattr(model, "current_weather", {})}
        plans_path = getattr(model, "plans_path", None)
        if plans_path:
            ctx["plans_path"] = plans_path
        self.dispatcher = BlockDispatcher(
            llm_client=model.llm_client,
            memory=self.memory,
            context=ctx,
        )

        self._init_memory_sync(edge_id, geometry, archetype, target_info, home_info, gender, age)

        self.memory.stream._store["cognition"] = deque([
            MemoryNode(
                topic="cognition",
                step=model.steps,
                description=f"Initialised as {archetype}. Current goal: {target_info['name'] if target_info else 'none'}"
            )
        ], maxlen=self.memory.stream._max)

        if hasattr(model, 'tracker') and model.tracker:
            model.tracker.log_movement(
                agent_id=self.unique_id,
                step_number=model.steps,
                longitude=geometry.x,
                latitude=geometry.y,
                edge_id=edge_id,
                position_along_edge=self.position_along_edge,
                speed=self.move_speed
            )

    def _init_memory_sync(self, edge_id, geometry, archetype: str,
                          target_info=None, home_info=None,
                          gender="unknown", age=None) -> None:
        self.memory.status._data["agent_profile"] = {
            "archetype": archetype,
            "age": age if age is not None else random.randint(18, 70),
            "gender": gender,
            "preferences": self._archetype_preferences(archetype),
        }
        current_node = None
        if hasattr(self, 'current_edge_geom') and self.current_edge_geom is not None:
            end_coords = self.current_edge_geom.coords[-1]
            current_node = (round(end_coords[0], 6), round(end_coords[1], 6))
        self.memory.status._data["position"] = {
            "lon": geometry.x,
            "lat": geometry.y,
            "edge_id": edge_id,
            "current_node": current_node,
        }
        if edge_id is not None:
            self.memory.status._data["visited_edges"] = {str(edge_id): 1}
        destination_entry = target_info or {
            "name": None, "amenity_type": None,
            "lon": None, "lat": None, "target_node": None,
        }
        destination_entry["start_lon"] = geometry.x
        destination_entry["start_lat"] = geometry.y
        self.memory.status._data["destination"] = destination_entry
        self.memory.status._data["home"] = home_info or {
            "name": None, "amenity_type": None,
            "lon": None, "lat": None, "target_node": None,
        }
        # Store initial activity target (work/campus) so daily plans can re-route
        if archetype != "resident" and target_info and target_info.get("name") != "home":
            self.memory.status._data["work"] = {
                "name": target_info.get("name"),
                "amenity_type": target_info.get("amenity_type"),
                "lon": target_info.get("lon"),
                "lat": target_info.get("lat"),
                "target_node": target_info.get("target_node"),
            }
        else:
            self.memory.status._data["work"] = {
                "name": None, "amenity_type": None,
                "lon": None, "lat": None, "target_node": None,
            }
        proposed_path = self.model._compute_proposed_path(current_node, target_info)
        self.memory.status._data["proposed_path"] = proposed_path
        self.memory.status._data["needs"] = {
            "hunger": 0.0,
            "energy": 1.0,
            "social": 0.2,
            "comfort": 0.7,
        }
        self.memory.status._data["cognition_state"] = {
            "mood": "neutral",
            "curiosity": 0.7,
            "fatigue": 0.0,
        }
        self.memory.status._data["explore_steps"] = 0
        self.memory.status._data["plan"] = self._init_plan(archetype)

    @staticmethod
    def _archetype_preferences(archetype: str) -> list:
        prefs = {
            "resident": ["supermarket", "pharmacy", "park", "home_area"],
            "commuter": ["direct_route", "transport", "cafe", "efficiency"],
            "tourist": ["attraction", "cafe", "restaurant", "new_streets", "views"],
            "student": ["cafe", "library", "park", "social", "cheap_food"],
        }
        return prefs.get(archetype, [])

    def _init_plan(self, archetype: str) -> dict:
        plans_path = getattr(self.model, "plans_path", None)
        plans_config = load_plans_json(plans_path)
        archetype_plan = plans_config.get(archetype, {})
        phases = archetype_plan.get("phases", [])
        current_phase = phases[0] if phases else None
        return {
            "phases": phases,
            "current_phase_index": 0,
            "current_phase": current_phase,
            "completed_phases": [],
            "target_override": None,
            "encountered_qualities": [],
            "phase_start_steps": {current_phase["id"]: 0} if current_phase else {},
            "status": "active" if phases else "completed",
        }

    def step(self):
        perception_mode = getattr(self.model, 'perception_mode', 'both')
        if perception_mode == 'rule_based':
            try:
                from rule_based_movement import make_decision as rule_based_move
                rule_based_move(self)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Agent {self.unique_id} rule-based step error: {e}")
                self._simple_move()
            return

        try:
            snapshot = getattr(self.model, '_step_snapshot', None)
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(asyncio.run, self._async_step(snapshot))
                    future.result(timeout=30)
            else:
                loop.run_until_complete(self._async_step(snapshot))
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Agent {self.unique_id} step error: {e}")
            self._simple_move()

    def get_archetype(self) -> str:
        return self.memory.status._data.get("agent_profile", {}).get("archetype", "resident")

    async def _async_step(self, agent_snapshot: list | None = None) -> None:
        perception_mode = getattr(self.model, 'perception_mode', 'both')

        if perception_mode in ['amenities', 'both']:
            self.nearby_amenities = self.model.get_nearby_amenities(self.geometry)
        else:
            self.nearby_amenities = []

        if perception_mode in ['perception', 'both']:
            self.street_perception = self.model.get_nearby_perception(self.geometry)
        else:
            self.street_perception = None

        if self.model.steps % 10 == 0 and self.unique_id == 0:
            amenity_count = len(self.nearby_amenities) if self.nearby_amenities else 0
            has_perception = self.street_perception is not None
            print(f"[DEBUG] Step {self.model.steps} | Mode: {perception_mode} | Amenities: {amenity_count} | Perception: {has_perception}")

        current_node = None
        if self.current_edge_geom is not None:
            cs = list(self.current_edge_geom.coords)
            if self.position_along_edge >= 1.0:
                ex, ey = cs[-1][0], cs[-1][1]
                current_node = (round(ex, 6), round(ey, 6))
            elif self.position_along_edge <= 0.0:
                sx, sy = cs[0][0], cs[0][1]
                current_node = (round(sx, 6), round(sy, 6))
            else:
                sx, sy = cs[0][0], cs[0][1]
                ex, ey = cs[-1][0], cs[-1][1]
                dsx = sx - self.geometry.x
                dsy = sy - self.geometry.y
                dex = ex - self.geometry.x
                dey = ey - self.geometry.y
                current_node = (round(sx, 6), round(sy, 6)) if (dsx*dsx + dsy*dsy) < (dex*dex + dey*dey) else (round(ex, 6), round(ey, 6))

        await self.memory.status.update("position", {
            "lon": self.geometry.x,
            "lat": self.geometry.y,
            "edge_id": self.current_edge_id,
            "current_node": current_node,
        })

        needs_new_edge = (
            self.current_edge_id is None or
            self.position_along_edge >= 1.0
        )

        candidate_edges = self._get_candidate_edges()

        nearby_agents = self.model.get_nearby_agents(self.unique_id, self.geometry, agent_snapshot) if agent_snapshot else []
        nearby_transit = self.model.get_nearby_external_data(self.geometry, "ext_transit_stops", radius_deg=0.0007)

        result = await self.dispatcher.run(
            step=self.model.steps,
            candidate_edges=candidate_edges,
            nearby_amenities=self.nearby_amenities,
            street_perception=self.street_perception,
            needs_new_edge=needs_new_edge,
            nearby_agents=nearby_agents,
            nearby_transit=nearby_transit,
            time_of_day=self.model.time_of_day,
        )

        if needs_new_edge and result.mobility.action == "move_to_edge":
            self._apply_mobility(result.mobility.params)
            on_path = result.mobility.params.get("on_proposed_path", False)
            if not on_path and current_node:
                await self._update_proposed_path(current_node)
            if hasattr(self.model, 'tracker') and self.model.tracker:
                self.model.tracker.log_decision(
                    agent_id=self.unique_id,
                    step_number=self.model.steps,
                    decision_type="edge_change",
                    longitude=self.geometry.x,
                    latitude=self.geometry.y,
                    from_edge_id=self.previous_edge_id,
                    to_edge_id=self.current_edge_id,
                    alternatives_count=len(candidate_edges) if candidate_edges else 0,
                    decision_reason=result.mobility.reasoning,
                    is_fallback=result.mobility.fallback,
                )
        elif needs_new_edge:
            import logging as _log
            _log.getLogger(__name__).warning(
                f"Agent {self.unique_id} stuck at edge end "
                f"(action={result.mobility.action}, reason={result.mobility.reasoning}). "
                f"Falling back to sync edge selection."
            )
            self._select_next_edge_sync()

        self._advance_along_edge()

        if hasattr(self.model, 'tracker') and self.model.tracker:
            current_needs = await self.memory.status.get("needs", {})
            self.model.tracker.log_movement(
                agent_id=self.unique_id,
                step_number=self.model.steps,
                longitude=self.geometry.x,
                latitude=self.geometry.y,
                edge_id=self.current_edge_id,
                position_along_edge=self.position_along_edge,
                speed=self.move_speed,
                nearby_amenities_count=len(self.nearby_amenities),
                energy=current_needs.get("energy"),
                hunger=current_needs.get("hunger"),
                social=current_needs.get("social"),
                comfort=current_needs.get("comfort"),
            )

    async def _update_proposed_path(self, current_node) -> None:
        destination = await self.memory.status.get("destination", {})
        target_node = destination.get("target_node") if destination else None
        if not target_node or not current_node:
            return
        if isinstance(target_node, (list, tuple)):
            target_node = (round(float(target_node[0]), 6), round(float(target_node[1]), 6))
        if current_node == target_node:
            return
        proposed_path = self.model._compute_proposed_path(current_node, destination)
        await self.memory.status.update("proposed_path", proposed_path)

    def _get_candidate_edges(self) -> list[dict]:
        if self.current_edge_geom is None:
            return []
        end_point = Point(self.current_edge_geom.coords[-1])
        raw_edges = self.model.find_connected_edges(end_point)
        candidates = [
            entry for entry in raw_edges
            if entry[0] != self.current_edge_id and entry[0] != self.previous_edge_id
        ]
        if not candidates:
            candidates = [entry for entry in raw_edges if entry[0] != self.current_edge_id]
        if not candidates:
            candidates = raw_edges

        perception_mode = getattr(self.model, 'perception_mode', 'both')
        import math as _m

        result = []
        for entry in candidates:
            eid, geom, direction = entry[0], entry[1], entry[2]
            midpoint = Point(geom.coords[len(geom.coords) // 2])
            coords = list(geom.coords)
            if len(coords) >= 2:
                dlon = coords[-1][0] - coords[0][0]
                dlat = coords[-1][1] - coords[0][1]
                edge_bearing = (_m.degrees(_m.atan2(dlon, dlat)) + 360) % 360
                if direction == "reverse":
                    edge_bearing = (edge_bearing + 180) % 360
            else:
                edge_bearing = None

            if perception_mode in ['amenities', 'both']:
                amenity_types = [a.get("type", "") for a in self.model.get_nearby_amenities(midpoint)[:3]]
            else:
                amenity_types = []

            if perception_mode in ['perception', 'both']:
                perception = self.model.get_streetview_perception(midpoint, heading=edge_bearing)
            else:
                perception = None

            result.append({
                "edge_id": eid,
                "geom": geom,
                "direction": direction,
                "amenities": [{"type": t} for t in amenity_types],
                "perception": perception,
                "description": f"{direction} edge",
            })
        return result

    def _apply_mobility(self, params: dict) -> None:
        edge_id = params.get("edge_id")
        direction = params.get("direction", "forward")
        geom = params.get("geom")
        if geom is None:
            geom = self.model.edges.get(edge_id)
        if geom is None:
            return
        if direction == "reverse":
            geom = LineString(list(geom.coords)[::-1])
        self.previous_edge_id = self.current_edge_id
        self.current_edge_id = edge_id
        self.current_edge_geom = geom
        self.position_along_edge = 0.0

    def _advance_along_edge(self) -> None:
        if self.current_edge_geom is None:
            return
        if self.position_along_edge >= 1.0:
            return
        self.position_along_edge += self.move_speed
        if self.position_along_edge >= 1.0:
            self.position_along_edge = 1.0
        coords = list(self.current_edge_geom.coords)
        if len(coords) >= 2:
            idx = min(int(self.position_along_edge * (len(coords) - 1)), len(coords) - 2)
            frac = (self.position_along_edge * (len(coords) - 1)) - idx
            x1, y1 = coords[idx]
            x2, y2 = coords[idx + 1]
            self.geometry = Point(x1 + (x2 - x1) * frac, y1 + (y2 - y1) * frac)

    def _simple_move(self) -> None:
        if self.current_edge_geom is None:
            return
        self.position_along_edge += self.move_speed
        if self.position_along_edge >= 1.0:
            self._select_next_edge_sync()
        self._advance_along_edge()

    def _select_next_edge_sync(self) -> None:
        if self.current_edge_geom is None:
            return
        end_point = Point(self.current_edge_geom.coords[-1])
        next_edges = self.model.find_connected_edges(end_point)
        candidates = [
            entry for entry in next_edges
            if entry[0] != self.current_edge_id and entry[0] != self.previous_edge_id
        ]
        if not candidates:
            candidates = [entry for entry in next_edges if entry[0] != self.current_edge_id]
        if not candidates:
            candidates = next_edges
        if not candidates:
            return
        candidates.sort(key=lambda e: self.model.edge_visit_count_global.get(e[0], 0))
        eid, geom, direction = candidates[0][0], candidates[0][1], candidates[0][2]
        if direction == "reverse":
            geom = LineString(list(geom.coords)[::-1])
        self.previous_edge_id = self.current_edge_id
        self.current_edge_id = eid
        self.current_edge_geom = geom
        self.position_along_edge = 0.0

    def to_dict(self):
        return {
            "id": self.unique_id,
            "type": self.agent_type,
            "location": {
                "lon": self.geometry.x,
                "lat": self.geometry.y
            }
        }
