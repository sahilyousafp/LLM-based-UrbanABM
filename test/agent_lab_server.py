"""
Spatial Cognition Lab — FastAPI server (port 8100).

Tests agent's understanding of spatial parameters by recording episodic
perception diary and comparing LLM-generated narratives with and without
spatial history. Reuses Backend modules unchanged.
"""

import os
import sys
import logging
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# ── 1. Load test env BEFORE importing Backend modules ──────────────────────
TEST_DIR = Path(__file__).parent
PROJECT_ROOT = TEST_DIR.parent

load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(TEST_DIR / ".env.test", override=True)

# ── 2. Make Backend importable ─────────────────────────────────────────────
sys.path.insert(0, str(PROJECT_ROOT / "Backend"))
sys.path.insert(0, str(PROJECT_ROOT / "Backend" / "Agent"))

# ── 3. Local imports ───────────────────────────────────────────────────────
from spatial_memory import PerceptionDiary

# ── 4. Backend imports (unchanged source) ──────────────────────────────────
from fastapi import FastAPI, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import duckdb
from shapely import wkt
from shapely.geometry import Point

from model import CityModel, CityAgent
from agent_tracker import AgentTracker
from geoparquet_recorder import (
    create_recorder,
    get_recorder,
    clear_recorder,
    recover_unmerged_sessions,
)
from datetime import datetime


class FixedAgentTracker(AgentTracker):
    """AgentTracker subclass with corrected INSERT placeholder count."""

    def log_movement(self, agent_id, step_number, longitude, latitude,
                     edge_id=None, position_along_edge=None, speed=None,
                     nearby_amenities_count=0,
                     energy=None, hunger=None, social=None, comfort=None):
        try:
            timestamp = datetime.now()
            self.con.execute(
                """
                INSERT INTO agent_movements
                (movement_id, agent_id, timestamp, step_number, longitude, latitude,
                 geometry, edge_id, position_along_edge, speed, nearby_amenities_count,
                 energy, hunger, social, comfort)
                VALUES (
                    (SELECT COALESCE(MAX(movement_id), 0) + 1 FROM agent_movements),
                    ?, ?, ?, ?, ?,
                    ST_Point(?, ?),
                    ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [agent_id, timestamp, step_number, longitude, latitude,
                 longitude, latitude, edge_id, position_along_edge, speed,
                 nearby_amenities_count, energy, hunger, social, comfort],
            )
        except Exception as e:
            logging.getLogger("agent_tracker").error(f"Failed to log movement: {e}")

logger = logging.getLogger(__name__)

# ── 5. Paths ───────────────────────────────────────────────────────────────
DB_PATH = PROJECT_ROOT / "Backend" / "Environment" / "eixample_overture.duckdb"
SV_OUTPUT_DIR = PROJECT_ROOT / "Backend" / "Environment" / "output"
SV_IMAGES_DIR = SV_OUTPUT_DIR / "images"
SV_RESULTS_DIR = SV_OUTPUT_DIR / "results"

TEST_TRACKER_DB = TEST_DIR / "tracking_data" / "agent_lab.duckdb"
TEST_RECORDING_DIR = TEST_DIR / "tracking_data"
TEST_TRACKER_DB.parent.mkdir(parents=True, exist_ok=True)

# ── 6. Bootstrap CityModel ─────────────────────────────────────────────────
print("=" * 70)
print("Spatial Cognition Lab — bootstrapping…")
print("=" * 70)

city_model = CityModel(num_agents=0, spawn_seed=int(os.getenv("SPAWN_SEED", "42")))
perception_diary = PerceptionDiary(
    max_entries=int(os.getenv("SPATIAL_MEMORY_DEPTH", "50"))
)

try:
    if city_model.tracker is not None:
        city_model.tracker.close()
except Exception as e:
    print(f"[WARN] Closing default tracker: {e}")

try:
    city_model.tracker = FixedAgentTracker(db_path=TEST_TRACKER_DB)
    print(f"[OK] Test tracker DB: {TEST_TRACKER_DB}")
except Exception:
    city_model.tracker = None
    print(f"[WARN] Skipped test tracker; perception diary will record all data.")
print(f"[OK] PerceptionDiary ready (max_entries={perception_diary.max_entries})")
print("=" * 70)

# ── 7. FastAPI app ─────────────────────────────────────────────────────────
app = FastAPI(title="Spatial Cognition Lab")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db_connection():
    con = duckdb.connect(str(DB_PATH), read_only=True)
    con.install_extension("spatial")
    con.load_extension("spatial")
    return con


# ── 8. Health & frontend config ────────────────────────────────────────────
@app.get("/")
async def read_root():
    return {
        "status": "running",
        "service": "spatial-cognition-lab",
        "port": 8100,
        "agent_count": len(city_model.city_agents),
        "perception_mode": getattr(city_model, "perception_mode", "both"),
        "diary_entries": len(perception_diary.entries),
        "llm": {
            "provider": os.environ.get("LLM_PROVIDER"),
            "model": os.environ.get("LLM_MODEL"),
            "locked": True,
        },
    }


@app.get("/api/config/frontend")
async def get_frontend_config():
    return {
        "mapbox_token": os.environ.get("MAPBOX_TOKEN", ""),
        "llm_provider": os.environ.get("LLM_PROVIDER", "ollama"),
        "llm_model": os.environ.get("LLM_MODEL", "qwen2.5-coder:3b"),
        "llm_locked": True,
        "perception_mode": getattr(city_model, "perception_mode", "both"),
        "archetypes": list(CityAgent.ARCHETYPES),
    }


# ── 9. Spatial data endpoints ──────────────────────────────────────────────
@app.get("/api/buildings")
async def get_buildings():
    con = get_db_connection()
    try:
        rows = con.execute("SELECT ST_AsText(geometry) as wkt FROM buildings").fetchall()
        features = []
        for row in rows:
            try:
                geom = wkt.loads(row[0])
                if geom.geom_type == "Polygon":
                    features.append({
                        "type": "Feature",
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[list(c) for c in geom.exterior.coords]],
                        },
                        "properties": {"layer": "buildings"},
                    })
            except Exception:
                continue
        return {"type": "FeatureCollection", "features": features}
    finally:
        con.close()


@app.get("/api/walk_network")
async def get_walk_network():
    con = get_db_connection()
    try:
        rows = con.execute("SELECT ST_AsText(geometry) as wkt FROM walk_edges").fetchall()
        features = []
        for row in rows:
            try:
                geom = wkt.loads(row[0])
                features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [list(c) for c in geom.coords],
                    },
                    "properties": {"layer": "walk_network"},
                })
            except Exception:
                continue
        return {"type": "FeatureCollection", "features": features}
    finally:
        con.close()


@app.get("/api/amenities")
async def get_amenities():
    con = get_db_connection()
    try:
        rows = con.execute(
            "SELECT name, amenity, ST_AsText(geometry) as wkt FROM amenities"
        ).fetchall()
        features = []
        for row in rows:
            try:
                geom = wkt.loads(row[2])
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [geom.x, geom.y]},
                    "properties": {
                        "name": str(row[0]) if row[0] else "Unnamed",
                        "amenity": row[1],
                        "layer": "amenities",
                    },
                })
            except Exception:
                continue
        return {"type": "FeatureCollection", "features": features}
    finally:
        con.close()


@app.get("/api/streetview_grid")
async def get_streetview_grid():
    import json as json_lib
    import re

    features = []
    if not SV_RESULTS_DIR.is_dir():
        return {"type": "FeatureCollection", "features": []}

    for json_file in sorted(SV_RESULTS_DIR.glob("*_analysis.json")):
        m = re.match(r"^(-?\d+\.\d+)_(-?\d+\.\d+)_analysis\.json$", json_file.name)
        if not m:
            continue
        lat, lon = float(m.group(1)), float(m.group(2))
        try:
            data = json_lib.loads(json_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        scene = data.get("scene_analysis") or {}
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "lat": lat,
                "lon": lon,
                "scene_overview": scene.get("scene_overview", ""),
                "pedestrian_activity": scene.get("pedestrian_activity", ""),
                "vegetation": scene.get("vegetation", ""),
            },
        })
    return {"type": "FeatureCollection", "features": features}


@app.get("/api/streetview_grid/image/{filename}")
async def get_streetview_image(filename: str):
    import mimetypes
    file_path = SV_RESULTS_DIR / filename
    if not file_path.exists():
        return {"error": "Image not found"}
    content_type, _ = mimetypes.guess_type(str(file_path))
    return FileResponse(str(file_path), media_type=content_type or "image/jpeg")


# ── 10. Single-agent lifecycle ─────────────────────────────────────────────
@app.post("/api/single-agent/configure")
async def configure_single_agent(payload: dict = Body(...)):
    """Create a single test agent. Body: {start_lon, start_lat, target_lon, target_lat, archetype}"""
    required = ("start_lon", "start_lat", "target_lon", "target_lat", "archetype")
    for k in required:
        if k not in payload:
            return {"error": f"Missing field: {k}"}

    try:
        start_lon = float(payload["start_lon"])
        start_lat = float(payload["start_lat"])
        target_lon = float(payload["target_lon"])
        target_lat = float(payload["target_lat"])
        archetype = str(payload["archetype"])
    except (TypeError, ValueError) as e:
        return {"error": f"Invalid payload types: {e}"}

    if archetype not in CityAgent.ARCHETYPES:
        return {
            "error": f"Unknown archetype '{archetype}'",
            "allowed": list(CityAgent.ARCHETYPES),
        }

    # Reset agent and diary
    city_model.city_agents = []
    city_model.steps = 0
    perception_diary.entries.clear()
    perception_diary.adherence_log.clear()
    perception_diary._visited_amenities_set.clear()
    perception_diary._visited_amenities_list.clear()

    # Snap to network
    start_node = city_model._find_nearest_node(start_lon, start_lat)
    target_node = city_model._find_nearest_node(target_lon, target_lat)
    if start_node is None or target_node is None:
        return {"error": "Could not snap to walk network"}

    if start_node == target_node:
        return {"error": "Start and target are the same node"}

    edges_at_start = city_model.node_to_edges.get(start_node, [])
    if not edges_at_start:
        return {"error": "No edges at snapped start node"}

    # Check Dijkstra reachability
    reachability_check = city_model.dijkstra_next_node(start_node, target_node)
    if reachability_check is None:
        # Try to find nearest reachable node to target
        import heapq
        visited = set()
        heap = [(0.0, start_node)]
        while heap:
            d, u = heapq.heappop(heap)
            if u in visited:
                continue
            visited.add(u)
            for _eid, geom, direction in city_model.node_to_edges.get(u, []):
                if direction == "forward":
                    v = (round(geom.coords[-1][0], 6), round(geom.coords[-1][1], 6))
                else:
                    v = (round(geom.coords[0][0], 6), round(geom.coords[0][1], 6))
                if v not in visited:
                    heapq.heappush(heap, (d + geom.length, v))

        # Find closest reachable node to original target
        closest_reachable = None
        closest_dist = float("inf")
        for node in visited:
            dx = node[0] - target_node[0]
            dy = node[1] - target_node[1]
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < closest_dist:
                closest_dist = dist
                closest_reachable = node

        dist_m = closest_dist * 111000
        return {
            "error": f"Target is on an isolated network segment. "
                     f"Your click snapped to {target_node}, but only {len(visited)} nodes "
                     f"are reachable from start. Nearest reachable node is {closest_reachable} "
                     f"({dist_m:.0f}m away). Try clicking within the connected walk network "
                     f"(main streets like Passeig de Gracia, Carrer d'Arago, etc.)."
        }

    forward_edges = [e for e in edges_at_start if e[2] == "forward"]
    chosen = forward_edges[0] if forward_edges else edges_at_start[0]
    edge_id, edge_geom, direction = chosen
    # Pre-flip reverse edges so coords[-1] in _init_memory_sync is always the destination node
    if direction == "reverse":
        from shapely.geometry import LineString
        edge_geom = LineString(list(edge_geom.coords)[::-1])
    start_point = Point(edge_geom.coords[0])

    # Find archetype-aligned amenity near the target node (test-only)
    ARCHETYPE_AMENITY_MAP = {
        "commuter": ["bus_station", "train_station", "subway_entrance"],
        "tourist":  ["attraction", "museum", "cafe", "restaurant"],
        "student":  ["library", "university", "cafe", "fast_food"],
        "resident": ["supermarket", "pharmacy", "park", "bakery"],
    }
    target_amenity_types = ARCHETYPE_AMENITY_MAP.get(archetype, [])
    target_lon_final = float(target_node[0])
    target_lat_final = float(target_node[1])
    target_name = "user_target"
    target_amenity_type = "user_pin"

    if target_amenity_types:
        try:
            con = get_db_connection()
            placeholders = ", ".join(["?"] * len(target_amenity_types))
            query = f"""
            SELECT name, amenity, ST_X(geometry) as lon, ST_Y(geometry) as lat,
                   ST_Distance(geometry, ST_Point(?, ?)) as dist_deg
            FROM amenities
            WHERE amenity IN ({placeholders})
              AND ST_DWithin(geometry, ST_Point(?, ?), 0.002)
            ORDER BY dist_deg
            LIMIT 1
            """
            params = [target_node[0], target_node[1]] + target_amenity_types + [target_node[0], target_node[1]]
            row = con.execute(query, params).fetchone()
            con.close()
            if row:
                amenity_lon = float(row[2])
                amenity_lat = float(row[3])
                # Re-snap target_node to nearest network node of the amenity
                amenity_snapped = city_model._find_nearest_node(amenity_lon, amenity_lat)
                if amenity_snapped:
                    # CRITICAL: Re-check reachability after amenity snapping
                    # The amenity might be on a different connected component
                    reach_check = city_model.dijkstra_next_node(start_node, amenity_snapped)
                    if reach_check is not None:
                        # Amenity is reachable — use it
                        target_lon_final = amenity_lon
                        target_lat_final = amenity_lat
                        target_name = str(row[0]) if row[0] else "Unnamed"
                        target_amenity_type = str(row[1])
                        target_node = amenity_snapped
                    else:
                        # Amenity unreachable — fall back to original click target
                        logger.info(
                            f"Amenity '{row[0]}' at ({amenity_lon:.6f}, {amenity_lat:.6f}) "
                            f"snapped to unreachable node {amenity_snapped}. "
                            f"Falling back to original target {target_node}."
                        )
        except Exception as e:
            logger.warning(f"Failed to find archetype-aligned amenity: {e}")

    target_info = {
        "name": target_name,
        "amenity_type": target_amenity_type,
        "lon": target_lon_final,
        "lat": target_lat_final,
        "target_node": target_node,
    }

    agent = CityAgent(
        model=city_model,
        geometry=start_point,
        crs="EPSG:4326",
        edge_id=int(edge_id),
        edge_geom=edge_geom,
        archetype=archetype,
        target_info=target_info,
    )
    city_model.city_agents.append(agent)

    return {
        "status": "configured",
        "agent_id": agent.unique_id,
        "archetype": archetype,
        "start": {"lon": start_point.x, "lat": start_point.y},
        "target": {"lon": target_info["lon"], "lat": target_info["lat"]},
        "perception_mode": getattr(city_model, "perception_mode", "both"),
    }


@app.post("/api/single-agent/reset")
async def reset_single_agent():
    """Drop the agent and clear the diary."""
    city_model.city_agents = []
    city_model.steps = 0
    perception_diary.entries.clear()
    perception_diary.adherence_log.clear()
    perception_diary._visited_amenities_set.clear()
    perception_diary._visited_amenities_list.clear()
    return {"status": "reset"}


# ── 11. Stepping (with diary recording) ────────────────────────────────────
@app.post("/api/step_continuous")
async def step_continuous():
    if not city_model.city_agents:
        return {"step": city_model.steps, "agents": [], "error": "No agent configured"}

    await city_model.async_step()

    # Record to perception diary
    for agent in city_model.city_agents:
        perception = city_model.get_nearby_perception(agent.geometry)
        nearby = agent.nearby_amenities or []
        needs = await agent.memory.status.get("needs", {})
        position = await agent.memory.status.get("position", {})
        edge_id = position.get("edge_id", "unknown")

        perception_diary.record(
            step=city_model.steps,
            edge_id=str(edge_id),
            position=(agent.geometry.x, agent.geometry.y),
            perception=perception,
            nearby_amenities=nearby,
            needs=needs,
        )

        # Fix visited_amenities — write to agent memory
        visited = await agent.memory.status.get("visited_amenities", [])
        for amenity in nearby:
            if not any(v.get("name") == amenity.get("name") for v in visited):
                amenity_copy = dict(amenity)
                amenity_copy["first_seen_step"] = city_model.steps
                visited.append(amenity_copy)
        await agent.memory.status.update("visited_amenities", visited)

        # Record path adherence — read on_proposed_path from current_plan (set by mobility_block)
        plan = await agent.memory.status.get("current_plan", {})
        on_path = plan.get("on_proposed_path", False)
        perception_diary.record_adherence(
            step=city_model.steps,
            followed=on_path,
            chosen_edge=str(edge_id),
            dijkstra_edge=str(plan.get("target_edge_id", "none")),
        )

    agents_data = []
    for agent in city_model.city_agents:
        needs = await agent.memory.status.get("needs", {})
        agents_data.append({
            "id": agent.unique_id,
            "lon": agent.geometry.x,
            "lat": agent.geometry.y,
            "nearby_count": len(agent.nearby_amenities),
            "needs": needs,
        })

    return {
        "step": city_model.steps,
        "agents": agents_data,
        "diary_entries": len(perception_diary.entries),
        "llm_stats": city_model.llm_client.stats(),
    }


# ── 12. Per-agent endpoints ────────────────────────────────────────────────
@app.get("/api/agents")
async def get_agents():
    features = []
    for agent in city_model.city_agents:
        archetype = "unknown"
        try:
            profile = await agent.memory.status.get("agent_profile", {})
            archetype = profile.get("archetype", "unknown")
        except Exception:
            pass
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [agent.geometry.x, agent.geometry.y]},
            "properties": {
                "id": agent.unique_id,
                "archetype": archetype,
                "nearby_count": len(agent.nearby_amenities),
            },
        })
    return {"type": "FeatureCollection", "features": features}


def _find_agent(agent_id: int):
    return next((a for a in city_model.city_agents if a.unique_id == agent_id), None)


@app.get("/api/agent/{agent_id}")
async def get_agent_info(agent_id: int):
    agent = _find_agent(agent_id)
    if not agent:
        return {"error": "Agent not found"}
    return {
        "id": agent.unique_id,
        "location": {"lon": agent.geometry.x, "lat": agent.geometry.y},
        "nearby_amenities": agent.nearby_amenities,
        "street_perception": agent.street_perception,
    }


@app.get("/api/agent/{agent_id}/memory")
async def get_agent_memory(agent_id: int):
    agent = _find_agent(agent_id)
    if not agent:
        return {"error": "Agent not found"}
    return await agent.memory.snapshot()


@app.get("/api/agent/{agent_id}/stream")
async def get_agent_stream(agent_id: int, topic: str = "", n: int = 20):
    agent = _find_agent(agent_id)
    if not agent:
        return {"error": "Agent not found"}
    if topic:
        nodes = await agent.memory.stream.get_recent(topic, n=n)
    else:
        nodes = await agent.memory.stream.get_recent_all(n=n)
    return {
        "agent_id": agent_id,
        "topic": topic or "all",
        "events": [
            {"step": nd.step, "topic": nd.topic, "description": nd.description, "metadata": nd.metadata}
            for nd in nodes
        ],
    }


@app.get("/api/agent/{agent_id}/cognition")
async def get_agent_cognition(agent_id: int):
    agent = _find_agent(agent_id)
    if not agent:
        return {"error": "Agent not found"}
    cognition = await agent.memory.status.get("cognition_state", {})
    needs = await agent.memory.status.get("needs", {})
    profile = await agent.memory.status.get("agent_profile", {})
    destination = await agent.memory.status.get("destination", {})
    return {
        "agent_id": agent_id,
        "archetype": profile.get("archetype", "unknown"),
        "cognition_state": cognition,
        "needs": needs,
        "destination": destination,
    }


@app.get("/api/agent/{agent_id}/perception-text")
async def get_agent_perception_text(agent_id: int):
    """Return perception fields and nearby streetview image."""
    import re

    agent = _find_agent(agent_id)
    if not agent:
        return {"error": "Agent not found"}

    if not agent.street_perception:
        return {
            "agent_id": agent_id,
            "perception": {},
            "image_url": None,
        }

    perception = agent.street_perception
    image_url = None
    agent_lon, agent_lat = agent.geometry.x, agent.geometry.y

    if SV_RESULTS_DIR.is_dir():
        closest_distance = float('inf')
        closest_file = None
        for json_file in SV_RESULTS_DIR.glob("*_analysis.json"):
            m = re.match(r"^(-?\d+\.\d+)_(-?\d+\.\d+)_analysis\.json$", json_file.name)
            if not m:
                continue
            lat, lon = float(m.group(1)), float(m.group(2))
            dist = ((lon - agent_lon) ** 2 + (lat - agent_lat) ** 2) ** 0.5
            if dist < closest_distance:
                closest_distance = dist
                closest_file = json_file.name.replace("_analysis.json", "")

        if closest_file and closest_distance < 0.01:
            for ext in ['.jpg', '.jpeg', '.png']:
                img_path = SV_RESULTS_DIR / f"{closest_file}{ext}"
                if img_path.exists():
                    image_url = f"/api/streetview_grid/image/{closest_file}{ext}"
                    break

    return {
        "agent_id": agent_id,
        "perception": {
            "scene_overview": perception.get("scene_overview", ""),
            "buildings": perception.get("buildings", ""),
            "vegetation": perception.get("vegetation", ""),
            "pedestrian_activity": perception.get("pedestrian_activity", ""),
            "lighting_atmosphere": perception.get("lighting_atmosphere", ""),
            "visual_barriers": perception.get("visual_barriers", ""),
            "sightlines": perception.get("sightlines", ""),
        },
        "image_url": image_url,
        "nearby_amenities": [
            {"type": a.get("type", "?"), "name": a.get("name", "?"), "distance_m": a.get("distance_m", 0)}
            for a in agent.nearby_amenities[:10]
        ]
    }


@app.get("/api/agent/{agent_id}/planned-path")
async def get_agent_planned_path(agent_id: int):
    """Return Dijkstra path as GeoJSON LineString (current shortest path to target)."""
    agent = _find_agent(agent_id)
    if not agent:
        return {"error": "Agent not found"}

    target_info = await agent.memory.status.get("destination", {})
    if not target_info or not target_info.get("target_node"):
        return {"agent_id": agent_id, "path": None}

    try:
        position = await agent.memory.status.get("position", {})
        current_node = position.get("current_node")
        if not current_node:
            return {"agent_id": agent_id, "path": None}
        current_node = (round(current_node[0], 6), round(current_node[1], 6))

        raw_target = target_info.get("target_node")
        target_node = (round(float(raw_target[0]), 6), round(float(raw_target[1]), 6))

        path_nodes = [current_node]
        current = current_node
        max_iterations = 1000

        while current != target_node and len(path_nodes) < max_iterations:
            next_node = city_model.dijkstra_next_node(current, target_node)
            if next_node is None:
                logger.warning(
                    f"[planned-path] Dijkstra blocked at node {current} "
                    f"after {len(path_nodes)} hops toward target {target_node}"
                )
                break
            path_nodes.append(next_node)
            current = next_node

        if len(path_nodes) < 2:
            return {"agent_id": agent_id, "path": None}

        coords = []
        for i in range(len(path_nodes) - 1):
            u, v = path_nodes[i], path_nodes[i + 1]
            for edge_id, edge_geom, direction in city_model.node_to_edges.get(u, []):
                if direction == "forward":
                    end_node = edge_geom.coords[-1]
                else:
                    end_node = edge_geom.coords[0]
                end_node_key = (round(end_node[0], 6), round(end_node[1], 6))

                if end_node_key == v:
                    if direction == "forward":
                        edge_coords = list(edge_geom.coords)
                    else:
                        edge_coords = list(edge_geom.coords)[::-1]

                    if i == 0:
                        coords.extend([(lon, lat) for lon, lat in edge_coords])
                    else:
                        coords.extend([(lon, lat) for lon, lat in edge_coords[1:]])
                    break

        if len(coords) < 2:
            return {"agent_id": agent_id, "path": None}

        return {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "num_nodes": len(path_nodes),
                "agent_id": agent_id,
                "start_node": list(current_node),
                "end_node": list(target_node),
            },
        }
    except Exception as e:
        logger.error(f"Planned path error: {e}")
        return {"agent_id": agent_id, "path": None, "error": str(e)}


@app.get("/api/agent/{agent_id}/proposed-path")
async def get_agent_proposed_path(agent_id: int):
    """Return the originally proposed path and current shortest path side-by-side.

    The proposed path is the Dijkstra route computed at spawn/target-set time.
    The current path is the shortest path from the agent's current position.
    Deviations from the proposed path reveal agent decision-making behavior.
    """
    agent = _find_agent(agent_id)
    if not agent:
        return {"error": "Agent not found"}

    try:
        proposed = await agent.memory.status.get("proposed_path", {})
        destination = await agent.memory.status.get("destination", {})
        position = await agent.memory.status.get("position", {})
        current_plan = await agent.memory.status.get("current_plan", {})

        result = {
            "agent_id": agent_id,
            "proposed_path": proposed,
            "destination": destination,
            "current_plan": current_plan,
            "shortest_path": None,
        }

        # Also compute current shortest path for comparison
        current_node = position.get("current_node")
        target_node = destination.get("target_node") if destination else None
        if current_node and target_node:
            if isinstance(target_node, (list, tuple)):
                target_node = (round(float(target_node[0]), 6), round(float(target_node[1]), 6))
            if current_node != target_node:
                path_nodes = [current_node]
                current = current_node
                for _ in range(1000):
                    next_node = city_model.dijkstra_next_node(current, target_node)
                    if next_node is None:
                        break
                    path_nodes.append(next_node)
                    current = next_node
                    if current == target_node:
                        break

                if len(path_nodes) >= 2:
                    coords = []
                    for i in range(len(path_nodes) - 1):
                        u, v = path_nodes[i], path_nodes[i + 1]
                        for edge_id, edge_geom, direction in city_model.node_to_edges.get(u, []):
                            end = edge_geom.coords[-1] if direction == "forward" else edge_geom.coords[0]
                            end_key = (round(end[0], 6), round(end[1], 6))
                            if end_key == v:
                                edge_coords = list(edge_geom.coords) if direction == "forward" else list(edge_geom.coords)[::-1]
                                if i == 0:
                                    coords.extend([(lon, lat) for lon, lat in edge_coords])
                                else:
                                    coords.extend([(lon, lat) for lon, lat in edge_coords[1:]])
                                break

                    if len(coords) >= 2:
                        result["shortest_path"] = {
                            "type": "Feature",
                            "geometry": {"type": "LineString", "coordinates": coords},
                            "properties": {"num_nodes": len(path_nodes)},
                        }

        return result
    except Exception as e:
        logger.error(f"Proposed path error: {e}")
        return {"agent_id": agent_id, "error": str(e)}


# ── 13. NEW: Perception Diary endpoints ────────────────────────────────────
@app.get("/api/agent/{agent_id}/perception-diary")
async def get_perception_diary(agent_id: int):
    """Return the full episodic diary timeline."""
    agent = _find_agent(agent_id)
    if not agent:
        return {"error": "Agent not found"}
    return {
        "agent_id": agent_id,
        "entries": perception_diary.get_timeline(),
        "stats": perception_diary.get_spatial_stats(),
    }


@app.get("/api/agent/{agent_id}/spatial-stats")
async def get_spatial_stats(agent_id: int):
    """Return aggregated diary statistics."""
    agent = _find_agent(agent_id)
    if not agent:
        return {"error": "Agent not found"}
    return {
        "agent_id": agent_id,
        "stats": perception_diary.get_spatial_stats(),
        "adherence": perception_diary.get_adherence_stats(),
    }


@app.get("/api/agent/{agent_id}/memory-audit")
async def get_memory_audit(agent_id: int):
    """Return complete memory audit."""
    agent = _find_agent(agent_id)
    if not agent:
        return {"error": "Agent not found"}

    visited_edges = await agent.memory.status.get("visited_edges", {})
    return {
        "agent_id": agent_id,
        "diary": perception_diary.get_memory_audit(),
        "agent_visited_edges": visited_edges,
        "agent_visited_amenities": await agent.memory.status.get("visited_amenities", []),
    }


# ── 14. NEW: Narrative endpoints (with and without history) ───────────────
@app.get("/api/agent/{agent_id}/narrative")
async def get_agent_narrative(agent_id: int, include_history: bool = True):
    """Generate narrative, optionally with perception diary history."""
    agent = _find_agent(agent_id)
    if not agent:
        return {"error": "Agent not found"}

    try:
        profile = await agent.memory.status.get("agent_profile", {})
        needs = await agent.memory.status.get("needs", {})
        cognition = await agent.memory.status.get("cognition_state", {})

        perception_ctx = ""
        if agent.street_perception:
            sp = agent.street_perception
            scene_parts = [
                sp.get(k, "")
                for k in ("scene_overview", "vegetation", "pedestrian_activity", "lighting_atmosphere")
                if sp.get(k, "") and sp.get(k, "").strip().lower() != "unknown"
            ]
            if scene_parts:
                perception_ctx = " Street scene: " + " ".join(scene_parts[:2])

        amenities_list = ", ".join(a.get("type", "?") for a in agent.nearby_amenities[:5]) or "nothing notable"

        if include_history and perception_diary.entries:
            history_text = perception_diary.get_history_prompt_text(last_n=10)
            visited_amenities = perception_diary.get_visited_amenities()
            visited_str = ", ".join(f"{a.get('name')} ({a.get('type')})" for a in visited_amenities[:5]) or "none yet"

            user_msg = (
                f"Agent {agent_id} is a {profile.get('archetype', 'pedestrian')}. "
                f"\n\nJOURNEY SO FAR:\n{history_text}\n\n"
                f"AMENITIES ENCOUNTERED: {visited_str}\n\n"
                f"CURRENT SCENE: {agent.street_perception.get('scene_overview', 'unknown')}\n"
                f"CURRENT NEEDS: energy={needs.get('energy', 1.0):.2f}, comfort={needs.get('comfort', 0.5):.2f}\n"
                f"MOOD: {cognition.get('mood', 'neutral')}\n\n"
                f"Narrate what this agent is experiencing, referencing specific places from their journey. Be specific, not generic. (2-3 sentences)"
            )
        else:
            user_msg = (
                f"Agent {agent_id} is a {profile.get('archetype', 'pedestrian')} at "
                f"lon={agent.geometry.x:.5f}, lat={agent.geometry.y:.5f}. "
                f"Needs: energy={needs.get('energy', 1.0):.2f}, comfort={needs.get('comfort', 0.5):.2f}. "
                f"Mood: {cognition.get('mood', 'neutral')}. "
                f"Nearby: {amenities_list}.{perception_ctx} "
                f"Narrate what this agent is experiencing right now. (2-3 sentences)"
            )

        messages = [
            {"role": "system", "content": "You are narrating an urban simulation agent in Barcelona Eixample. Be concise and specific."},
            {"role": "user", "content": user_msg},
        ]

        narrative = await city_model.llm_client.chat(messages)
        return {
            "agent_id": agent_id,
            "include_history": include_history,
            "narrative": narrative,
            "location": {"lon": agent.geometry.x, "lat": agent.geometry.y},
            "archetype": profile.get("archetype", "unknown"),
        }
    except Exception as e:
        logger.error(f"Narrative error: {e}")
        return {"agent_id": agent_id, "error": str(e)}


@app.get("/api/agent/{agent_id}/narrative-compare")
async def get_narrative_comparison(agent_id: int):
    """Return both generic and history-aware narratives side-by-side."""
    agent = _find_agent(agent_id)
    if not agent:
        return {"error": "Agent not found"}

    try:
        # Get both versions
        generic_resp = await get_agent_narrative(agent_id, include_history=False)
        history_resp = await get_agent_narrative(agent_id, include_history=True)

        generic_narrative = generic_resp.get("narrative", "")
        history_narrative = history_resp.get("narrative", "")

        return {
            "agent_id": agent_id,
            "generic": generic_narrative,
            "history_aware": history_narrative,
            "diary_entries": len(perception_diary.entries),
            "visited_amenities": len(perception_diary.get_visited_amenities()),
        }
    except Exception as e:
        logger.error(f"Narrative comparison error: {e}")
        return {"agent_id": agent_id, "error": str(e)}


@app.get("/api/agent/{agent_id}/path-adherence")
async def get_path_adherence(agent_id: int):
    """Return path adherence statistics and log."""
    agent = _find_agent(agent_id)
    if not agent:
        return {"error": "Agent not found"}

    return {
        "agent_id": agent_id,
        "adherence": perception_diary.get_adherence_stats(),
        "log": perception_diary.get_adherence_log(),
    }


# ── 15. Perception mode & LLM stats ────────────────────────────────────────
@app.post("/api/config/perception-mode")
async def update_perception_mode(mode: str = Body(..., embed=True)):
    if mode not in ("amenities", "perception", "both", "rule_based"):
        return {"error": "Invalid mode"}
    city_model.perception_mode = mode
    return {"status": "updated", "mode": mode}


@app.get("/api/config/perception-mode")
async def get_perception_mode():
    return {"mode": getattr(city_model, "perception_mode", "both")}


@app.get("/api/llm/stats")
async def get_llm_stats():
    return city_model.llm_client.stats()


# ── 16. Trail (movement history) ───────────────────────────────────────────
@app.get("/api/single-agent/path")
async def get_single_agent_path():
    if not city_model.city_agents:
        return {"type": "FeatureCollection", "features": []}
    agent_id = city_model.city_agents[0].unique_id
    try:
        rows = city_model.tracker.con.execute(
            "SELECT longitude, latitude FROM agent_movements WHERE agent_id = ? ORDER BY step_number, movement_id",
            [agent_id],
        ).fetchall()
    except Exception as e:
        return {"type": "FeatureCollection", "features": [], "error": str(e)}

    if len(rows) < 1:
        return {"type": "FeatureCollection", "features": []}

    # Deduplicate consecutive identical coordinates
    deduped = [rows[0]]
    for r in rows[1:]:
        if r[0] != deduped[-1][0] or r[1] != deduped[-1][1]:
            deduped.append(r)

    coords = [[r[0], r[1]] for r in deduped]
    if len(coords) == 1:
        return {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": coords[0]},
                "properties": {"agent_id": agent_id},
            }],
        }
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"agent_id": agent_id},
        }],
    }


# ── 17. Recording (GeoParquet) ─────────────────────────────────────────────
@app.post("/api/recording/start")
async def start_recording(
    session_name: Optional[str] = None,
    include_thoughts: bool = True,
    include_perception: bool = True,
):
    recover_unmerged_sessions(TEST_RECORDING_DIR)
    clear_recorder()
    current_mode = getattr(city_model, "perception_mode", "both")
    recorder = create_recorder(
        output_dir=TEST_RECORDING_DIR,
        max_buffer_size=5000,
        include_thoughts=include_thoughts,
        include_perception=include_perception,
        perception_mode=current_mode,
    )
    session_id = recorder.start_recording(session_name or "agent_lab")
    city_model.set_recorder(recorder)
    return {
        "status": "recording_started",
        "session_id": session_id,
        "session_name": session_name or "agent_lab",
        "perception_mode": current_mode,
    }


@app.post("/api/recording/stop")
async def stop_recording():
    recorder = get_recorder()
    if not recorder or not recorder.is_recording:
        return {"status": "no_recording"}
    city_model.clear_recorder()
    file_path = recorder.stop_recording()
    if not file_path:
        return {"status": "error"}
    status = recorder.get_status()
    return {
        "status": "recording_stopped",
        "file_path": str(file_path),
        "file_name": file_path.name,
        "total_records": status["total_records"],
    }


@app.get("/api/recording/status")
async def get_recording_status():
    recorder = get_recorder()
    if not recorder:
        return {"is_recording": False}
    status = recorder.get_status()
    return {
        "is_recording": status["is_recording"],
        "session_id": status["session_id"],
    }


@app.get("/api/recording/download/{filename:path}")
async def download_recording(filename: str):
    file_path = TEST_RECORDING_DIR / filename
    if not file_path.exists():
        return {"error": "File not found"}
    return FileResponse(
        str(file_path), media_type="application/octet-stream", filename=file_path.name
    )


# ── 18. Test endpoints ─────────────────────────────────────────────────────
@app.post("/api/test/dijkstra")
async def test_dijkstra(payload: dict = Body(...)):
    """Test Dijkstra pathfinding between two nodes."""
    node1 = tuple(payload["node1"])
    node2 = tuple(payload["node2"])

    result = {
        "node1": node1,
        "node2": node2,
        "node1_in_graph": node1 in city_model.node_to_edges,
        "node2_in_graph": node2 in city_model.node_to_edges,
        "next_node": None,
    }

    if result["node1_in_graph"] and result["node2_in_graph"]:
        result["next_node"] = city_model.dijkstra_next_node(node1, node2)

    return result


@app.get("/api/reachable-area")
async def get_reachable_area(lon: float, lat: float, max_nodes: int = 500):
    """Return nodes reachable from a point, for visualization."""
    import heapq
    start_node = city_model._find_nearest_node(lon, lat)
    if start_node is None:
        return {"error": "Could not snap to network"}

    visited = set()
    heap = [(0.0, start_node)]
    while heap and len(visited) < max_nodes:
        d, u = heapq.heappop(heap)
        if u in visited:
            continue
        visited.add(u)
        for _eid, geom, direction in city_model.node_to_edges.get(u, []):
            if direction == "forward":
                v = (round(geom.coords[-1][0], 6), round(geom.coords[-1][1], 6))
            else:
                v = (round(geom.coords[0][0], 6), round(geom.coords[0][1], 6))
            if v not in visited:
                heapq.heappush(heap, (d + geom.length, v))

    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [n[0], n[1]]},
            "properties": {"reachable": True},
        }
        for n in visited
    ]

    return {
        "type": "FeatureCollection",
        "features": features,
        "total_reachable": len(visited),
        "start_node": list(start_node),
    }


# ── 19. Boot ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    print("Booting Uvicorn on http://127.0.0.1:8100 ...")
    uvicorn.run(app, host="127.0.0.1", port=8100, reload=False)
