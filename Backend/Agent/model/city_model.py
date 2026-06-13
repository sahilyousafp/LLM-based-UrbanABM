import asyncio
import json as _json_mod
import math as _math
import os
import random
import re as _re_mod
import sys
from collections import defaultdict
from pathlib import Path

import duckdb
import mesa
import networkx as nx
from shapely import wkt
from shapely.geometry import Point, LineString

# Add Backend root to path for cross-module imports
_BACKEND_ROOT = Path(__file__).parent.parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from LLM.llm_config import LLMConfig
from LLM.llm_client import LLMClient
from agent_tracker import AgentTracker
from rule_based_movement import make_decision as rule_based_move

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
DB_PATH = PROJECT_ROOT / "Backend" / "Environment" / "eixample_overture.duckdb"

# Pedestrian-class edges included in footway-only routing mode
_PED_CLASSES = frozenset({
    'footway', 'path', 'cycleway', 'living_street',
    'pedestrian', 'steps', 'connector',
    'residential', 'tertiary', 'service', 'unclassified',
})

_TIME_PHASES = ["morning", "afternoon", "evening", "night"]
_STEPS_PER_PHASE = 24


class CityModel(mesa.Model):
    """A model with LLM-driven agents moving through a city."""

    ARCHETYPE_AMENITY_TYPES: dict = {
        "commuter": ["office", "coworking_space", "commercial", "company", "bank"],
        "tourist":  ["attraction", "museum", "cafe", "viewpoint"],
        "student":  ["library", "university", "cafe"],
    }

    ARCHETYPE_PATH_ADHERENCE: dict = {
        "commuter": 1.00,
        "resident": 0.50,
        "student":  0.33,
        "tourist":  0.25,
    }

    def __init__(self, num_agents=None, spawn_seed=None, agent_gender="unknown", agent_age=None):
        super().__init__()
        if num_agents is None:
            num_agents = int(os.getenv("NUM_AGENTS", 50))
        self.num_agents = num_agents
        self._agent_gender = agent_gender
        self._agent_age = agent_age
        self.steps = 0
        self.perception_mode = os.getenv("PERCEPTION_MODE", "both")
        self.routing_mode = os.getenv("ROUTING_MODE", "all")
        self.llm_fallback = True
        self.spawn_seed = spawn_seed

        if spawn_seed is not None:
            random.seed(spawn_seed)
            print(f"[OK] Random seed set to: {spawn_seed} (reproducible spawning)")
        else:
            print(f"[INFO] No spawn seed set (random spawning each run)")

        self.city_agents = []
        self.edge_visit_count_global = {}
        self._recorder = None

        llm_config = LLMConfig.from_env()
        self.llm_client = LLMClient(llm_config)
        # -1 = unlimited (default), 0 = all rule-based, N = max LLM calls per step
        self.llm_calls_per_step = int(os.getenv("LLM_CALLS_PER_STEP", "-1"))
        self.llm_calls_this_step = 0
        pool_keys = len(llm_config.api_keys) if llm_config.api_keys else 1
        budget_label = "unlimited" if self.llm_calls_per_step < 0 else str(self.llm_calls_per_step)
        print(f"[OK] LLM client: provider={llm_config.provider}, model={llm_config.model}, "
              f"key_pool={pool_keys}, budget/step={budget_label}")

        try:
            _tracker_db = PROJECT_ROOT / "Documentation" / "tracking_data" / "agent_tracking.duckdb"
            _tracker_db.parent.mkdir(parents=True, exist_ok=True)
            self.tracker = AgentTracker(db_path=_tracker_db)
            print(f"[OK] Agent tracker initialized: {self.tracker.db_path}")
        except Exception as e:
            print(f"[WARN] Failed to initialize agent tracker: {e}")
            self.tracker = None

        print(f"Connecting to DB at: {DB_PATH}")
        try:
            self.con = duckdb.connect(str(DB_PATH), read_only=True)
            self.con.install_extension("spatial")
            self.con.load_extension("spatial")
            print("[OK] Database connected")
        except Exception as e:
            print(f"[ERROR] Database connection failed: {e}")
            return

        print("Loading walk_edges network...")
        _ROAD_WEIGHT = {
            'footway':       1.0,
            'path':          1.0,
            'cycleway':      1.0,
            'living_street': 1.0,
            'pedestrian':    1.0,
            'residential':   1.0,
            'connector':     1.0,
            'steps':         1.0,
            'service':       1.0,
            'unclassified':  1.0,
            'tertiary':      1.0,
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

            self.edges = {}
            self.node_to_edges = defaultdict(list)
            self.node_to_ped_edges = defaultdict(list)

            def _metric_length(geom) -> float:
                total = 0.0
                cs = list(geom.coords)
                for i in range(len(cs) - 1):
                    dlon = cs[i + 1][0] - cs[i][0]
                    dlat = cs[i + 1][1] - cs[i][1]
                    mid_lat = (cs[i][1] + cs[i + 1][1]) / 2
                    dx = dlon * 111320 * _math.cos(_math.radians(mid_lat))
                    dy = dlat * 110540
                    total += _math.sqrt(dx * dx + dy * dy)
                return total if total > 0 else 1e-6

            for _, row in edges_df.iterrows():
                edge_id = row['edge_id']
                edge_geom = wkt.loads(row['wkt'])
                start_point = wkt.loads(row['start_wkt'])
                end_point = wkt.loads(row['end_wkt'])
                road_class = row.get('road_class') or 'connector'
                weight_multiplier = _ROAD_WEIGHT.get(road_class, 2.0)
                length_m = _metric_length(edge_geom)

                self.edges[edge_id] = edge_geom
                start_key = (round(start_point.x, 6), round(start_point.y, 6))
                end_key = (round(end_point.x, 6), round(end_point.y, 6))

                self.node_to_edges[start_key].append((edge_id, edge_geom, 'forward', weight_multiplier, length_m))
                self.node_to_edges[end_key].append((edge_id, edge_geom, 'reverse', weight_multiplier, length_m))

                if road_class in _PED_CLASSES:
                    self.node_to_ped_edges[start_key].append((edge_id, edge_geom, 'forward', weight_multiplier, length_m))
                    self.node_to_ped_edges[end_key].append((edge_id, edge_geom, 'reverse', weight_multiplier, length_m))

            print(f"[OK] Built BIDIRECTIONAL network graph with {len(self.node_to_edges)} nodes")

            # --- Planarize: split edges at every intersection ---
            # Overture data has edges that cross without sharing nodes.
            # unary_union splits all LineStrings at crossing points, creating proper intersections.
            try:
                from shapely.ops import unary_union
                _orig_edges = len(self.edges)
                _all_geoms = list(self.edges.values())
                _merged = unary_union(_all_geoms)
                if _merged.geom_type == "MultiLineString":
                    _segments = list(_merged.geoms)
                elif _merged.geom_type == "LineString":
                    _segments = [_merged]
                else:
                    _segments = []

                if len(_segments) > _orig_edges:
                    print(f"[PLANARIZE] {_orig_edges} edges -> {len(_segments)} planarized segments (+{len(_segments)-_orig_edges} splits)")
                    self.edges.clear()
                    self.node_to_edges.clear()
                    self.node_to_ped_edges.clear()

                    for _seg in _segments:
                        _seg_len = _metric_length(_seg)
                        _eid = len(self.edges)
                        self.edges[_eid] = _seg

                        _sk = (round(_seg.coords[0][0], 6), round(_seg.coords[0][1], 6))
                        _ek = (round(_seg.coords[-1][0], 6), round(_seg.coords[-1][1], 6))
                        if _sk == _ek:
                            continue  # skip degenerate

                        _wm = 1.0  # all road classes weight 1.0
                        self.node_to_edges[_sk].append((_eid, _seg, "forward", _wm, _seg_len))
                        self.node_to_edges[_ek].append((_eid, _seg, "reverse", _wm, _seg_len))

                        self.node_to_ped_edges[_sk].append((_eid, _seg, "forward", _wm, _seg_len))
                        self.node_to_ped_edges[_ek].append((_eid, _seg, "reverse", _wm, _seg_len))

                    print(f"[PLANARIZE] Rebuilt graph: {len(self.node_to_edges)} nodes, {len(self.edges)} edges")
                else:
                    print(f"[PLANARIZE] No new splits (already planar)")
            except Exception as _ex:
                print(f"[PLANARIZE] Planarization skipped: {_ex}")

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
                        v = (
                            (round(geom.coords[-1][0], 6), round(geom.coords[-1][1], 6))
                            if direction == 'forward'
                            else (round(geom.coords[0][0], 6), round(geom.coords[0][1], 6))
                        )
                        if v not in component:
                            stack.append(v)
                if len(component) > len(best_component):
                    best_component = component
            self.main_component_nodes = best_component
            print(f"[OK] Main connected component: {len(self.main_component_nodes)}/{len(all_nodes)} nodes")

            # --- Snap-merge fragment nodes into main component ---
            _SNAP_DIST = 15.0  # meters – covers intersection area; Eixample blocks are ~130m apart
            _fragments = [n for n in self.node_to_edges if n not in self.main_component_nodes]
            if _fragments:
                _remap = {}
                for _node in _fragments:
                    _best_d = float('inf')
                    _best_key = None
                    _cos_base = _math.cos(_math.radians(_node[1]))
                    for _main in self.main_component_nodes:
                        _dlat = (_node[1] - _main[1]) * 110540
                        _dlon = (_node[0] - _main[0]) * 111320 * _cos_base
                        _d = _dlat * _dlat + _dlon * _dlon
                        if _d < _best_d:
                            _best_d = _d
                            _best_key = _main
                    if _math.sqrt(_best_d) <= _SNAP_DIST and _best_key:
                        _remap[_node] = _best_key

                if _remap:
                    print(f"[SNAP] Merging {len(_remap)} fragment nodes within {_SNAP_DIST}m of main component")
                    for _n2e in (self.node_to_edges, self.node_to_ped_edges):
                        _new_n2e = defaultdict(list)
                        for _u, _entries in list(_n2e.items()):
                            _new_u = _remap.get(_u, _u)
                            for _entry in _entries:
                                _eid, _geom, _dir = _entry[0], _entry[1], _entry[2]
                                _rest = _entry[3:]
                                _v = (
                                    (round(_geom.coords[-1][0], 6), round(_geom.coords[-1][1], 6))
                                    if _dir == "forward"
                                    else (round(_geom.coords[0][0], 6), round(_geom.coords[0][1], 6))
                                )
                                _new_v = _remap.get(_v, _v)
                                if _new_u != _new_v:
                                    _new_n2e[_new_u].append(_entry)
                        _n2e.clear()
                        _n2e.update(_new_n2e)

                    # Recompute main component
                    _all_nodes = set(self.node_to_edges.keys())
                    _unvisited = set(_all_nodes)
                    _best_component = set()
                    while _unvisited:
                        _start = next(iter(_unvisited))
                        _comp = set()
                        _stack = [_start]
                        while _stack:
                            _u = _stack.pop()
                            if _u in _comp:
                                continue
                            _comp.add(_u)
                            _unvisited.discard(_u)
                            for _entry in self.node_to_edges.get(_u, []):
                                _geom, _dir = _entry[1], _entry[2]
                                _v = (
                                    (round(_geom.coords[-1][0], 6), round(_geom.coords[-1][1], 6))
                                    if _dir == 'forward'
                                    else (round(_geom.coords[0][0], 6), round(_geom.coords[0][1], 6))
                                )
                                if _v not in _comp:
                                    _stack.append(_v)
                        if len(_comp) > len(_best_component):
                            _best_component = _comp
                    self.main_component_nodes = _best_component
                    print(f"[SNAP] Main component now: {len(self.main_component_nodes)}/{len(_all_nodes)} nodes")

            self.ped_nodes = frozenset(self.node_to_ped_edges.keys())
            print(f"[OK] Pedestrian-only graph: {len(self.ped_nodes)} nodes (ROUTING_MODE={self.routing_mode})")

            # Compute ped graph's own main connected component
            _ped_all = set(self.node_to_ped_edges.keys())
            _ped_unvisited = set(_ped_all)
            _ped_best = set()
            while _ped_unvisited:
                _ped_start = next(iter(_ped_unvisited))
                _ped_comp = set()
                _ped_stack = [_ped_start]
                while _ped_stack:
                    _pu = _ped_stack.pop()
                    if _pu in _ped_comp:
                        continue
                    _ped_comp.add(_pu)
                    _ped_unvisited.discard(_pu)
                    for _ped_entry in self.node_to_ped_edges.get(_pu, []):
                        _pgeom, _pdir = _ped_entry[1], _ped_entry[2]
                        _pv = (
                            (round(_pgeom.coords[-1][0], 6), round(_pgeom.coords[-1][1], 6))
                            if _pdir == 'forward'
                            else (round(_pgeom.coords[0][0], 6), round(_pgeom.coords[0][1], 6))
                        )
                        if _pv not in _ped_comp:
                            _ped_stack.append(_pv)
                if len(_ped_comp) > len(_ped_best):
                    _ped_best = _ped_comp
            self.main_ped_component_nodes = _ped_best
            print(f"[OK] Pedestrian main component: {len(self.main_ped_component_nodes)}/{len(_ped_all)} nodes")

            self._nx_graph = nx.Graph()
            for u_node, entries in self.node_to_edges.items():
                for entry in entries:
                    _eid, geom, direction = entry[0], entry[1], entry[2]
                    weight_mult = entry[3] if len(entry) > 3 else 1.0
                    length_m = entry[4] if len(entry) > 4 else geom.length * 111000
                    v_node = (
                        (round(geom.coords[-1][0], 6), round(geom.coords[-1][1], 6))
                        if direction == "forward"
                        else (round(geom.coords[0][0], 6), round(geom.coords[0][1], 6))
                    )
                    self._nx_graph.add_edge(u_node, v_node, weight=length_m * weight_mult)

            self._nx_ped_graph = nx.Graph()
            for u_node, entries in self.node_to_ped_edges.items():
                for entry in entries:
                    _eid, geom, direction = entry[0], entry[1], entry[2]
                    weight_mult = entry[3] if len(entry) > 3 else 1.0
                    length_m = entry[4] if len(entry) > 4 else geom.length * 111000
                    v_node = (
                        (round(geom.coords[-1][0], 6), round(geom.coords[-1][1], 6))
                        if direction == "forward"
                        else (round(geom.coords[0][0], 6), round(geom.coords[0][1], 6))
                    )
                    self._nx_ped_graph.add_edge(u_node, v_node, weight=length_m * weight_mult)

            print(f"[OK] NetworkX DiGraphs: {self._nx_graph.number_of_nodes()} nodes / "
                  f"{self._nx_graph.number_of_edges()} edges (full); "
                  f"{self._nx_ped_graph.number_of_nodes()} nodes (ped-only)")

        except Exception as e:
            print(f"[ERROR] Failed to load walk edges: {e}")
            self.edges = {}
            self.node_to_edges = defaultdict(list)
            self.node_to_ped_edges = defaultdict(list)
            self.main_component_nodes = set()
            self.main_ped_component_nodes = set()
            self.ped_nodes = frozenset()
            self._nx_graph = nx.DiGraph()
            self._nx_ped_graph = nx.DiGraph()

        print(f"Spawning {num_agents} agents on network...")

        _sv_results = PROJECT_ROOT / "Backend" / "Environment" / "output" / "results"
        self._sv_cache = []
        if _sv_results.is_dir():
            for _jf in sorted(_sv_results.glob("*_analysis.json")):
                _m = _re_mod.match(r"^(-?\d+\.\d+)_(-?\d+\.\d+)_analysis\.json$", _jf.name)
                if not _m:
                    continue
                try:
                    _data = _json_mod.loads(_jf.read_text(encoding="utf-8"))
                    _heading = _data.get("metadata", {}).get("heading")
                    self._sv_cache.append({
                        "lat": float(_m.group(1)),
                        "lon": float(_m.group(2)),
                        "scene_analysis": _data.get("scene_analysis", {}),
                        "heading": float(_heading) if _heading is not None else None,
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

        node_keys = list(self.node_to_edges.keys())

        # Import here to avoid circular import at module level
        from model.agent import CityAgent

        for i in range(num_agents):
            try:
                archetype = CityAgent.ARCHETYPES[i % len(CityAgent.ARCHETYPES)]
                spawn_edge_id = random.choice(edge_ids)
                spawn_edge_geom = self.edges[spawn_edge_id]
                spawn_start_point = Point(spawn_edge_geom.coords[0])

                if archetype == "resident":
                    home_key = random.choice(list(self.main_component_nodes))
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
                    elif self.main_component_nodes:
                        fallback_key = random.choice(list(self.main_component_nodes))
                        target_info = {
                            "name": "random_destination", "amenity_type": "unknown",
                            "lon": fallback_key[0], "lat": fallback_key[1],
                            "target_node": fallback_key,
                        }
                    else:
                        target_info = None

                if i == 0:
                    print(f"  Agent 0: lon={spawn_start_point.x:.6f}, lat={spawn_start_point.y:.6f} on edge {spawn_edge_id} [{archetype}] → target: {target_info}")

                agent = CityAgent(
                    model=self,
                    geometry=spawn_start_point,
                    crs="EPSG:4326",
                    edge_id=spawn_edge_id,
                    edge_geom=spawn_edge_geom,
                    archetype=archetype,
                    target_info=target_info,
                    gender=self._agent_gender,
                    age=self._agent_age,
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

        self.current_weather = self._load_weather_snapshot()

    def _load_weather_snapshot(self) -> dict:
        try:
            row = self.con.execute(
                "SELECT condition, temp_c, rain_mm, wind_ms, humidity_pct FROM ext_weather LIMIT 1"
            ).fetchone()
            if row:
                w = {"condition": row[0], "temp_c": row[1], "rain_mm": row[2],
                     "wind_ms": row[3], "humidity_pct": row[4]}
                print(f"[OK] Weather loaded: {w['condition']}, {w['temp_c']:.1f}°C, rain={w['rain_mm']}mm/h")
                return w
        except Exception:
            pass
        return {}

    def get_nearby_external_data(self, point_geom, table: str, radius_deg: float = 0.001) -> list[dict]:
        try:
            rows = self.con.execute(f"""
                SELECT * EXCLUDE geometry,
                       ST_Distance(geometry, ST_GeomFromText('POINT ({point_geom.x} {point_geom.y})')) * 111000 AS dist_m
                FROM {table}
                WHERE ST_DWithin(geometry, ST_GeomFromText('POINT ({point_geom.x} {point_geom.y})'), {radius_deg})
                ORDER BY dist_m
                LIMIT 10
            """).fetchdf()
            return rows.to_dict(orient="records")
        except Exception:
            return []

    def find_connected_edges(self, point):
        point_key = (round(point.x, 6), round(point.y, 6))
        return self.node_to_edges.get(point_key, [])

    def get_streetview_perception(self, point_geom, heading: float | None = None) -> str:
        scene = self.get_nearby_perception(point_geom, heading=heading)
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

    def get_nearby_agents(self, own_id: int, own_geom, snapshot: list, radius_deg: float = 0.0005) -> list[dict]:
        return [
            {"id": uid, "archetype": arch, "dist_m": round(own_geom.distance(geom) * 111000)}
            for uid, geom, arch in snapshot
            if uid != own_id and own_geom.distance(geom) <= radius_deg
        ]

    def get_nearby_amenities(self, point_geom):
        try:
            buffer_deg = 0.001
            query = f"""
            SELECT name, amenity, ST_Distance(geometry, ST_GeomFromText('POINT ({point_geom.x} {point_geom.y})')) as dist_deg
            FROM amenities
            WHERE ST_DWithin(geometry, ST_GeomFromText('POINT ({point_geom.x} {point_geom.y})'), {buffer_deg})
            ORDER BY dist_deg
            LIMIT 20
            """
            results = self.con.execute(query).fetchall()
            return [{"name": str(r[0]) if r[0] else "Unnamed", "type": r[1], "dist": r[2] * 111000} for r in results]
        except Exception as e:
            print(f"Query Error: {e}")
            return []

    def get_nearby_perception(self, point_geom, heading: float | None = None):
        result = None
        try:
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

        _TRACEBACK_MARKERS = ("Cell In[", "Traceback (most", "SyntaxError", "Error:", "[Request interrupted", "line ", "File \"")
        if result:
            scene_ov = result[0] or ""
            if any(m in scene_ov for m in _TRACEBACK_MARKERS):
                result = None
            else:
                return {
                    "scene_overview": scene_ov,
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

        _THRESHOLD_DEG = 0.0015
        best = None
        best_score = _THRESHOLD_DEG * 2

        def _angle_diff(a, b):
            d = abs(a - b) % 360
            return d if d <= 180 else 360 - d

        for entry in self._sv_cache:
            dx = entry["lon"] - point_geom.x
            dy = entry["lat"] - point_geom.y
            dist = (dx * dx + dy * dy) ** 0.5
            if dist > _THRESHOLD_DEG:
                continue
            if heading is not None and entry.get("heading") is not None:
                ang_penalty = _angle_diff(heading, entry["heading"]) / 180.0
                score = dist * (1.0 + 0.4 * ang_penalty)
            else:
                score = dist
            if score < best_score:
                best_score = score
                best = entry

        if best is None:
            return None
        result = dict(best["scene_analysis"])
        if not result.get("scene_overview") and result.get("scene"):
            result["scene_overview"] = result["scene"]
        scene_ov = result.get("scene_overview", "")
        if any(m in scene_ov for m in _TRACEBACK_MARKERS):
            return None
        if best.get("heading") is not None:
            result["heading"] = best["heading"]
        return result

    def _pick_target_for_archetype(self, archetype: str) -> dict | None:
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
        cos_lat = _math.cos(_math.radians(lat))
        if self.routing_mode == "footway" and getattr(self, 'main_ped_component_nodes', None):
            search_space = self.main_ped_component_nodes
        elif self.routing_mode == "footway" and getattr(self, 'ped_nodes', None):
            search_space = self.ped_nodes
        elif getattr(self, 'main_component_nodes', None):
            search_space = self.main_component_nodes
        else:
            search_space = self.node_to_edges.keys()
        best_key, best_dist_sq = None, float("inf")
        for node_key in search_space:
            dx = (node_key[0] - lon) * cos_lat
            dy = node_key[1] - lat
            d = dx * dx + dy * dy
            if d < best_dist_sq:
                best_dist_sq, best_key = d, node_key
        return best_key

    def dijkstra_next_node(self, from_node: tuple, to_node: tuple) -> tuple | None:
        graph = self._nx_ped_graph if self.routing_mode == "footway" else self._nx_graph
        if from_node == to_node or from_node not in graph:
            return None
        try:
            path = nx.dijkstra_path(graph, from_node, to_node, weight="weight")
            return path[1] if len(path) > 1 else None
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    def dijkstra_hops(self, from_node: tuple, to_node: tuple) -> int | None:
        graph = self._nx_ped_graph if self.routing_mode == "footway" else self._nx_graph
        if from_node == to_node:
            return 0
        if from_node not in graph:
            return None
        try:
            return nx.shortest_path_length(graph, from_node, to_node)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    def _compute_proposed_path(self, current_node, destination: dict) -> dict:
        if not current_node or not destination or not destination.get("target_node"):
            return {"nodes": [], "total_distance": 0.0, "created_at_step": self.steps}
        target_node = destination["target_node"]
        if isinstance(target_node, (list, tuple)):
            target_node = (round(float(target_node[0]), 6), round(float(target_node[1]), 6))
        if current_node == target_node:
            return {"nodes": [], "total_distance": 0.0, "created_at_step": self.steps}
        try:
            path = nx.dijkstra_path(self._nx_graph, current_node, target_node, weight="weight")
            total_dist = nx.dijkstra_path_length(self._nx_graph, current_node, target_node, weight="weight")
            return {"nodes": path, "total_distance": total_dist, "created_at_step": self.steps}
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return {"nodes": [], "total_distance": 0.0, "created_at_step": self.steps}

    @property
    def time_of_day(self) -> str:
        idx = (self.steps // _STEPS_PER_PHASE) % len(_TIME_PHASES)
        return _TIME_PHASES[idx]

    def step(self):
        self.steps += 1
        random.shuffle(self.city_agents)
        self._step_snapshot = [(a.unique_id, a.geometry, a.get_archetype()) for a in self.city_agents]
        for agent in self.city_agents:
            agent.step()
        if self.tracker and self.steps % 10 == 0:
            self.tracker.flush()

    def llm_budget_acquire(self) -> bool:
        """Consume one LLM slot for this step. Returns False when budget exhausted (caller should fall back to rule-based)."""
        if self.llm_calls_per_step < 0:   # -1 = unlimited
            return True
        if self.llm_calls_per_step == 0:  # 0 = all rule-based
            return False
        if self.llm_calls_this_step < self.llm_calls_per_step:
            self.llm_calls_this_step += 1
            return True
        return False

    async def async_step(self):
        self.steps += 1
        self.llm_calls_this_step = 0      # reset per-step LLM budget counter
        random.shuffle(self.city_agents)
        agent_snapshot = [(a.unique_id, a.geometry, a.get_archetype()) for a in self.city_agents]

        recorder = self._recorder
        if recorder and recorder.is_recording():
            for agent in self.city_agents:
                try:
                    await agent._async_step(agent_snapshot)
                except Exception:
                    import logging as _log
                    _log.getLogger(__name__).error(f"Agent {agent.unique_id} step failed (recording):", exc_info=True)
                decision_reason = None
                is_fallback = False
                if hasattr(agent, 'memory') and hasattr(agent.memory, 'stream'):
                    try:
                        recent = await agent.memory.stream.get_recent("mobility", n=1)
                        if recent:
                            event = recent[0]
                            decision_reason = event.description
                            meta = getattr(event, 'metadata', None) or {}
                            is_fallback = meta.get('fallback', False)
                    except Exception:
                        pass
                recorder.record_agent_state(
                    agent=agent,
                    step=self.steps,
                    decision_reason=decision_reason,
                    is_fallback=is_fallback,
                )
        else:
            results = await asyncio.gather(
                *[agent._async_step(agent_snapshot) for agent in self.city_agents],
                return_exceptions=True,
            )
            for agent, res in zip(self.city_agents, results):
                if isinstance(res, Exception):
                    import logging as _log
                    _log.getLogger(__name__).error(f"Agent {agent.unique_id} step failed: {res}")

        if self.tracker and self.steps % 10 == 0:
            self.tracker.flush()

    def __del__(self):
        try:
            if self.tracker:
                self.tracker.close()
        except Exception:
            pass
        try:
            self.con.close()
        except Exception:
            pass

    def set_recorder(self, recorder) -> None:
        self._recorder = recorder
        print(f"[OK] Recorder set: {recorder.session_id if recorder else None}")

    def clear_recorder(self) -> None:
        if self._recorder:
            print(f"[OK] Recorder cleared: {self._recorder.session_id}")
        self._recorder = None

    def is_recording(self) -> bool:
        return self._recorder is not None and self._recorder.is_recording
