import mesa
import mesa_geo as mg
import duckdb
from shapely import wkt
from shapely.geometry import Point, LineString
import random
import os
from collections import defaultdict

# Path to the DuckDB database
DB_PATH = r"d:\IaaC\2ND YEAR\THESIS\CODE EXPLORATIONS\Environment\Term02\DuckDB_OSM_Pipeline\OSM\eixample_osm.duckdb"

class CityAgent(mg.GeoAgent):
    """An agent that walks around the city"""
    
    def __init__(self, model, geometry, crs="EPSG:4326", edge_id=None, edge_geom=None):
        # GeoAgent requires model, geometry, and crs
        super().__init__(model=model, geometry=geometry, crs=crs)
        self.agent_type = "CityAgent"
        self.nearby_amenities = []
        
        # Network movement attributes
        self.current_edge_id = edge_id
        self.current_edge_geom = edge_geom
        self.previous_edge_id = None  # Track previous edge to prevent backtracking
        self.position_along_edge = 0.0  # 0.0 to 1.0
        self.move_speed = random.uniform(0.15, 0.25)  # Move 15-25% of edge per step (faster, varied speeds)
        
        # Exploration tracking to reduce revisiting same areas
        self.edge_visit_count = {}  # Count visits per edge
        if edge_id:
            self.edge_visit_count[edge_id] = 1
    
    def step(self):
        # Move along current edge
        if self.current_edge_geom is not None:
            self.position_along_edge += self.move_speed
            
            # If we've reached the end of the edge, pick a new edge
            if self.position_along_edge >= 1.0:
                self._select_next_edge()
            
            # Update position along current edge
            if self.current_edge_geom is not None:
                coords = list(self.current_edge_geom.coords)
                # Simple linear interpolation along edge
                if len(coords) >= 2:
                    idx = min(int(self.position_along_edge * (len(coords) - 1)), len(coords) - 2)
                    frac = (self.position_along_edge * (len(coords) - 1)) - idx
                    
                    x1, y1 = coords[idx]
                    x2, y2 = coords[idx + 1]
                    
                    new_x = x1 + (x2 - x1) * frac
                    new_y = y1 + (y2 - y1) * frac
                    
                    self.geometry = Point(new_x, new_y)
        
        # Query DuckDB for nearby amenities (always, not just on click)
        self.nearby_amenities = self.model.get_nearby_amenities(self.geometry)
    
    def _select_next_edge(self):
        """Select the next edge to walk along, avoiding backtracking and preferring exploration"""
        if self.current_edge_id is None:
            return
        
        # Get current edge end point
        if self.current_edge_geom:
            end_point = Point(self.current_edge_geom.coords[-1])
            
            # Find edges connected to this end point (bidirectional)
            next_edges = self.model.find_connected_edges(end_point)
            
            if next_edges:
                # Filter out the CURRENT edge to prevent immediate reversal on same edge
                candidate_edges = [
                    (eid, geom, direction) for eid, geom, direction in next_edges 
                    if eid != self.current_edge_id
                ]
                
                # If filtering left no options, allow backtracking (dead end)
                if not candidate_edges:
                    candidate_edges = next_edges
                
                # Prefer less-visited edges for exploration (70% of the time)
                if len(candidate_edges) > 1 and random.random() < 0.7:
                    # Sort by visit count (ascending - prefer unvisited)
                    candidate_edges.sort(key=lambda e: self.edge_visit_count.get(e[0], 0))
                    
                    # Pick from the least-visited half
                    cutoff = max(1, len(candidate_edges) // 2)
                    next_edge_id, next_edge_geom, direction = random.choice(candidate_edges[:cutoff])
                else:
                    # Pick randomly (allows some variability)
                    next_edge_id, next_edge_geom, direction = random.choice(candidate_edges)
                
                # Handle reverse direction: flip the geometry
                if direction == 'reverse':
                    next_edge_geom = LineString(list(next_edge_geom.coords)[::-1])
                
                # Update edge tracking
                self.previous_edge_id = self.current_edge_id
                self.current_edge_id = next_edge_id
                self.current_edge_geom = next_edge_geom
                self.position_along_edge = 0.0
                
                # Track visits
                self.edge_visit_count[next_edge_id] = self.edge_visit_count.get(next_edge_id, 0) + 1
            else:
                # No connected edges - stay on current edge or reset
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
    """A model with agents moving through a city"""
    
    def __init__(self, num_agents=10000):
        super().__init__()
        self.num_agents = num_agents
        self.steps = 0
        
        # Use custom attribute name (Mesa 3.0+ reserves 'agents')
        self.city_agents = []
        
        # Connect to DuckDB
        print(f"Connecting to DB at: {DB_PATH}")
        try:
            self.con = duckdb.connect(DB_PATH, read_only=True)
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
                # Pick a random edge
                edge_id = random.choice(edge_ids)
                edge_geom = self.edges[edge_id]
                
                # Start at beginning of edge
                start_point = Point(edge_geom.coords[0])
                
                if i == 0:
                    print(f"  Agent 0: lon={start_point.x:.6f}, lat={start_point.y:.6f} on edge {edge_id}")
                
                # Create agent with edge information
                agent = CityAgent(
                    model=self, 
                    geometry=start_point, 
                    crs="EPSG:4326",
                    edge_id=edge_id,
                    edge_geom=edge_geom
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
        # Step all agents
        random.shuffle(self.city_agents)
        for agent in self.city_agents:
            agent.step()
    
    def __del__(self):
        """Cleanup database connection"""
        try:
            self.con.close()
        except:
            pass
