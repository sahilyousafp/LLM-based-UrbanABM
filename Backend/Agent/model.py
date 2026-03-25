import asyncio
import mesa
import mesa_geo as mg
import duckdb
from shapely import wkt
from shapely.geometry import Point, LineString
import random
import os
import sys
from collections import defaultdict
from pathlib import Path
from agent_tracker import AgentTracker

# Add Backend root to path for cross-module imports
_BACKEND_ROOT = Path(__file__).parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from LLM.llm_config import LLMConfig
from LLM.llm_client import LLMClient
from LLM.Memory.memory import Memory
from LLM.Thinking.dispatcher import BlockDispatcher, reset_step_counter

# Path to the DuckDB database (Overture Maps data)
PROJECT_ROOT = Path(__file__).parent.parent.parent
DB_PATH = PROJECT_ROOT / "Backend" / "Environment" / "eixample_overture.duckdb"

class CityAgent(mg.GeoAgent):
    """
    A pedestrian agent with LLM-driven movement decisions.
    Memory stores agent state; BlockDispatcher coordinates reasoning blocks.
    """

    # Archetype pool — assigned round-robin during model init
    ARCHETYPES = ["resident", "commuter", "tourist", "student"]

    def __init__(self, model, geometry, crs="EPSG:4326", edge_id=None, edge_geom=None, archetype="resident"):
        super().__init__(model=model, geometry=geometry, crs=crs)
        self.agent_type = "CityAgent"
        self.nearby_amenities = []
        self.street_perception = None

        # Network movement attributes
        self.current_edge_id = edge_id
        self.current_edge_geom = edge_geom
        self.previous_edge_id = None
        self.position_along_edge = 0.0
        self.move_speed = random.uniform(0.15, 0.25)

        # --- LLM + Memory + Thinking ---
        self.memory = Memory(agent_id=self.unique_id)
        self.dispatcher = BlockDispatcher(
            llm_client=model.llm_client,
            memory=self.memory,
            context={"model": model},
        )

        # Initialise memory with agent profile (sync — safe at init time, no loop running)
        self._init_memory_sync(edge_id, geometry, archetype)

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

    def _init_memory_sync(self, edge_id, geometry, archetype: str) -> None:
        """Initialise memory synchronously at agent creation (no asyncio loop needed)."""
        self.memory.status._data["agent_profile"] = {
            "archetype": archetype,
            "age": random.randint(18, 70),
            "preferences": self._archetype_preferences(archetype),
        }
        self.memory.status._data["position"] = {
            "lon": geometry.x,
            "lat": geometry.y,
            "edge_id": edge_id,
        }
        if edge_id is not None:
            self.memory.status._data["visited_edges"] = {str(edge_id): 1}

    async def _init_memory(self, edge_id, geometry, archetype: str) -> None:
        """Set up initial memory state for this agent."""
        await self.memory.status.update("agent_profile", {
            "archetype": archetype,
            "age": random.randint(18, 70),
            "preferences": self._archetype_preferences(archetype),
        })
        await self.memory.status.update("position", {
            "lon": geometry.x,
            "lat": geometry.y,
            "edge_id": edge_id,
        })
        if edge_id is not None:
            await self.memory.status.update("visited_edges", {str(edge_id): 1})

    @staticmethod
    def _archetype_preferences(archetype: str) -> list:
        prefs = {
            "resident": ["supermarket", "pharmacy", "park", "home_area"],
            "commuter": ["direct_route", "transport", "cafe", "efficiency"],
            "tourist": ["attraction", "cafe", "restaurant", "new_streets", "views"],
            "student": ["cafe", "library", "park", "social", "cheap_food"],
        }
        return prefs.get(archetype, [])

    def step(self):
        """Synchronous step — runs async dispatcher via event loop."""
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
        # Get current perception mode from model
        perception_mode = getattr(self.model, 'perception_mode', 'both')

        # Query DuckDB for nearby amenities (if mode allows)
        if perception_mode in ['amenities', 'both']:
            self.nearby_amenities = self.model.get_nearby_amenities(self.geometry)
        else:
            self.nearby_amenities = []

        # Query nearest street view perception data (if mode allows)
        if perception_mode in ['perception', 'both']:
            self.street_perception = self.model.get_nearby_perception(self.geometry)
        else:
            self.street_perception = None

        # Update position in memory
        await self.memory.status.update("position", {
            "lon": self.geometry.x,
            "lat": self.geometry.y,
            "edge_id": self.current_edge_id,
        })

        # Only evaluate a new edge if we've reached the end of the current one
        # or if we don't have an edge yet.
        needs_new_edge = (
            self.current_edge_id is None or 
            self.position_along_edge >= 1.0
        )

        # Build candidate edges for mobility decision
        candidate_edges = self._get_candidate_edges()

        # Run all thinking blocks (needs + cognition + mobility)
        result = await self.dispatcher.run(
            step=self.model.steps,
            candidate_edges=candidate_edges,
            nearby_amenities=self.nearby_amenities,
            street_perception=self.street_perception,
            needs_new_edge=needs_new_edge,
        )

        # Apply mobility decision — move to chosen edge
        if needs_new_edge and result.mobility.action == "move_to_edge":
            self._apply_mobility(result.mobility.params)
            # Log decision with fallback status
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
                    is_fallback=result.mobility.fallback
                )
        elif result.mobility.action == "stay":
            pass # Stay on current edge

        # Advance position along current edge
        self._advance_along_edge()

        # Log to tracker
        if hasattr(self.model, 'tracker') and self.model.tracker:
            self.model.tracker.log_movement(
                agent_id=self.unique_id,
                step_number=self.model.steps,
                longitude=self.geometry.x,
                latitude=self.geometry.y,
                edge_id=self.current_edge_id,
                position_along_edge=self.position_along_edge,
                speed=self.move_speed,
                nearby_amenities_count=len(self.nearby_amenities)
            )

    def _get_candidate_edges(self) -> list[dict]:
        """Get reachable edges from current position as dicts for the dispatcher."""
        if self.current_edge_geom is None:
            return []
        end_point = Point(self.current_edge_geom.coords[-1])
        raw_edges = self.model.find_connected_edges(end_point)
        # Exclude current edge to prevent immediate reversal
        candidates = [(eid, geom, d) for eid, geom, d in raw_edges if eid != self.current_edge_id]
        if not candidates:
            candidates = raw_edges  # Dead end — allow reversal

        result = []
        for eid, geom, direction in candidates:
            # Annotate each candidate with nearby amenity types and perception for the prompt
            midpoint = Point(geom.coords[len(geom.coords) // 2])
            amenity_types = [a.get("type", "") for a in self.model.get_nearby_amenities(midpoint)[:3]]
            perception = self.model.get_streetview_perception(midpoint)
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
        candidates = [(eid, g, d) for eid, g, d in next_edges if eid != self.current_edge_id]
        if not candidates:
            candidates = next_edges
        if not candidates:
            self.position_along_edge = 0.0
            return
        candidates.sort(key=lambda e: self.model.edge_visit_count_global.get(e[0], 0))
        eid, geom, direction = candidates[0]
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
    
    def __init__(self, num_agents=500):
        super().__init__()
        self.num_agents = num_agents
        self.steps = 0
        self.perception_mode = "both"  # 'amenities', 'perception', or 'both'

        # Use custom attribute name (Mesa 3.0+ reserves 'agents')
        self.city_agents = []
        self.edge_visit_count_global = {}  # Shared visit count for sync fallback

        # --- Shared LLM client (one per model, shared across all agents) ---
        llm_config = LLMConfig.from_env()
        self.llm_client = LLMClient(llm_config)
        print(f"[OK] LLM client: provider={llm_config.provider}, model={llm_config.model}")

        # Initialize agent tracker
        try:
            self.tracker = AgentTracker()
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
        try:
            edges_df = self.con.execute("""
                SELECT 
                    rowid as edge_id,
                    ST_AsText(geometry) as wkt,
                    ST_AsText(ST_StartPoint(geometry)) as start_wkt,
                    ST_AsText(ST_EndPoint(geometry)) as end_wkt
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
                
                self.edges[edge_id] = edge_geom
                
                # BIDIRECTIONAL: Map BOTH start and end points to edges
                # This allows agents to traverse edges in both directions
                start_key = (round(start_point.x, 6), round(start_point.y, 6))
                end_key = (round(end_point.x, 6), round(end_point.y, 6))
                
                # From start point: can take this edge in forward direction
                self.node_to_edges[start_key].append((edge_id, edge_geom, 'forward'))
                # From end point: can take this edge in reverse direction  
                self.node_to_edges[end_key].append((edge_id, edge_geom, 'reverse'))
            
            print(f"[OK] Built BIDIRECTIONAL network graph with {len(self.node_to_edges)} nodes")
            
        except Exception as e:
            print(f"[ERROR] Failed to load walk edges: {e}")
            self.edges = {}
            self.node_to_edges = defaultdict(list)
        
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
        
        for i in range(num_agents):
            try:
                # Pick a random edge
                edge_id = random.choice(edge_ids)
                edge_geom = self.edges[edge_id]
                
                # Start at beginning of edge
                start_point = Point(edge_geom.coords[0])
                
                # Assign archetype round-robin
                archetype = CityAgent.ARCHETYPES[i % len(CityAgent.ARCHETYPES)]
                
                if i == 0:
                    print(f"  Agent 0: lon={start_point.x:.6f}, lat={start_point.y:.6f} on edge {edge_id} [{archetype}]")
                
                # Create agent with edge information and archetype
                agent = CityAgent(
                    model=self, 
                    geometry=start_point, 
                    crs="EPSG:4326",
                    edge_id=edge_id,
                    edge_geom=edge_geom,
                    archetype=archetype,
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
            
            if not result:
                return None
            
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
        except Exception as e:
            print(f"Perception query error: {e}")
            # Fallback to JSON cache if DuckDB query fails
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

    def step(self):
        self.steps += 1
        reset_step_counter(self.steps)
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
        reset_step_counter(self.steps)
        random.shuffle(self.city_agents)
        # Run all agent async steps concurrently
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
