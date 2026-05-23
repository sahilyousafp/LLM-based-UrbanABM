import asyncio
import mesa
import mesa_geo as mg
import duckdb
from shapely import wkt
from shapely.geometry import Point, LineString
import random
import os
import sys
from collections import defaultdict, deque
from pathlib import Path
from agent_tracker import AgentTracker

# Add Backend root to path for cross-module imports
_BACKEND_ROOT = Path(__file__).parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from LLM.llm_config import LLMConfig
from LLM.llm_client import LLMClient
from LLM.Memory.memory import Memory
from LLM.Memory.stream_memory import MemoryNode
from LLM.Thinking.dispatcher import BlockDispatcher
from LLM.Thinking.blocks.plan_block import load_plans_json
from rule_based_movement import make_decision as rule_based_move

# Path to the DuckDB database (Overture Maps data)
PROJECT_ROOT = Path(__file__).parent.parent.parent
DB_PATH = PROJECT_ROOT / "Backend" / "Environment" / "eixample_overture.duckdb"

# Walkability weights based on Overture road_class — lower = more preferred for pedestrian routing
WALKABILITY_WEIGHT = {
    "pedestrian":    0.3,
    "footway":       0.5,
    "living_street": 0.6,
    "path":          0.7,
    "cycleway":      0.7,
    "steps":         0.85,
    "residential":   1.0,
    "tertiary":      1.1,
    "service":       1.1,
    "connector":     1.2,
}

class CityAgent(mg.GeoAgent):
    """
    A pedestrian agent with LLM-driven movement decisions.
    Memory stores agent state; BlockDispatcher coordinates reasoning blocks.
    """

    # Archetype pool — assigned round-robin during model init
    ARCHETYPES = ["resident", "commuter", "tourist", "student"]

    def __init__(self, model, geometry, crs="EPSG:4326", edge_id=None, edge_geom=None, archetype="resident", target_info=None):
        super().__init__(model=model, geometry=geometry, crs=crs)
        self.agent_type = "CityAgent"
        self.nearby_amenities = []
        self.street_perception = None

        # Network movement attributes
        self.current_edge_id = edge_id
        self.current_edge_geom = edge_geom
        self.previous_edge_id = None
        self.position_along_edge = 0.0
        self.move_speed = random.uniform(0.10, 0.20)

        # --- LLM + Memory + Thinking ---
        self.memory = Memory(agent_id=self.unique_id)
        ctx = {"model": model}
        plans_path = getattr(model, "plans_path", None)
        if plans_path:
            ctx["plans_path"] = plans_path
        self.dispatcher = BlockDispatcher(
            llm_client=model.llm_client,
            memory=self.memory,
            context=ctx,
        )

        # Initialise memory with agent profile (sync — safe at init time, no loop running)
        self._init_memory_sync(edge_id, geometry, archetype, target_info)

        # Add initial "spawned" event to stream so it's not empty at start
        self.memory.stream._store["cognition"] = deque([
            MemoryNode(
                topic="cognition",
                step=model.steps,
                description=f"Initialised as {archetype}. Current goal: {target_info['name'] if target_info else 'none'}"
            )
        ], maxlen=self.memory.stream._max)

        # Log initial position to tracker
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

    def _init_memory_sync(self, edge_id, geometry, archetype: str, target_info=None) -> None:
        """Initialise memory synchronously at agent creation (no asyncio loop needed)."""
        self.memory.status._data["agent_profile"] = {
            "archetype": archetype,
            "age": random.randint(18, 70),
            "preferences": self._archetype_preferences(archetype),
        }
        # Compute initial current_node (end of starting edge) for accurate pathfinding
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
        # Store spawn position so recorders can log start→target per row
        destination_entry["start_lon"] = geometry.x
        destination_entry["start_lat"] = geometry.y
        self.memory.status._data["destination"] = destination_entry
        # Compute and store the proposed path at spawn time
        proposed_path = self.model._compute_proposed_path(current_node, target_info)
        self.memory.status._data["proposed_path"] = proposed_path
        self.memory.status._data["needs"] = {
            "hunger": 0.5,
            "energy": 1.0,
            "social": 0.5,
            "comfort": 0.7,
        }
        self.memory.status._data["cognition_state"] = {
            "mood": "neutral",
            "curiosity": 0.7,
            "fatigue": 0.0,
        }
        self.memory.status._data["explore_steps"] = 0

        # Initialize plan from plans.json (used by both LLM and rule_based modes)
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
        """Initialize plan from plans.json config for given archetype.
        Auto-advances to first phase so rule_based mode has current_phase set."""
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
        """Execute agent step based on perception mode."""
        # Get perception mode from model
        perception_mode = getattr(self.model, 'perception_mode', 'both')
        
        # Rule-based mode: no LLM calls, deterministic movement
        if perception_mode == 'rule_based':
            try:
                rule_based_move(self)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Agent {self.unique_id} rule-based step error: {e}")
                self._simple_move()
            return
        
        # LLM-driven modes: use dispatcher with optional fallback
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Called from within an async context (e.g. FastAPI) — use new loop in thread
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(asyncio.run, self._async_step())
                    future.result(timeout=30)
            else:
                loop.run_until_complete(self._async_step())
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Agent {self.unique_id} step error: {e}")
            # Fallback to simple movement
            self._simple_move()

    async def _async_step(self) -> None:
        """Full async step: query amenities, run dispatcher, update geometry."""
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
            end_coords = self.current_edge_geom.coords[-1]
            current_node = (round(end_coords[0], 6), round(end_coords[1], 6))

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

        result = await self.dispatcher.run(
            step=self.model.steps,
            candidate_edges=candidate_edges,
            nearby_amenities=self.nearby_amenities,
            street_perception=self.street_perception,
            needs_new_edge=needs_new_edge,
        )

        if needs_new_edge and result.mobility.action == "move_to_edge":
            self._apply_mobility(result.mobility.params)
            # Update proposed path if agent deviated from it
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
        elif result.mobility.action == "stay":
            pass

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
        """Recompute the proposed path from current position to target."""
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
        """Get reachable edges from current position as dicts for the dispatcher."""
        if self.current_edge_geom is None:
            return []
        end_point = Point(self.current_edge_geom.coords[-1])
        raw_edges = self.model.find_connected_edges(end_point)
        # Exclude current edge and previous edge to prevent immediate reversal and backtracking
        candidates = [
            entry for entry in raw_edges
            if entry[0] != self.current_edge_id and entry[0] != self.previous_edge_id
        ]
        if not candidates:
            candidates = raw_edges  # Dead end — allow reversal

        # Get perception mode to determine what data to fetch
        perception_mode = getattr(self.model, 'perception_mode', 'both')

        result = []
        for entry in candidates:
            eid, geom, direction = entry[0], entry[1], entry[2]
            # Annotate each candidate with nearby amenity types (if mode allows)
            midpoint = Point(geom.coords[len(geom.coords) // 2])
            
            # Fetch amenities based on perception mode
            if perception_mode in ['amenities', 'both']:
                amenity_types = [a.get("type", "") for a in self.model.get_nearby_amenities(midpoint)[:3]]
            else:
                amenity_types = []
            
            # Fetch perception based on perception mode
            if perception_mode in ['perception', 'both']:
                perception = self.model.get_streetview_perception(midpoint)
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
        """Apply a mobility decision — switch to the chosen edge."""
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
        """Move agent forward along the current edge geometry."""
        if self.current_edge_geom is None:
            return
        
        # Don't move if we're already at the end waiting for a new edge
        if self.position_along_edge >= 1.0:
            return

        self.position_along_edge += self.move_speed
        
        # Cap at 1.0 so we stop at intersections until a new decision is made
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
        """Emergency fallback: rule-based movement (no LLM, no async)."""
        if self.current_edge_geom is None:
            return
        self.position_along_edge += self.move_speed
        if self.position_along_edge >= 1.0:
            self._select_next_edge_sync()
        self._advance_along_edge()

    def _select_next_edge_sync(self) -> None:
        """Synchronous fallback edge selection (least-visited)."""
        if self.current_edge_geom is None:
            return
        end_point = Point(self.current_edge_geom.coords[-1])
        next_edges = self.model.find_connected_edges(end_point)
        candidates = [
            entry for entry in next_edges
            if entry[0] != self.current_edge_id and entry[0] != self.previous_edge_id
        ]
        if not candidates:
            candidates = next_edges
        if not candidates:
            self.position_along_edge = 0.0
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

class CityModel(mesa.Model):
    """A model with LLM-driven agents moving through a city"""

    # Archetype → amenity types for DB target query (resident uses random node)
    ARCHETYPE_AMENITY_TYPES: dict = {
        "commuter": ["bus_station", "train_station", "office"],
        "tourist":  ["attraction", "museum", "cafe", "viewpoint"],
        "student":  ["library", "university", "cafe"],
    }

    # Probability of following Dijkstra at each intersection (rule-based mode).
    # Aligned with LLM-mode explore budgets: budget N → 1 forced per N+1 steps → prob 1/(N+1).
    ARCHETYPE_PATH_ADHERENCE: dict = {
        "commuter": 1.00,  # budget 0 → always
        "resident": 0.50,  # budget 1 → every 2nd step
        "student":  0.33,  # budget 2 → every 3rd step
        "tourist":  0.25,  # budget 3 → every 4th step
    }

    def __init__(self, num_agents=None, spawn_seed=None):
        super().__init__()
        # Use environment variable if num_agents not explicitly provided
        if num_agents is None:
            num_agents = int(os.getenv("NUM_AGENTS", 50))
        self.num_agents = num_agents
        self.steps = 0
        # Perception modes: 'amenities', 'perception', 'both', 'rule_based'
        # rule_based = no LLM calls, deterministic least-visited movement
        self.perception_mode = os.getenv("PERCEPTION_MODE", "both")
        self.llm_fallback = True  # Hardcoded: LLM fallback enabled for non-rule_based modes
        self.spawn_seed = spawn_seed
        
        # Set random seed for reproducibility if provided
        if spawn_seed is not None:
            random.seed(spawn_seed)
            print(f"[OK] Random seed set to: {spawn_seed} (reproducible spawning)")
        else:
            print(f"[INFO] No spawn seed set (random spawning each run)")

        # Use custom attribute name (Mesa 3.0+ reserves 'agents')
        self.city_agents = []
        self.edge_visit_count_global = {}  # Shared visit count for sync fallback

        # --- Recorder for GeoParquet export ---
        self._recorder = None

        # --- Shared LLM client (one per model, shared across all agents) ---
        llm_config = LLMConfig.from_env()
        self.llm_client = LLMClient(llm_config)
        print(f"[OK] LLM client: provider={llm_config.provider}, model={llm_config.model}")

        # Initialize agent tracker — store DB in Documentation/ (outside the source
        # tree) so DuckDB WAL writes don't trigger Live Server / VS Code refreshes.
        try:
            _tracker_db = PROJECT_ROOT / "Documentation" / "tracking_data" / "agent_tracking.duckdb"
            _tracker_db.parent.mkdir(parents=True, exist_ok=True)
            self.tracker = AgentTracker(db_path=_tracker_db)
            print(f"[OK] Agent tracker initialized: {self.tracker.db_path}")
        except Exception as e:
            print(f"[WARN] Failed to initialize agent tracker: {e}")
            self.tracker = None
        
        # Connect to DuckDB
        print(f"Connecting to DB at: {DB_PATH}")
        try:
            self.con = duckdb.connect(str(DB_PATH), read_only=True)
            self.con.install_extension("spatial")
            self.con.load_extension("spatial")
            print("[OK] Database connected")
        except Exception as e:
            print(f"[ERROR] Database connection failed: {e}")
            return
        
        # Load walk_edges network for pathfinding
        print("Loading walk_edges network...")
        # Penalty multipliers so Dijkstra prefers pedestrian paths over car roads.
        # Car roads exist in walk_edges only for network connectivity (added by network_connector).
        _ROAD_WEIGHT = {
            'footway': 1.0, 'pedestrian': 1.0, 'path': 1.0,
            'steps': 1.0, 'cycleway': 1.1, 'living_street': 1.2,
            'connector': 1.0,
            'residential': 2.0, 'service': 2.0,
            'tertiary': 3.0, 'unclassified': 2.0,
        }
        try:
            edges_df = self.con.execute("""
                SELECT
                    rowid as edge_id,
                    ST_AsText(geometry) as wkt,
                    ST_AsText(ST_StartPoint(geometry)) as start_wkt,
                    ST_AsText(ST_EndPoint(geometry)) as end_wkt,
                    road_class
                FROM walk_edges
            """).fetchdf()
            print(f"[OK] Loaded {len(edges_df)} walk edges")

            # Build BIDIRECTIONAL network graph for efficient lookup
            self.edges = {}
            self.node_to_edges = defaultdict(list)

            for _, row in edges_df.iterrows():
                edge_id = row['edge_id']
                edge_geom = wkt.loads(row['wkt'])
                start_point = wkt.loads(row['start_wkt'])
                end_point = wkt.loads(row['end_wkt'])
                road_class = row.get('road_class') or 'connector'
                weight_multiplier = _ROAD_WEIGHT.get(road_class, 2.0)

                self.edges[edge_id] = edge_geom

                # BIDIRECTIONAL: Map BOTH start and end points to edges
                # This allows agents to traverse edges in both directions
                start_key = (round(start_point.x, 6), round(start_point.y, 6))
                end_key = (round(end_point.x, 6), round(end_point.y, 6))

                # From start point: can take this edge in forward direction
                self.node_to_edges[start_key].append((edge_id, edge_geom, 'forward', weight_multiplier))
                # From end point: can take this edge in reverse direction
                self.node_to_edges[end_key].append((edge_id, edge_geom, 'reverse', weight_multiplier))

            print(f"[OK] Built BIDIRECTIONAL network graph with {len(self.node_to_edges)} nodes")

            # Find the largest connected component so _find_nearest_node only snaps to reachable nodes
            all_nodes = set(self.node_to_edges.keys())
            unvisited = set(all_nodes)
            best_component: set = set()
            while unvisited:
                start = next(iter(unvisited))
                component: set = set()
                stack = [start]
                while stack:
                    u = stack.pop()
                    if u in component:
                        continue
                    component.add(u)
                    unvisited.discard(u)
                    for entry in self.node_to_edges.get(u, []):
                        geom, direction = entry[1], entry[2]
                        v = (round(geom.coords[-1][0], 6), round(geom.coords[-1][1], 6)) if direction == 'forward' else (round(geom.coords[0][0], 6), round(geom.coords[0][1], 6))
                        if v not in component:
                            stack.append(v)
                if len(component) > len(best_component):
                    best_component = component
            self.main_component_nodes = best_component
            print(f"[OK] Main connected component: {len(self.main_component_nodes)}/{len(all_nodes)} nodes")

        except Exception as e:
            print(f"[ERROR] Failed to load walk edges: {e}")
            self.edges = {}
            self.node_to_edges = defaultdict(list)
            self.main_component_nodes = set()
        
        print(f"Spawning {num_agents} agents on network...")

        # Load street view scene analysis JSON cache for agent perception
        import json as _json_mod
        import re as _re_mod
        _sv_results = PROJECT_ROOT / "Backend" / "Environment" / "output" / "results"
        self._sv_cache = []
        if _sv_results.is_dir():
            for _jf in sorted(_sv_results.glob("*_analysis.json")):
                _m = _re_mod.match(r"^(-?\d+\.\d+)_(-?\d+\.\d+)_analysis\.json$", _jf.name)
                if not _m:
                    continue
                try:
                    _data = _json_mod.loads(_jf.read_text(encoding="utf-8"))
                    self._sv_cache.append({
                        "lat": float(_m.group(1)),
                        "lon": float(_m.group(2)),
                        "scene_analysis": _data.get("scene_analysis", {}),
                    })
                except Exception:
                    continue
            print(f"[OK] Loaded {len(self._sv_cache)} street view scene analysis points")
        else:
            print("[WARN] Street view results directory not found; scene perception disabled")

        edge_ids = list(self.edges.keys())

        if not edge_ids:
            print("[ERROR] No edges available for spawning!")
            return
        
        # Pick a single edge for all agents to start from
        common_edge_id = random.choice(edge_ids)
        common_edge_geom = self.edges[common_edge_id]
        common_start_point = Point(common_edge_geom.coords[0])
        
        node_keys = list(self.node_to_edges.keys())

        for i in range(num_agents):
            try:
                # Assign archetype round-robin
                archetype = CityAgent.ARCHETYPES[i % len(CityAgent.ARCHETYPES)]

                # Resolve target destination for this agent
                if archetype == "resident":
                    # Home = a random street intersection node
                    home_key = random.choice(node_keys)
                    target_info = {
                        "name": "home", "amenity_type": "residential",
                        "lon": home_key[0], "lat": home_key[1],
                        "target_node": home_key,
                    }
                else:
                    target_info = self._pick_target_for_archetype(archetype)
                    if target_info:
                        node = self._find_nearest_node(target_info["lon"], target_info["lat"])
                        target_info["target_node"] = node
                    elif node_keys:
                        fallback_key = random.choice(node_keys)
                        target_info = {
                            "name": "random_destination", "amenity_type": "unknown",
                            "lon": fallback_key[0], "lat": fallback_key[1],
                            "target_node": fallback_key,
                        }
                    else:
                        target_info = None

                if i == 0:
                    print(f"  Agent 0: lon={common_start_point.x:.6f}, lat={common_start_point.y:.6f} on edge {common_edge_id} [{archetype}] → target: {target_info}")

                # Create agent with edge information, archetype, and destination
                agent = CityAgent(
                    model=self,
                    geometry=common_start_point,
                    crs="EPSG:4326",
                    edge_id=common_edge_id,
                    edge_geom=common_edge_geom,
                    archetype=archetype,
                    target_info=target_info,
                )
                self.city_agents.append(agent)
                
            except Exception as e:
                print(f"  ERROR spawning agent {i}: {e}")
                import traceback
                traceback.print_exc()
        
        if self.city_agents:
            print(f"[OK] Total agents spawned: {len(self.city_agents)}")
        else:
            print("[WARN] No agents were spawned!")
            print("  Check the errors above for details")
    
    def find_connected_edges(self, point):
        """Find edges connected to the given point (bidirectional network)"""
        point_key = (round(point.x, 6), round(point.y, 6))
        return self.node_to_edges.get(point_key, [])
    
    def get_streetview_perception(self, point_geom) -> str:
        """
        Find the nearest street view scene analysis within ~150m.
        Returns a prose paragraph summarising the scene, or empty string if none nearby.
        """
        scene = self.get_nearby_perception(point_geom)
        if not scene:
            return ""
        field_labels = [
            ("scene_overview",      "Scene"),
            ("buildings",           "Buildings"),
            ("materials",           "Materials"),
            ("vegetation",          "Vegetation"),
            ("street_furniture",    "Street furniture"),
            ("signage",             "Signage"),
            ("ground_surfaces",     "Ground"),
            ("spatial_enclosure",   "Spatial enclosure"),
            ("pedestrian_activity", "Pedestrian activity"),
            ("lighting_atmosphere", "Lighting"),
            ("as_resident",         "For residents"),
            ("as_commuter",         "For commuters"),
            ("as_tourist",          "For tourists"),
            ("as_student",          "For students"),
        ]
        parts = []
        for key, label in field_labels:
            val = scene.get(key, "")
            if val and val.strip().lower() != "unknown":
                parts.append(f"{label}: {val}")
        return " | ".join(parts) if parts else ""

    def get_nearby_amenities(self, point_geom):
        """
        Query DuckDB for amenities within ~50m of the point.
        Database is in EPSG:4326 (WGS84), distance needs approximate conversion
        """
        try:
            # Use point directly (already in WGS84)
            # 0.0005 degrees ≈ 50m at Barcelona latitude
            buffer_deg = 0.001
            
            query = f"""
            SELECT name, amenity, ST_Distance(geometry, ST_GeomFromText('POINT ({point_geom.x} {point_geom.y})')) as dist_deg
            FROM amenities
            WHERE ST_DWithin(geometry, ST_GeomFromText('POINT ({point_geom.x} {point_geom.y})'), {buffer_deg})
            ORDER BY dist_deg
            LIMIT 20
            """
            results = self.con.execute(query).fetchall()
            # Convert degrees to meters (approximate: 1 degree ≈ 111km at this latitude)
            return [{"name": str(r[0]) if r[0] else "Unnamed", "type": r[1], "dist": r[2] * 111000} for r in results]
        except Exception as e:
            print(f"Query Error: {e}")
            print(f"  Point: lon={point_geom.x}, lat={point_geom.y}")
            return []

    def get_nearby_perception(self, point_geom):
        """
        Find the nearest street view scene analysis point within ~150m from DuckDB.
        Returns the full scene_analysis dict, or None if nothing nearby.
        """
        result = None
        try:
            # Query DuckDB for nearest perception point within ~150m
            # 0.0015 degrees ≈ 150m at Barcelona latitude
            buffer_deg = 0.0015
            
            query = f"""
            SELECT 
                scene_overview, buildings, materials, building_condition,
                street_furniture, vegetation_text, signage, ground_surfaces,
                spatial_impression, pedestrian_activity, lighting_atmosphere,
                as_resident, as_commuter, as_tourist, as_student,
                latitude, longitude
            FROM streetview_perception
            WHERE ST_DWithin(geometry, ST_GeomFromText('POINT ({point_geom.x} {point_geom.y})'), {buffer_deg})
            ORDER BY ST_Distance(geometry, ST_GeomFromText('POINT ({point_geom.x} {point_geom.y})'))
            LIMIT 1
            """
            result = self.con.execute(query).fetchone()
        except Exception as e:
            print(f"Perception DB query error: {e}")

        if result:
            # Map database columns to scene_analysis dict format
            return {
                "scene_overview": result[0] or "",
                "buildings": result[1] or "",
                "materials": result[2] or "",
                "building_condition": result[3] or "",
                "street_furniture": result[4] or "",
                "vegetation": result[5] or "",
                "signage": result[6] or "",
                "ground_surfaces": result[7] or "",
                "spatial_enclosure": result[8] or "",
                "pedestrian_activity": result[9] or "",
                "lighting_atmosphere": result[10] or "",
                "as_resident": result[11] or "",
                "as_commuter": result[12] or "",
                "as_tourist": result[13] or "",
                "as_student": result[14] or "",
            }
        
        # Fallback to JSON cache if DuckDB returns no result or fails
        _THRESHOLD_DEG = 0.0015
        best = None
        best_dist = _THRESHOLD_DEG
        for entry in self._sv_cache:
            dx = entry["lon"] - point_geom.x
            dy = entry["lat"] - point_geom.y
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < best_dist:
                best_dist = dist
                best = entry
        return dict(best["scene_analysis"]) if best else None

    def _pick_target_for_archetype(self, archetype: str) -> dict | None:
        """Query a random amenity matching the archetype's preferred types. Returns None on failure."""
        amenity_types = self.ARCHETYPE_AMENITY_TYPES.get(archetype, [])
        if not amenity_types:
            return None
        placeholders = ", ".join(["?"] * len(amenity_types))
        query = f"""
            SELECT name, amenity,
                   ST_X(ST_Centroid(geometry)) AS lon,
                   ST_Y(ST_Centroid(geometry)) AS lat
            FROM amenities
            WHERE amenity IN ({placeholders})
            ORDER BY RANDOM() LIMIT 1
        """
        try:
            row = self.con.execute(query, amenity_types).fetchone()
            if row is None:
                return None
            return {"name": str(row[0] or "Unknown"), "amenity_type": str(row[1]),
                    "lon": float(row[2]), "lat": float(row[3])}
        except Exception as e:
            print(f"[WARN] _pick_target_for_archetype({archetype}): {e}")
            return None

    def _find_nearest_node(self, lon: float, lat: float) -> tuple | None:
        """Find the nearest node in the main connected component to (lon, lat). O(N)."""
        search_space = self.main_component_nodes if getattr(self, 'main_component_nodes', None) else self.node_to_edges.keys()
        best_key, best_dist_sq = None, float("inf")
        for node_key in search_space:
            dx, dy = node_key[0] - lon, node_key[1] - lat
            d = dx * dx + dy * dy
            if d < best_dist_sq:
                best_dist_sq, best_key = d, node_key
        return best_key

    def dijkstra_next_node(self, from_node: tuple, to_node: tuple) -> tuple | None:
        """Return the next node key on the shortest weighted path from from_node to to_node."""
        import heapq
        if from_node == to_node or from_node not in self.node_to_edges:
            return None
        dist = {from_node: 0.0}
        prev: dict = {from_node: None}
        heap = [(0.0, from_node)]
        while heap:
            d, u = heapq.heappop(heap)
            if d > dist.get(u, float("inf")):
                continue
            if u == to_node:
                break
            for entry in self.node_to_edges.get(u, []):
                _eid, geom, direction = entry[0], entry[1], entry[2]
                weight_mult = entry[3] if len(entry) > 3 else 1.0
                if direction == "forward":
                    v = (round(geom.coords[-1][0], 6), round(geom.coords[-1][1], 6))
                else:
                    v = (round(geom.coords[0][0], 6), round(geom.coords[0][1], 6))
                new_dist = d + geom.length * weight_mult
                if new_dist < dist.get(v, float("inf")):
                    dist[v] = new_dist
                    prev[v] = u
                    heapq.heappush(heap, (new_dist, v))
        if to_node not in prev:
            return None
        node = to_node
        while prev.get(node) != from_node:
            node = prev.get(node)
            if node is None:
                return None
        return node

    def _compute_proposed_path(self, current_node, destination: dict) -> dict:
        """Compute the full Dijkstra path from current node to target destination.

        Returns a dict with:
            nodes: list of (lon, lat) tuples along the path
            total_distance: total path length in degrees
            created_at_step: simulation step when path was computed
        """
        if not current_node or not destination or not destination.get("target_node"):
            return {"nodes": [], "total_distance": 0.0, "created_at_step": self.steps}

        target_node = destination["target_node"]
        if isinstance(target_node, (list, tuple)):
            target_node = (round(float(target_node[0]), 6), round(float(target_node[1]), 6))
        if current_node == target_node:
            return {"nodes": [], "total_distance": 0.0, "created_at_step": self.steps}

        import heapq
        dist = {current_node: 0.0}
        prev: dict = {current_node: None}
        heap = [(0.0, current_node)]
        while heap:
            d, u = heapq.heappop(heap)
            if d > dist.get(u, float("inf")):
                continue
            if u == target_node:
                break
            for entry in self.node_to_edges.get(u, []):
                _eid, geom, direction = entry[0], entry[1], entry[2]
                weight_mult = entry[3] if len(entry) > 3 else 1.0
                if direction == "forward":
                    v = (round(geom.coords[-1][0], 6), round(geom.coords[-1][1], 6))
                else:
                    v = (round(geom.coords[0][0], 6), round(geom.coords[0][1], 6))
                new_dist = d + geom.length * weight_mult
                if new_dist < dist.get(v, float("inf")):
                    dist[v] = new_dist
                    prev[v] = u
                    heapq.heappush(heap, (new_dist, v))

        if target_node not in prev:
            return {"nodes": [], "total_distance": 0.0, "created_at_step": self.steps}

        path_nodes = []
        node = target_node
        while node is not None:
            path_nodes.append(node)
            node = prev.get(node)
        path_nodes.reverse()

        return {
            "nodes": path_nodes,
            "total_distance": dist.get(target_node, 0.0),
            "created_at_step": self.steps,
        }

    def step(self):
        self.steps += 1
        # Step all agents
        random.shuffle(self.city_agents)
        for agent in self.city_agents:
            agent.step()
        
        # Periodically flush tracker data to disk (every 10 steps)
        if self.tracker and self.steps % 10 == 0:
            self.tracker.flush()

    async def async_step(self):
        """Async-native step for use within FastAPI endpoints."""
        self.steps += 1
        random.shuffle(self.city_agents)
        
        # Run all agent async steps concurrently
        # If recording, capture agent states after each step
        if self._recorder and self._recorder.is_recording:
            # Record each agent's state after stepping
            for agent in self.city_agents:
                await agent._async_step()
                # Extract decision data from agent's last mobility action
                decision_reason = None
                is_fallback = False
                if hasattr(agent, 'memory') and hasattr(agent.memory, 'stream'):
                    try:
                        recent = await agent.memory.stream.get_recent("mobility", n=1)
                        if recent:
                            event = recent[0]
                            if hasattr(event, 'step') and event.step == self.steps:
                                decision_reason = event.description
                                is_fallback = getattr(event, 'fallback', False) if hasattr(event, 'fallback') else False
                    except Exception:
                        pass
                
                self._recorder.record_agent_state(
                    agent=agent,
                    step=self.steps,
                    decision_reason=decision_reason,
                    is_fallback=is_fallback,
                )
        else:
            # Normal step without recording
            await asyncio.gather(*[agent._async_step() for agent in self.city_agents])
        
        if self.tracker and self.steps % 10 == 0:
            self.tracker.flush()
    
    def __del__(self):
        """Cleanup database connection and tracker"""
        try:
            if self.tracker:
                self.tracker.close()
        except:
            pass
        try:
            self.con.close()
        except:
            pass

    def set_recorder(self, recorder) -> None:
        """Set the GeoParquet recorder for this model."""
        self._recorder = recorder
        print(f"[OK] Recorder set: {recorder.session_id if recorder else None}")

    def clear_recorder(self) -> None:
        """Clear the recorder from this model."""
        if self._recorder:
            print(f"[OK] Recorder cleared: {self._recorder.session_id}")
        self._recorder = None

    def is_recording(self) -> bool:
        """Check if recording is active."""
        return self._recorder is not None and self._recorder.is_recording
