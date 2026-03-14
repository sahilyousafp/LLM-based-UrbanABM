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

# Path to the DuckDB database (OSM data)
PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "Environment" / "eixample_osm.duckdb"

class CityAgent(mg.GeoAgent):
    """
    A pedestrian agent (OSM data) with LLM-driven movement decisions.
    Memory stores agent state; BlockDispatcher coordinates reasoning blocks.
    """

    ARCHETYPES = ["resident", "commuter", "tourist", "student"]

    def __init__(self, model, geometry, crs="EPSG:4326", edge_id=None, edge_geom=None, archetype="resident"):
        super().__init__(model=model, geometry=geometry, crs=crs)
        self.agent_type = "CityAgent"
        self.nearby_amenities = []
        self.current_edge_id = edge_id
        self.current_edge_geom = edge_geom
        self.previous_edge_id = None
        self.position_along_edge = 0.0
        self.move_speed = random.uniform(0.15, 0.25)

        self.memory = Memory(agent_id=self.unique_id)
        self.dispatcher = BlockDispatcher(
            llm_client=model.llm_client,
            memory=self.memory,
            context={"model": model},
        )
        self._init_memory_sync(edge_id, geometry, archetype)

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
        prefs = {
            "resident": ["supermarket", "pharmacy", "park"],
            "commuter": ["direct_route", "transport", "cafe"],
            "tourist": ["attraction", "cafe", "restaurant"],
            "student": ["cafe", "library", "park"],
        }
        self.memory.status._data["agent_profile"] = {
            "archetype": archetype,
            "age": random.randint(18, 70),
            "preferences": prefs.get(archetype, []),
        }
        self.memory.status._data["position"] = {"lon": geometry.x, "lat": geometry.y, "edge_id": edge_id}
        if edge_id is not None:
            self.memory.status._data["visited_edges"] = {str(edge_id): 1}

    async def _init_memory(self, edge_id, geometry, archetype: str) -> None:
        prefs = {
            "resident": ["supermarket", "pharmacy", "park"],
            "commuter": ["direct_route", "transport", "cafe"],
            "tourist": ["attraction", "cafe", "restaurant"],
            "student": ["cafe", "library", "park"],
        }
        await self.memory.status.update("agent_profile", {
            "archetype": archetype,
            "age": random.randint(18, 70),
            "preferences": prefs.get(archetype, []),
        })
        await self.memory.status.update("position", {"lon": geometry.x, "lat": geometry.y, "edge_id": edge_id})
        if edge_id is not None:
            await self.memory.status.update("visited_edges", {str(edge_id): 1})

    def step(self):
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(asyncio.run, self._async_step())
                    future.result(timeout=30)
            else:
                loop.run_until_complete(self._async_step())
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Agent {self.unique_id} step error: {e}")
            self._advance_along_edge()

    async def _async_step(self) -> None:
        self.nearby_amenities = self.model.get_nearby_amenities(self.geometry)
        await self.memory.status.update("position", {
            "lon": self.geometry.x, "lat": self.geometry.y, "edge_id": self.current_edge_id,
        })
        candidate_edges = self._get_candidate_edges()
        result = await self.dispatcher.run(
            step=self.model.steps,
            candidate_edges=candidate_edges,
            nearby_amenities=self.nearby_amenities,
        )
        if result.mobility.action == "move_to_edge":
            self._apply_mobility(result.mobility.params)
        self._advance_along_edge()
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
        if self.current_edge_geom is None:
            return []
        end_point = Point(self.current_edge_geom.coords[-1])
        raw_edges = self.model.find_connected_edges(end_point)
        candidates = [(eid, g, d) for eid, g, d in raw_edges if eid != self.current_edge_id]
        if not candidates:
            candidates = raw_edges
        return [
            {"edge_id": eid, "geom": g, "direction": d, "amenities": [], "description": f"{d} edge"}
            for eid, g, d in candidates
        ]

    def _apply_mobility(self, params: dict) -> None:
        geom = params.get("geom") or self.model.edges.get(params.get("edge_id"))
        if geom is None:
            return
        if params.get("direction") == "reverse":
            geom = LineString(list(geom.coords)[::-1])
        self.previous_edge_id = self.current_edge_id
        self.current_edge_id = params.get("edge_id")
        self.current_edge_geom = geom
        self.position_along_edge = 0.0

    def _advance_along_edge(self) -> None:
        if self.current_edge_geom is None:
            return
        self.position_along_edge += self.move_speed
        if self.position_along_edge >= 1.0:
            self.position_along_edge = 0.0
        coords = list(self.current_edge_geom.coords)
        if len(coords) >= 2:
            idx = min(int(self.position_along_edge * (len(coords) - 1)), len(coords) - 2)
            frac = (self.position_along_edge * (len(coords) - 1)) - idx
            x1, y1 = coords[idx]
            x2, y2 = coords[idx + 1]
            self.geometry = Point(x1 + (x2 - x1) * frac, y1 + (y2 - y1) * frac)

    def to_dict(self):
        return {
            "id": self.unique_id,
            "type": self.agent_type,
            "location": {"lon": self.geometry.x, "lat": self.geometry.y}
        }

class CityModel(mesa.Model):
    """A model (OSM data) with LLM-driven agents moving through a city"""
    
    def __init__(self, num_agents=500):
        super().__init__()
        self.num_agents = num_agents
        self.steps = 0
        self.city_agents = []
        self.edge_visit_count_global = {}

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
        edge_ids = list(self.edges.keys())
        
        if not edge_ids:
            print("[ERROR] No edges available for spawning!")
            return
        
        for i in range(num_agents):
            try:
                edge_id = random.choice(edge_ids)
                edge_geom = self.edges[edge_id]
                start_point = Point(edge_geom.coords[0])
                archetype = CityAgent.ARCHETYPES[i % len(CityAgent.ARCHETYPES)]
                if i == 0:
                    print(f"  Agent 0: lon={start_point.x:.6f}, lat={start_point.y:.6f} on edge {edge_id} [{archetype}]")
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
    
    def step(self):
        self.steps += 1
        reset_step_counter(self.steps)
        random.shuffle(self.city_agents)
        for agent in self.city_agents:
            agent.step()
        if self.tracker and self.steps % 10 == 0:
            self.tracker.flush()

    async def async_step(self):
        """Async-native step for use within FastAPI endpoints."""
        self.steps += 1
        reset_step_counter(self.steps)
        random.shuffle(self.city_agents)
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
