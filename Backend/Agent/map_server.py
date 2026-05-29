import json
import logging
import os
import shutil
import sys
import uuid
import threading
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Body, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import duckdb
import numpy as np
import pyproj
from shapely import wkt
from shapely.geometry import Point, LineString

logger = logging.getLogger(__name__)

# Load .env from project root before anything else
PROJECT_ROOT = Path(__file__).parent.parent.parent
FRONTEND_DIR = PROJECT_ROOT / "Frontend"
load_dotenv(PROJECT_ROOT / ".env")

from model import CityModel, CityAgent
from geoparquet_recorder import create_recorder, get_recorder, clear_recorder, recover_unmerged_sessions

# Import Overture pipeline for map data downloads
_env_dir = Path(__file__).parent.parent / "Environment"
sys.path.insert(0, str(_env_dir))
from overture_to_duckdb import OverturePipeline

# Import external data plugin registry
_plugins_dir = _env_dir / "plugins"
sys.path.insert(0, str(_plugins_dir.parent))
from plugins.registry import list_with_status, get_plugin

# Ensure Backend root on path (model.py also does this, but be explicit here)
sys.path.insert(0, str(Path(__file__).parent.parent))


app = FastAPI(title="Urban ABM Backend API")

# Configure CORS to allow frontend to fetch from backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database path
DB_PATH = PROJECT_ROOT / "Backend" / "Environment" / "eixample_overture.duckdb"

# Street view grid — Qwen2.5-VL results (new schema) from Backend/Environment/output
SV_OUTPUT_DIR = PROJECT_ROOT / "Backend" / "Environment" / "output"
SV_IMAGES_DIR = SV_OUTPUT_DIR / "images"
SV_RESULTS_DIR = SV_OUTPUT_DIR / "results"

# Get spawn seed from environment (for reproducible experiments)
spawn_seed = os.environ.get("SPAWN_SEED")
if spawn_seed:
    spawn_seed = int(spawn_seed)
    print(f"[INFO] Using spawn seed from environment: {spawn_seed}")
else:
    print(f"[INFO] No SPAWN_SEED environment variable set (random spawning)")

# Get number of agents from environment
num_agents = int(os.getenv("NUM_AGENTS", 50))
print(f"[INFO] Number of agents from environment: {num_agents}")

# Initialize Mesa model with agents (LLMClient is initialised inside CityModel)
city_model = CityModel(num_agents=num_agents, spawn_seed=spawn_seed)


def get_db_connection():
    """Get DuckDB connection"""
    con = duckdb.connect(str(DB_PATH), read_only=True)
    con.install_extension("spatial")
    con.load_extension("spatial")
    return con

@app.get("/")
async def read_root():
    """API root - health check"""
    return {
        "status": "running",
        "message": "Urban ABM Backend API",
        "endpoints": [
            "/api/buildings",
            "/api/walk_network",
            "/api/roads",
            "/api/agents",
            "/api/agent/{agent_id}",
            "/api/agent/{agent_id}/summary",
            "/api/agent/{agent_id}/streetview",
            "/api/agent/{agent_id}/memory",
            "/api/agent/{agent_id}/stream",
            "/api/agent/{agent_id}/cognition",
            "/api/agents/summaries",
            "/api/amenities",
            "/api/walk_nodes",
            "/api/stats",
            "/api/tables",
            "/api/test",
            "/api/streetview_grid",
            "/api/streetview_grid/image/{filename}",
            "/api/streetview_grid/analysis/{lat}_{lon}",
            "/api/config/frontend",
            "/api/config/llm (POST)",
            "/api/llm/stats",
            "/api/step_continuous (POST)",
            "/api/step (POST)",
            "/api/recording/start (POST)",
            "/api/recording/stop (POST)",
            "/api/recording/status"
        ]
    }

@app.get("/api/buildings")
async def get_buildings():
    """Get buildings as GeoJSON"""
    con = get_db_connection()
    try:
        # Remove LIMIT to get ALL buildings
        query = "SELECT ST_AsText(geometry) as wkt FROM buildings"
        results = con.execute(query).fetchall()
        
        print(f"Retrieved {len(results)} buildings from database")
        
        features = []
        for idx, row in enumerate(results):
            try:
                geom = wkt.loads(row[0])
                if geom.geom_type == "Polygon":
                    coords = [[list(coord) for coord in geom.exterior.coords]]
                    features.append({
                        "type": "Feature",
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": coords
                        },
                        "properties": {"layer": "buildings"}
                    })
                    
                    if idx == 0:
                        print(f"First building coords: {coords[0][0]}")
                        
            except Exception as e:
                if idx < 5:
                    print(f"Error processing building {idx}: {e}")
                continue
        
        print(f"Returning {len(features)} building features")
        
        return {
            "type": "FeatureCollection",
            "features": features
        }
    finally:
        con.close()

@app.get("/api/walk_network")
async def get_walk_network():
    """Get walk network as GeoJSON"""
    con = get_db_connection()
    try:
        # Get ALL walk edges
        query = "SELECT ST_AsText(geometry) as wkt FROM walk_edges"
        results = con.execute(query).fetchall()
        
        print(f"Retrieved {len(results)} walk edges from database")
        
        features = []
        for idx, row in enumerate(results):
            try:
                geom = wkt.loads(row[0])
                coords = [list(coord) for coord in geom.coords]
                features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": coords
                    },
                    "properties": {"layer": "walk_network"}
                })
                
                if idx == 0:
                    print(f"First walk edge coords: {coords[0]}")
                    
            except Exception as e:
                if idx < 5:
                    print(f"Error processing walk edge {idx}: {e}")
                continue
        
        print(f"Returning {len(features)} walk network features")
        
        return {
            "type": "FeatureCollection",
            "features": features
        }
    except Exception as e:
        print(f"Error in walk_network endpoint: {e}")
        return {
            "type": "FeatureCollection",
            "features": []
        }
    finally:
        con.close()


@app.get("/api/walk_network/candidates")
async def get_walk_network_candidates(
    bbox: str = Query(..., description="west,south,east,north in WGS84"),
    spacing: int = Query(200, ge=50, le=500, description="Sample spacing in metres"),
):
    """Sample points along walk edges within a bbox at `spacing` metres.

    Returns GeoJSON with properties matching street_plm_job.py sample points:
    lat, lon, heading, street_name, highway_type, edge_id, dist_along_edge_m.
    """
    try:
        w, s, e, n = [float(x) for x in bbox.split(",")]
    except Exception:
        return {"error": "bbox must be 'west,south,east,north'"}

    UTM31N = "EPSG:32631"
    try:
        to_utm = pyproj.Transformer.from_crs("EPSG:4326", UTM31N, always_xy=True)
        to_wgs = pyproj.Transformer.from_crs(UTM31N, "EPSG:4326", always_xy=True)
    except Exception as exc:
        return {"error": f"Projection setup failed: {exc}"}

    try:
        con = get_db_connection()
        rows = con.execute("""
            SELECT id, ST_AsText(geometry), name, road_type
            FROM walk_edges
            WHERE ST_Intersects(geometry, ST_MakeEnvelope(?, ?, ?, ?))
        """, [w, s, e, n]).fetchall()
        con.close()
    except Exception as exc:
        logger.warning(f"Candidates walk_edges query failed: {exc}")
        return {"type": "FeatureCollection", "features": []}

    seen_cells: set = set()
    features = []

    for row in rows:
        edge_id, wkt_str, name, road_type = row
        if not wkt_str:
            continue
        try:
            line_wgs = wkt.loads(wkt_str)
            coords_proj = [to_utm.transform(x, y) for x, y in line_wgs.coords]
        except Exception:
            continue

        line_proj = LineString(coords_proj)
        length = line_proj.length
        if length < 1:
            continue

        n_steps = max(1, int(length / spacing))
        for i in range(n_steps + 1):
            dist = min(i * spacing, length)
            p = line_proj.interpolate(dist)
            offset = 1.0 if dist + 1.0 < length else -1.0
            p2 = line_proj.interpolate(dist + offset)
            dx, dy = p2.x - p.x, p2.y - p.y
            heading = float((np.degrees(np.arctan2(dx, dy)) + 360) % 360)

            lon, lat = to_wgs.transform(p.x, p.y)
            if not (w <= lon <= e and s <= lat <= n):
                continue

            cell = (round(lat, 4), round(lon, 4))
            if cell in seen_cells:
                continue
            seen_cells.add(cell)

            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
                "properties": {
                    "lat": round(lat, 6),
                    "lon": round(lon, 6),
                    "heading": round(heading, 1),
                    "street_name": name or "",
                    "highway_type": road_type or "unknown",
                    "edge_id": edge_id or "",
                    "dist_along_edge_m": round(float(dist), 1),
                },
            })

    return {"type": "FeatureCollection", "features": features}


@app.get("/api/roads")
async def get_roads():
    """Get roads/drive network as GeoJSON"""
    con = get_db_connection()
    try:
        # Check if roads or drive_edges table exists
        tables = con.execute("SHOW TABLES").fetchall()
        table_names = [t[0] for t in tables]
        
        print(f"Available tables: {table_names}")
        
        # Try 'roads' table first (Overture), then fall back to 'drive_edges' (OSM)
        if 'roads' in table_names:
            query = "SELECT ST_AsText(geometry) as wkt FROM roads"
        elif 'drive_edges' in table_names:
            query = "SELECT ST_AsText(geometry) as wkt FROM drive_edges"
        else:
            print("No roads or drive_edges table found")
            return {
                "type": "FeatureCollection",
                "features": []
            }
        
        results = con.execute(query).fetchall()
        
        print(f"Retrieved {len(results)} road edges from database")
        
        features = []
        for idx, row in enumerate(results):
            try:
                geom = wkt.loads(row[0])
                coords = [list(coord) for coord in geom.coords]
                features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": coords
                    },
                    "properties": {"layer": "roads"}
                })
            except Exception as e:
                if idx < 5:
                    print(f"Error processing road {idx}: {e}")
                continue
        
        print(f"Returning {len(features)} road features")
        
        return {
            "type": "FeatureCollection",
            "features": features
        }
    finally:
        con.close()

@app.get("/api/agents")
async def get_agents():
    """Get agents as GeoJSON with current state."""
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
            "geometry": {
                "type": "Point",
                "coordinates": [agent.geometry.x, agent.geometry.y]
            },
            "properties": {
                "id": agent.unique_id,
                "type": agent.agent_type,
                "archetype": archetype,
                "nearby_count": len(agent.nearby_amenities)
            }
        })
    
    return {
        "type": "FeatureCollection",
        "features": features
    }

@app.post("/api/agents/respawn")
async def respawn_agents(count: int = 15):
    """Re-create the city model with a new agent count (1-100)."""
    global city_model
    count = max(1, min(100, count))
    city_model = CityModel(num_agents=count)
    return {
        "status": "respawned",
        "count": len(city_model.city_agents),
        "step": city_model.steps,
    }


@app.post("/api/agents/respawn_advanced")
async def respawn_agents_advanced(payload: dict = Body(...)):
    """
    Advanced respawn supporting click / random-in-zone / POI / home-work spawn modes.

    Body:
      {
        "count": int (1-100, used only when spawn_mode == 'random'),
        "spawn_mode": "click" | "random" | "poi" | "home_work",
        "points": [{"lon": float, "lat": float, "archetype": str}, ...],
        "home_points": [{lon,lat}, ...] (home_work only — residents start here),
        "work_points": [{lon,lat}, ...] (home_work only — commuters start here),
        "archetype_mix": {"resident":0.25, "commuter":0.25, "tourist":0.25, "student":0.25}
      }
    """
    global city_model
    from shapely.geometry import LineString

    spawn_mode = str(payload.get("spawn_mode", "random"))
    count = max(1, min(100, int(payload.get("count", 15))))
    points = payload.get("points", []) or []
    home_points = payload.get("home_points", []) or []
    work_points = payload.get("work_points", []) or []
    mix = payload.get("archetype_mix", {}) or {}

    archetypes = list(CityAgent.ARCHETYPES)

    def _normalise_mix(m: dict) -> list[str]:
        """Turn the archetype mix into a deterministic sequence covering `count` agents."""
        if not m:
            return [archetypes[i % len(archetypes)] for i in range(count)]
        total = sum(max(0.0, float(v)) for v in m.values()) or 1.0
        seq: list[str] = []
        for arch in archetypes:
            n = int(round(count * max(0.0, float(m.get(arch, 0))) / total))
            seq.extend([arch] * n)
        while len(seq) < count:
            seq.append(archetypes[len(seq) % len(archetypes)])
        return seq[:count]

    # Resolve the (lon, lat, archetype) triples we want to materialise.
    triples: list[tuple[float, float, str]] = []
    if spawn_mode == "click" and points:
        for p in points:
            arch = str(p.get("archetype") or archetypes[len(triples) % len(archetypes)])
            try:
                triples.append((float(p["lon"]), float(p["lat"]), arch))
            except (KeyError, TypeError, ValueError):
                continue
    elif spawn_mode == "home_work" and (home_points or work_points):
        for p in home_points:
            try:
                triples.append((float(p["lon"]), float(p["lat"]), "resident"))
            except (KeyError, TypeError, ValueError):
                continue
        for p in work_points:
            try:
                triples.append((float(p["lon"]), float(p["lat"]), "commuter"))
            except (KeyError, TypeError, ValueError):
                continue
    elif spawn_mode == "poi" and points:
        # Same shape as click — UI pre-filters amenities client-side.
        arch_seq = _normalise_mix(mix) if mix else None
        for idx, p in enumerate(points[:count]):
            arch = (arch_seq[idx] if arch_seq else None) or str(p.get("archetype") or archetypes[idx % len(archetypes)])
            try:
                triples.append((float(p["lon"]), float(p["lat"]), arch))
            except (KeyError, TypeError, ValueError):
                continue
    else:
        # "random" — fall back to the legacy edge-based spawn.
        city_model = CityModel(num_agents=count)
        return {
            "status": "respawned",
            "spawn_mode": "random",
            "count": len(city_model.city_agents),
            "step": city_model.steps,
        }

    if not triples:
        return {"error": "No valid spawn points supplied for mode '{}'".format(spawn_mode)}

    # Rebuild empty model so the new agents own the network state.
    city_model = CityModel(num_agents=0)

    placed = 0
    skipped = 0
    for lon, lat, arch in triples:
        if arch not in CityAgent.ARCHETYPES:
            arch = archetypes[placed % len(archetypes)]
        start_node = city_model._find_nearest_node(lon, lat)
        if not start_node:
            skipped += 1
            continue
        edges_at_start = city_model.node_to_edges.get(start_node, [])
        if not edges_at_start:
            skipped += 1
            continue
        forward = [e for e in edges_at_start if e[2] == "forward"] or edges_at_start
        edge_id, edge_geom, direction = forward[0][0], forward[0][1], forward[0][2]
        if direction == "reverse":
            edge_geom = LineString(list(edge_geom.coords)[::-1])
        start_point = Point(edge_geom.coords[0])

        target_info = None
        try:
            target_info = city_model._pick_target_for_archetype(arch)
            if target_info:
                tn = city_model._find_nearest_node(target_info["lon"], target_info["lat"])
                target_info["target_node"] = tn
        except Exception:
            target_info = None

        try:
            agent = CityAgent(
                model=city_model,
                geometry=start_point,
                crs="EPSG:4326",
                edge_id=int(edge_id),
                edge_geom=edge_geom,
                archetype=arch,
                target_info=target_info,
            )
            city_model.city_agents.append(agent)
            placed += 1
        except Exception as e:
            logger.warning(f"respawn_advanced: failed to place agent at ({lon},{lat}): {e}")
            skipped += 1

    return {
        "status": "respawned",
        "spawn_mode": spawn_mode,
        "count": placed,
        "skipped": skipped,
        "step": city_model.steps,
    }


@app.post("/api/streetview/download")
async def download_streetview(payload: dict = Body(...)):
    """
    Sample candidate points inside ``bbox`` at ``spacing`` metres and fetch JPEGs
    from the Google Street View Static API. Skips any candidate within
    ``spacing / 2`` metres of an already-analysed point. Does NOT run VLM
    inference — that lane remains the offline batch job.

    Body: {"bbox": [west, south, east, north], "spacing": int}
    """
    import math
    import re as _re
    import requests

    bbox = payload.get("bbox") or []
    spacing = int(payload.get("spacing", 200))
    spacing = max(40, min(spacing, 600))
    if len(bbox) != 4:
        return {"error": "bbox must be [west, south, east, north]"}

    api_key = os.environ.get("GOOGLE_STREETVIEW_API_KEY", "")
    if not api_key:
        return {
            "error": "GOOGLE_STREETVIEW_API_KEY not configured in .env",
            "downloaded": [],
            "skipped": 0,
        }

    west, south, east, north = [float(x) for x in bbox]
    if east < west or north < south:
        return {"error": "bbox bounds inverted"}

    ref_lat = (north + south) / 2.0
    deg_per_m_lat = 1.0 / 110540.0
    deg_per_m_lon = 1.0 / (111320.0 * math.cos(math.radians(ref_lat)) or 1.0)
    step_lat = spacing * deg_per_m_lat
    step_lon = spacing * deg_per_m_lon

    # Existing analysis files so we don't re-download.
    existing: list[tuple[float, float]] = []
    if SV_RESULTS_DIR.is_dir():
        for jf in SV_RESULTS_DIR.glob("*_analysis.json"):
            m = _re.match(r"^(-?\d+\.\d+)_(-?\d+\.\d+)_analysis\.json$", jf.name)
            if m:
                existing.append((float(m.group(1)), float(m.group(2))))
    if SV_IMAGES_DIR.is_dir():
        for jp in SV_IMAGES_DIR.glob("sv_*.jpg"):
            m = _re.match(r"^sv_(-?\d+\.\d+)_(-?\d+\.\d+)_h\d+\.jpg$", jp.name)
            if m:
                existing.append((float(m.group(1)), float(m.group(2))))
    SV_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    near_threshold = (spacing / 2.0) * deg_per_m_lat  # ~degrees latitude
    near_threshold_sq = near_threshold ** 2

    def _is_near_existing(lat: float, lon: float) -> bool:
        for (elat, elon) in existing:
            if (elat - lat) ** 2 + (elon - lon) ** 2 <= near_threshold_sq:
                return True
        return False

    # Grid-sample inside the bbox. We rely on the Street View metadata API to
    # reject candidates with no panorama coverage (rural / blocked).
    downloaded: list[dict] = []
    skipped = 0
    requested = 0
    lat = south
    while lat <= north and requested < 200:
        lon = west
        while lon <= east and requested < 200:
            requested += 1
            if _is_near_existing(lat, lon):
                skipped += 1
                lon += step_lon
                continue

            # Probe metadata first (free, distinguishes "no panorama" from billable fetch)
            try:
                meta = requests.get(
                    "https://maps.googleapis.com/maps/api/streetview/metadata",
                    params={"location": f"{lat:.6f},{lon:.6f}", "radius": 50, "key": api_key},
                    timeout=10,
                ).json()
                if meta.get("status") != "OK":
                    skipped += 1
                    lon += step_lon
                    continue
                pano_lat = float(meta.get("location", {}).get("lat", lat))
                pano_lon = float(meta.get("location", {}).get("lng", lon))
            except Exception:
                skipped += 1
                lon += step_lon
                continue

            if _is_near_existing(pano_lat, pano_lon):
                skipped += 1
                lon += step_lon
                continue

            heading = 0  # north — keeps file naming deterministic
            fname = f"sv_{pano_lat:.6f}_{pano_lon:.6f}_h{heading}.jpg"
            fpath = SV_IMAGES_DIR / fname
            if fpath.exists():
                skipped += 1
                lon += step_lon
                continue
            try:
                img = requests.get(
                    "https://maps.googleapis.com/maps/api/streetview",
                    params={
                        "size": "640x640",
                        "location": f"{pano_lat:.6f},{pano_lon:.6f}",
                        "heading": heading,
                        "pitch": 0,
                        "fov": 90,
                        "key": api_key,
                    },
                    timeout=20,
                )
                if img.status_code == 200 and img.content[:3] == b"\xff\xd8\xff":
                    fpath.write_bytes(img.content)
                    downloaded.append({"lat": pano_lat, "lon": pano_lon, "filename": fname})
                    existing.append((pano_lat, pano_lon))
                else:
                    skipped += 1
            except Exception:
                skipped += 1
            lon += step_lon
        lat += step_lat

    return {
        "status": "ok",
        "spacing_m": spacing,
        "bbox": [west, south, east, north],
        "requested": requested,
        "downloaded": downloaded,
        "skipped": skipped,
        "note": "VLM analysis is offline — run street_plm_job.py to populate scene fields.",
    }

@app.get("/api/agent/{agent_id}")
async def get_agent_info(agent_id: int):
    """Get agent details and what they see (from stored query results)"""
    # Find the agent
    agent = next((a for a in city_model.city_agents if a.unique_id == agent_id), None)
    
    if not agent:
        return {"error": "Agent not found"}
    
    # Return stored query results (not re-querying)
    return {
        "id": agent.unique_id,
        "type": agent.agent_type,
        "location": {
            "lon": agent.geometry.x,
            "lat": agent.geometry.y
        },
        "nearby_amenities": agent.nearby_amenities,
        "street_perception": agent.street_perception
    }

@app.get("/api/agent/{agent_id}/summary")
async def get_agent_summary(agent_id: int):
    """Get LLM-generated natural language summary of what the agent sees."""
    agent = next((a for a in city_model.city_agents if a.unique_id == agent_id), None)
    if not agent:
        return {"error": "Agent not found"}

    try:
        profile = await agent.memory.status.get("agent_profile", {})
        needs = await agent.memory.status.get("needs", {})
        cognition = await agent.memory.status.get("cognition_state", {})

        # Build perception context for narration using scene_analysis text fields
        perception_ctx = ""
        if hasattr(agent, 'street_perception') and agent.street_perception:
            sp = agent.street_perception
            scene_parts = []
            for key in ("scene_overview", "vegetation", "pedestrian_activity", "lighting_atmosphere"):
                val = sp.get(key, "")
                if val and val.strip().lower() != "unknown":
                    scene_parts.append(val)
            if scene_parts:
                perception_ctx = f" Street scene: {' '.join(scene_parts[:2])}"

        # Format amenities list
        amenities_list = ', '.join(a.get('type', '?') for a in agent.nearby_amenities[:5]) or 'nothing notable'

        messages = [
            {"role": "system", "content": "You are narrating an urban simulation agent in Barcelona Eixample. Be concise (2-3 sentences)."},
            {"role": "user", "content": (
                f"Agent {agent_id} is a {profile.get('archetype','pedestrian')} at "
                f"lon={agent.geometry.x:.5f}, lat={agent.geometry.y:.5f}. "
                f"Needs: hunger={needs.get('hunger',0.5):.2f}, energy={needs.get('energy',1.0):.2f}, social={needs.get('social',0.5):.2f}. "
                f"Mood: {cognition.get('mood','neutral')}. "
                f"Nearby: {amenities_list}."
                f"{perception_ctx} "
                "Narrate what this agent is experiencing right now."
            )}
        ]

        summary = await city_model.llm_client.chat(messages)
        
        return {
            "agent_id": agent.unique_id,
            "summary": summary,
            "location": {"lon": agent.geometry.x, "lat": agent.geometry.y},
            "amenity_count": len(agent.nearby_amenities),
            "archetype": profile.get("archetype", "unknown"),
            "perception_mode": getattr(city_model, 'perception_mode', 'both'),
        }
    except Exception as e:
        import logging
        logging.error(f"Error generating summary for agent {agent_id}: {e}")
        return {
            "agent_id": agent_id,
            "summary": f"Error generating narrative: {str(e)}",
            "location": {"lon": agent.geometry.x, "lat": agent.geometry.y},
            "amenity_count": len(agent.nearby_amenities) if agent else 0,
            "error_details": str(e),
        }


@app.get("/api/agents/summaries")
async def get_all_agent_summaries():
    """Get LLM-generated summaries for a sample of agents (first 10 to avoid overload)."""
    import asyncio

    async def _summarize(agent):
        profile = await agent.memory.status.get("agent_profile", {})
        needs = await agent.memory.status.get("needs", {})
        messages = [
            {"role": "system", "content": "Narrate this urban simulation agent in one sentence."},
            {"role": "user", "content": (
                f"Agent {agent.unique_id} ({profile.get('archetype','pedestrian')}) at "
                f"{agent.geometry.x:.4f},{agent.geometry.y:.4f}. "
                f"Hunger={needs.get('hunger',0.5):.1f} Energy={needs.get('energy',1.0):.1f}. "
                f"Nearby: {', '.join(a.get('type','?') for a in agent.nearby_amenities[:3]) or 'none'}."
            )}
        ]
        summary = await city_model.llm_client.chat(messages)
        return {
            "agent_id": agent.unique_id,
            "summary": summary,
            "location": {"lon": agent.geometry.x, "lat": agent.geometry.y},
            "archetype": profile.get("archetype", "unknown"),
        }

    sample = city_model.city_agents[:10]
    summaries = await asyncio.gather(*[_summarize(a) for a in sample])
    return {"total_agents": len(city_model.city_agents), "sample_size": len(summaries), "summaries": list(summaries)}

@app.post("/api/step")
async def step_simulation():
    """Step the simulation forward using async LLM-driven movement."""
    await city_model.async_step()
    return {
        "step": city_model.steps,
        "agents": len(city_model.city_agents),
        "llm_stats": city_model.llm_client.stats(),
    }

@app.post("/api/step_continuous")
async def step_continuous():
    """Step simulation and return updated agent positions"""
    await city_model.async_step()
    
    agents_data = []
    for agent in city_model.city_agents:
        needs = {}
        try:
            needs = await agent.memory.status.get("needs", {})
        except Exception:
            pass
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
        "llm_stats": city_model.llm_client.stats(),
    }

@app.get("/api/test")
async def test_agents():
    """Test endpoint to verify agents"""
    return {
        "agent_count": len(city_model.city_agents),
        "sample_agent": {
            "id": city_model.city_agents[0].unique_id if city_model.city_agents else None,
            "coords": [city_model.city_agents[0].geometry.x, city_model.city_agents[0].geometry.y] if city_model.city_agents else None
        } if city_model.city_agents else "No agents"
    }

@app.get("/api/tables")
async def list_tables():
    """List all tables in the database"""
    con = get_db_connection()
    try:
        tables = con.execute("SHOW TABLES").fetchall()
        table_list = [t[0] for t in tables]
        
        # Get row counts
        table_info = {}
        for table in table_list:
            try:
                count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                table_info[table] = count
            except:
                table_info[table] = 0
        
        return table_info
    finally:
        con.close()

@app.get("/api/amenities")
async def get_amenities():
    """Get amenities as GeoJSON"""
    con = get_db_connection()
    try:
        query = "SELECT name, amenity, ST_AsText(geometry) as wkt, address, website, phone, amenity_tags FROM amenities"
        results = con.execute(query).fetchall()
        
        features = []
        for row in results:
            try:
                geom = wkt.loads(row[2])
                features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [geom.x, geom.y]
                    },
                    "properties": {
                        "name": str(row[0]) if row[0] else "Unnamed",
                        "amenity": row[1],
                        "address": str(row[3]) if row[3] else None,
                        "website": str(row[4]) if row[4] else None,
                        "phone": str(row[5]) if row[5] else None,
                        "amenity_tags": str(row[6]) if row[6] else None,
                        "layer": "amenities"
                    }
                })
            except:
                continue
        
        return {
            "type": "FeatureCollection",
            "features": features
        }
    finally:
        con.close()

@app.get("/api/walk_nodes")
async def get_walk_nodes():
    """Get walk nodes as GeoJSON"""
    con = get_db_connection()
    try:
        query = "SELECT ST_AsText(geometry) as wkt FROM walk_nodes LIMIT 500"
        results = con.execute(query).fetchall()
        
        features = []
        for row in results:
            try:
                geom = wkt.loads(row[0])
                features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [geom.x, geom.y]
                    },
                    "properties": {"layer": "walk_nodes"}
                })
            except:
                continue
        
        return {
            "type": "FeatureCollection",
            "features": features
        }
    finally:
        con.close()

@app.get("/api/stats")
async def get_stats():
    """Get database statistics"""
    con = get_db_connection()
    try:
        stats = {}
        
        # Count each table
        tables = ['buildings', 'walk_edges', 'walk_nodes', 'amenities']
        for table in tables:
            try:
                count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                stats[table] = count
            except:
                stats[table] = 0
        
        # Get bounding box
        try:
            bbox = con.execute("""
                SELECT 
                    MIN(ST_XMin(geometry)) as minx,
                    MAX(ST_XMax(geometry)) as maxx,
                    MIN(ST_YMin(geometry)) as miny,
                    MAX(ST_YMax(geometry)) as maxy
                FROM buildings
            """).fetchone()
            stats['bbox'] = {
                'minLon': bbox[0],
                'maxLon': bbox[1],
                'minLat': bbox[2],
                'maxLat': bbox[3]
            }
        except:
            stats['bbox'] = None
        
        return stats
    finally:
        con.close()

@app.get("/api/agent/{agent_id}/memory")
async def get_agent_memory(agent_id: int):
    """Return the full memory snapshot (status + stream) for an agent."""
    agent = next((a for a in city_model.city_agents if a.unique_id == agent_id), None)
    if not agent:
        return {"error": "Agent not found"}
    snapshot = await agent.memory.snapshot()
    return snapshot


@app.get("/api/agent/{agent_id}/stream")
async def get_agent_stream(agent_id: int, topic: str = "", n: int = 20):
    """Return recent stream memory events for an agent, optionally filtered by topic."""
    agent = next((a for a in city_model.city_agents if a.unique_id == agent_id), None)
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
    """Return current cognition state and needs for an agent."""
    agent = next((a for a in city_model.city_agents if a.unique_id == agent_id), None)
    if not agent:
        return {"error": "Agent not found"}
    cognition = await agent.memory.status.get("cognition_state", {})
    needs = await agent.memory.status.get("needs", {})
    profile = await agent.memory.status.get("agent_profile", {})
    plan = await agent.memory.status.get("current_plan", {})
    satisfaction_source = await agent.memory.status.get("satisfaction_source", "none")
    satisfaction_reasoning = await agent.memory.status.get("satisfaction_reasoning", "")
    return {
        "agent_id": agent_id,
        "archetype": profile.get("archetype", "unknown"),
        "cognition_state": cognition,
        "needs": needs,
        "current_plan": plan,
        "satisfaction_source": satisfaction_source,
        "satisfaction_reasoning": satisfaction_reasoning,
    }


@app.get("/api/agent/{agent_id}/perception-text")
async def get_agent_perception(agent_id: int):
    """Return perception data and streetview image for the agent's current location."""
    import json as json_lib
    import re
    from math import radians, cos, sin, asin, sqrt

    agent = next((a for a in city_model.city_agents if a.unique_id == agent_id), None)
    if not agent:
        return {"error": "Agent not found", "image_url": "", "perception": {}}

    # Get agent's current position
    agent_lon = agent.geometry.x
    agent_lat = agent.geometry.y

    # Find closest streetview analysis to agent's current position
    closest_file = None
    closest_distance = float('inf')

    if SV_RESULTS_DIR.is_dir():
        for json_file in SV_RESULTS_DIR.glob("*_analysis.json"):
            # Parse lat/lon from filename: {lat}_{lon}_analysis.json
            m = re.match(r"^(-?\d+\.\d+)_(-?\d+\.\d+)_analysis\.json$", json_file.name)
            if not m:
                continue
            sv_lat, sv_lon = float(m.group(1)), float(m.group(2))

            # Simple Euclidean distance (good enough for nearby points)
            dist = sqrt((sv_lon - agent_lon)**2 + (sv_lat - agent_lat)**2)
            if dist < closest_distance:
                closest_distance = dist
                closest_file = json_file

    # Load the closest streetview data
    perception_data = {}
    image_url = ""
    if closest_file:
        try:
            data = json_lib.loads(closest_file.read_text(encoding="utf-8"))
            meta = data.get("metadata", {})
            scene = data.get("scene_analysis", {})
            src_img = meta.get("source_image", "")
            perception_data = scene
            image_url = f"/api/streetview_grid/image/{src_img}" if src_img else ""
        except Exception as e:
            logger.warning(f"Error loading streetview data: {e}")

    # Fall back to agent's street_perception if available
    if hasattr(agent, 'street_perception') and agent.street_perception:
        perception_data = agent.street_perception

    return {
        "agent_id": agent_id,
        "image_url": image_url,
        "perception": perception_data,
        "location": {"lon": agent_lon, "lat": agent_lat},
        "closest_distance_km": closest_distance if closest_distance != float('inf') else None,
    }


@app.post("/api/config/llm")
async def update_llm_config(provider: str, model: str, base_url: str = "", api_key: str = ""):
    """Hot-swap the LLM provider/model at runtime (takes effect on next agent step)."""
    import os
    os.environ["LLM_PROVIDER"] = provider
    os.environ["LLM_MODEL"] = model
    if base_url:
        os.environ["LLM_BASE_URL"] = base_url
    if api_key:
        os.environ["LLM_API_KEY"] = api_key

    from LLM.llm_config import LLMConfig
    from LLM.llm_client import LLMClient
    new_config = LLMConfig.from_env()
    city_model.llm_client = LLMClient(new_config)
    # Update all agent dispatchers to use the new client
    for agent in city_model.city_agents:
        agent.dispatcher.llm = city_model.llm_client
        agent.dispatcher.needs_block.llm = city_model.llm_client
        agent.dispatcher.cognition_block.llm = city_model.llm_client
        agent.dispatcher.mobility_block.llm = city_model.llm_client
    return {"status": "updated", "provider": provider, "model": model}


# Global perception mode setting (affects what agents query for)
perception_mode = os.getenv("PERCEPTION_MODE", "both")  # Default: amenities + perception points

@app.post("/api/config/perception-mode")
async def update_perception_mode(mode: str = Body(..., embed=True)):
    """Update what agents perceive: 'amenities', 'perception', 'both', or 'rule_based'."""
    global perception_mode
    if mode not in ["amenities", "perception", "both", "rule_based"]:
        return {"error": "Invalid mode. Must be 'amenities', 'perception', 'both', or 'rule_based'"}
    perception_mode = mode
    # Update the model's perception mode
    city_model.perception_mode = mode
    print(f"Agent perception mode updated to: {mode}")
    return {"status": "updated", "mode": mode}


@app.get("/api/config/perception-mode")
async def get_perception_mode():
    """Get current perception mode."""
    return {"mode": perception_mode}


@app.get("/api/llm/stats")
async def get_llm_stats():
    """Return LLM usage statistics (token counts, latency)."""
    return city_model.llm_client.stats()


# ── Street View Grid endpoints (DuckDB-backed) ─────────────────────


@app.get("/api/streetview_grid")
async def get_streetview_grid():
    """Return GeoJSON of new-schema JSON files from Backend/Environment/output/results/."""
    import json as json_lib
    import re

    features = []
    seen: set = set()  # (round4-lat, round4-lon) dedup

    # ── 1. New-schema: test/StreetPLM/results/ (Qwen2.5-VL) ─────────
    if SV_RESULTS_DIR.is_dir():
        for json_file in sorted(SV_RESULTS_DIR.glob("*_analysis.json")):
            m = re.match(r"^(-?\d+\.\d+)_(-?\d+\.\d+)_analysis\.json$", json_file.name)
            if not m:
                continue
            lat, lon = float(m.group(1)), float(m.group(2))
            key = (round(lat, 4), round(lon, 4))
            seen.add(key)
            try:
                data = json_lib.loads(json_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            meta = data.get("metadata", {})
            scene = data.get("scene_analysis") or {}
            src_img = meta.get("source_image", "")
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "lat": lat, "lon": lon,
                    "heading": meta.get("heading", 0.0),
                    "image_url": f"/api/streetview_grid/image/{src_img}" if src_img else "",
                    "model": meta.get("model", ""),
                    "timestamp": meta.get("timestamp", ""),
                    "schema": "new",
                    "scene": scene.get("scene", ""),
                    "lighting": json_lib.dumps(scene.get("lighting", []), ensure_ascii=False),
                    "spatial_character": json_lib.dumps(scene.get("spatial_character", []), ensure_ascii=False),
                    "crowdedness": json_lib.dumps(scene.get("crowdedness", []), ensure_ascii=False),
                    "greenery": json_lib.dumps(scene.get("greenery", []), ensure_ascii=False),
                    "street_amenities": json_lib.dumps(scene.get("street_amenities", []), ensure_ascii=False),
                    "visible_text": json_lib.dumps(scene.get("visible_text", []), ensure_ascii=False),
                },
            })

    return {"type": "FeatureCollection", "features": features}


@app.get("/api/streetview_grid/image/{filename}")
async def get_streetview_image(filename: str):
    """Serve a street view image from either the StreetPLM or legacy output directory."""
    for images_dir in [SV_IMAGES_DIR, SV_OUTPUT_DIR / "images"]:
        filepath = images_dir / filename
        if filepath.is_file():
            return FileResponse(filepath, media_type="image/jpeg")
    return {"error": "Image not found", "filename": filename}


@app.get("/api/streetview_grid/json/{filename}")
async def get_streetview_json(filename: str):
    """Serve a street view analysis JSON file from the results directory."""
    import json as json_lib
    results_dir = SV_OUTPUT_DIR / "results"
    filepath = results_dir / filename
    if not filepath.is_file():
        return {"error": "Analysis JSON not found", "filename": filename}
    try:
        return json_lib.loads(filepath.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/streetview_grid/analysis/{lat}_{lon}")
async def get_streetview_analysis(lat: str, lon: str):
    """Return all perception fields for a given coordinate from DuckDB."""
    try:
        con = duckdb.connect(str(DB_PATH), read_only=True)
        row = con.execute("""
            SELECT latitude, longitude, walkability, has_vegetation,
                   pedestrian_activity, architectural_style,
                   building_condition, source_image,
                   scene_narrative, materials, street_furniture,
                   spatial_impression, heading, timestamp_str, model_name
            FROM streetview_perception
            WHERE ROUND(latitude, 4) = ROUND(CAST(? AS DOUBLE), 4)
              AND ROUND(longitude, 4) = ROUND(CAST(? AS DOUBLE), 4)
            LIMIT 1
        """, [lat, lon]).fetchone()
        con.close()
    except Exception as e:
        return {"error": str(e)}

    if not row:
        return {"error": "Analysis not found"}
    return {
        "latitude": row[0],
        "longitude": row[1],
        "walkability": row[2],
        "has_vegetation": row[3],
        "pedestrian_activity": row[4],
        "architectural_style": row[5],
        "building_condition": row[6],
        "source_image": row[7],
        "scene_narrative": row[8],
        "materials": row[9],
        "street_furniture": row[10],
        "spatial_impression": row[11],
        "heading": row[12],
        "timestamp": row[13],
        "model": row[14],
    }


# ── Frontend config endpoint ────────────────────────────────────────

@app.get("/api/config/frontend")
async def get_frontend_config():
    """Expose non-secret configuration needed by the frontend."""
    return {
        "mapbox_token": os.environ.get("MAPBOX_TOKEN", ""),
        "llm_provider": os.environ.get("LLM_PROVIDER", "gemini"),
        "llm_model": os.environ.get("LLM_MODEL", ""),
        "num_agents": int(os.environ.get("NUM_AGENTS", 50)),
        "available_providers": [
            {"id": "gemini", "name": "Google Gemini 2.0 Flash Lite", "description": "Fast, efficient cloud LLM by Google — no local setup required"},
            {"id": "ollama", "name": "Ollama (Local)", "description": "Local LLM via Ollama — no GPU required"},
            {"id": "vllm", "name": "vLLM (Docker GPU)", "description": "High-performance GPU inference via vLLM Docker"},
            {"id": "lmdeploy", "name": "LMDeploy (Docker GPU)", "description": "High-performance GPU inference via LMDeploy Docker"},
        ],
    }


# ── Recording API endpoints ─────────────────────────────────────────

@app.post("/api/recording/start")
async def start_recording(
    session_name: str = None,
    include_thoughts: bool = True,
    include_perception: bool = True,
):
    """
    Start recording agent behaviors to GeoParquet.

    Args:
        session_name: Optional name for the recording session
        include_thoughts: Whether to include agent thought streams (default: True)
        include_perception: Whether to include street perception data (default: True)

    Returns:
        Session ID and status
    """
    # Recover any unmerged sessions from previous crashes
    logger.info("Checking for unmerged recording sessions...")
    recovered = recover_unmerged_sessions(PROJECT_ROOT / "Documentation")
    if recovered:
        logger.info(f"Recovered {len(recovered)} session(s): {[p.name for p in recovered]}")

    # Stop any existing recording
    clear_recorder()

    # Get current perception mode
    current_perception_mode = getattr(city_model, 'perception_mode', 'both')

    # Create new recorder
    recorder = create_recorder(
        output_dir=PROJECT_ROOT / "Documentation",
        max_buffer_size=5000,
        include_thoughts=include_thoughts,
        include_perception=include_perception,
        perception_mode=current_perception_mode,
    )

    # Start recording
    session_id = recorder.start_recording(session_name)

    # Set recorder on city_model for integration
    city_model.set_recorder(recorder)

    return {
        "status": "recording_started",
        "session_id": session_id,
        "session_name": session_name or "auto",
        "include_thoughts": include_thoughts,
        "include_perception": include_perception,
        "perception_mode": current_perception_mode,
        "output_dir": str(PROJECT_ROOT / "Documentation"),
        "recovered_sessions": [str(p) for p in recovered],
    }


@app.post("/api/recording/stop")
async def stop_recording():
    """
    Stop recording and export to GeoParquet.
    
    Returns:
        File path and recording statistics
    """
    recorder = get_recorder()
    
    if not recorder or not recorder.is_recording:
        return {"status": "no_recording", "message": "No active recording session"}
    
    # Stop recording on city_model
    city_model.clear_recorder()
    
    # Stop recorder and export
    file_path = recorder.stop_recording()
    
    if file_path:
        status = recorder.get_status()
        return {
            "status": "recording_stopped",
            "file_path": str(file_path),
            "file_name": file_path.name,
            "total_records": status['total_records'],
            "agents_tracked": status['agents_tracked'],
            "steps_recorded": status['steps_recorded'],
            "records_written": status['records_written'],
        }
    else:
        return {
            "status": "error",
            "message": "Failed to export GeoParquet - check server logs",
        }


@app.get("/api/recording/status")
async def get_recording_status():
    """
    Get current recording status.
    
    Returns:
        Recording status and statistics
    """
    recorder = get_recorder()
    
    if not recorder:
        return {
            "is_recording": False,
            "message": "No recorder initialized",
        }
    
    status = recorder.get_status()
    output_path = recorder.get_output_path()
    
    return {
        "is_recording": status['is_recording'],
        "session_id": status['session_id'],
        "session_name": status['session_name'],
        "start_time": status['start_time'],
        "start_step": status['start_step'],
        "total_records": status['total_records'],
        "agents_tracked": status['agents_tracked'],
        "steps_recorded": status['steps_recorded'],
        "buffer_size": status['buffer_size'],
        "output_path": str(output_path) if output_path else None,
    }


# ── Overture download jobs ─────────────────────────────────────────────────
_overture_jobs: dict[str, dict] = {}  # job_id → progress dict

@app.post("/api/overture/download")
async def start_overture_download(payload: dict = Body(...)):
    """Start a background Overture Maps download for the given bbox."""
    bbox_list = payload.get("bbox")  # [west, south, east, north]
    layers = payload.get("layers", ["buildings", "amenities", "transport"])
    location_name = payload.get("location_name", "custom_zone")
    gcp_project = payload.get("gcp_project") or None
    if not bbox_list or len(bbox_list) != 4:
        return {"error": "bbox must be [west, south, east, north]"}

    bbox = {
        "min_lon": bbox_list[0], "min_lat": bbox_list[1],
        "max_lon": bbox_list[2], "max_lat": bbox_list[3],
    }
    job_id = str(uuid.uuid4())[:8]

    pipeline = OverturePipeline(
        bbox=bbox, location_name=location_name,
        layers=layers, gcp_project=gcp_project,
        db_path=DB_PATH,
        job_id=job_id,
    )
    _overture_jobs[job_id] = pipeline.progress

    def run():
        pipeline.run()

    threading.Thread(target=run, daemon=True).start()
    return {"job_id": job_id, "status": "started"}


@app.get("/api/overture/status/{job_id}")
async def get_overture_status(job_id: str):
    if job_id not in _overture_jobs:
        return {"error": "Unknown job"}
    return _overture_jobs[job_id]


@app.post("/api/overture/save/{job_id}")
async def save_overture_download(job_id: str, payload: dict = Body(...)):
    """Save downloaded data by appending to existing DB or saving as new file."""
    global city_model

    if job_id not in _overture_jobs:
        return {"error": "Unknown job"}

    mode = payload.get("mode", "append")  # "append" or "new"
    db_name = payload.get("db_name")

    # Get pending file path from the job progress (set by pipeline)
    progress = _overture_jobs[job_id]
    pending_path = progress.get("pending_path")

    if not pending_path:
        return {"error": "No pending database found for this job"}

    pending_path = Path(pending_path)
    if not pending_path.exists():
        return {"error": "Pending database file not found"}

    try:
        if mode == "append":
            # Append downloaded tables to the live database
            pending_con = duckdb.connect(str(pending_path), read_only=True)
            pending_con.execute("INSTALL spatial; LOAD spatial;")
            main_con = duckdb.connect(str(DB_PATH))
            main_con.execute("INSTALL spatial; LOAD spatial;")

            tables = ["buildings", "amenities", "walk_edges"]
            for tbl in tables:
                try:
                    # Check if table exists in pending
                    pending_con.execute(f"SELECT 1 FROM {tbl} LIMIT 1")

                    # Export pending table to temp parquet
                    import tempfile
                    with tempfile.NamedTemporaryFile(suffix='.parquet', delete=False) as tmp:
                        tmp_path = tmp.name
                    pending_con.execute(f"COPY {tbl} TO '{tmp_path}' (FORMAT PARQUET)")

                    # Insert rows that don't already exist (by id)
                    main_con.execute(f"""
                        INSERT INTO {tbl}
                        SELECT * FROM read_parquet('{tmp_path}')
                        WHERE id NOT IN (SELECT id FROM {tbl})
                    """)
                    os.unlink(tmp_path)
                    count = main_con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                except Exception as e:
                    logger.warning(f"Failed to append {tbl}: {e}")

            pending_con.close()
            main_con.close()

            # Reinitialize the model with updated data
            city_model = CityModel(num_agents=num_agents, spawn_seed=spawn_seed)
            pending_path.unlink(missing_ok=True)

            return {
                "status": "ok",
                "mode": "append",
                "message": "Data appended to map database"
            }

        elif mode == "new":
            # Save pending file as a new database
            if not db_name or not db_name.strip():
                return {"error": "db_name required for new mode"}

            # Sanitize filename
            db_name = "".join(c for c in db_name if c.isalnum() or c == "_")
            if not db_name:
                return {"error": "Invalid database name"}

            new_db_path = DB_PATH.parent / f"{db_name}.duckdb"
            shutil.copy2(str(pending_path), str(new_db_path))
            pending_path.unlink(missing_ok=True)

            return {
                "status": "ok",
                "mode": "new",
                "filename": f"{db_name}.duckdb",
                "message": f"Database saved as {db_name}.duckdb"
            }

        else:
            return {"error": f"Unknown mode: {mode}"}

    except Exception as exc:
        logger.error(f"Save failed: {exc}")
        pending_path.unlink(missing_ok=True)
        return {"error": f"Save failed: {exc}"}


@app.post("/api/database/upload")
async def upload_database(file: UploadFile = File(...)):
    """Upload a DuckDB database file and reload the model."""
    global city_model
    if not file.filename or not (file.filename.endswith('.duckdb') or file.filename.endswith('.db')):
        return {"error": "Please upload a .duckdb or .db file"}
    try:
        content = await file.read()
        if len(content) == 0:
            return {"error": "Uploaded file is empty"}
        backup_path = DB_PATH.with_name(DB_PATH.stem + ".bak" + DB_PATH.suffix)
        if DB_PATH.exists():
            shutil.copy2(str(DB_PATH), str(backup_path))
        DB_PATH.write_bytes(content)
        city_model = CityModel(num_agents=num_agents, spawn_seed=spawn_seed)
        return {
            "status": "ok",
            "filename": file.filename,
            "agents": len(city_model.city_agents),
            "message": f"Database loaded: {file.filename} ({len(content)//1024} KB)"
        }
    except Exception as exc:
        return {"error": f"Upload failed: {exc}"}


@app.get("/api/recording/download/{filename}")
async def download_recording(filename: str):
    """
    Download a recorded GeoParquet file.
    
    Args:
        filename: Name of the GeoParquet file to download
        
    Returns:
        File download response
    """
    file_path = PROJECT_ROOT / "Documentation" / filename
    
    if not file_path.exists():
        return {"error": "File not found", "filename": filename}
    
    return FileResponse(
        str(file_path),
        media_type="application/octet-stream",
        filename=filename,
    )


@app.get("/api/assets/agents/{filename}")
async def serve_agent_glb(filename: str):
    """Serve GLB agent character models to the frontend."""
    safe = Path(filename).name  # strip any path traversal
    file_path = FRONTEND_DIR / "assets" / "agents" / safe
    if not file_path.exists() or file_path.suffix.lower() != ".glb":
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Asset not found: {safe}")
    return FileResponse(str(file_path), media_type="model/gltf-binary")


# ── External Data Source endpoints ───────────────────────────────────────────
_ext_jobs: dict[str, dict] = {}  # job_id → progress dict


@app.get("/api/external/sources")
async def list_external_sources():
    """List all available external data plugins with their loaded status."""
    try:
        con = duckdb.connect(str(DB_PATH))
        con.execute("INSTALL spatial; LOAD spatial;")
        result = list_with_status(con)
        con.close()
        return {"sources": result}
    except Exception as e:
        return {"sources": [], "error": str(e)}


@app.post("/api/external/{source_name}/download")
async def start_external_download(source_name: str, payload: dict = Body(default={})):
    """Start a background download job for an external data plugin."""
    plugin = get_plugin(source_name)
    if not plugin:
        return {"error": f"Unknown source: {source_name}"}

    bbox_list = payload.get("bbox")
    if not bbox_list or len(bbox_list) != 4:
        return {"error": "bbox must be [west, south, east, north]"}

    bbox = {
        "min_lon": bbox_list[0], "min_lat": bbox_list[1],
        "max_lon": bbox_list[2], "max_lat": bbox_list[3],
    }
    job_id = f"{source_name}_{str(uuid.uuid4())[:8]}"
    progress = {"pct": 0, "status": "running", "log": [], "error": None}
    _ext_jobs[job_id] = progress

    def _progress_cb(pct: float, msg: str) -> None:
        progress["pct"] = pct
        progress["log"].append(msg)
        if len(progress["log"]) > 50:
            progress["log"] = progress["log"][-50:]

    def run():
        try:
            con = duckdb.connect(str(DB_PATH))
            con.execute("INSTALL spatial; LOAD spatial;")
            row_count = plugin.fetch(bbox=bbox, con=con, progress_cb=_progress_cb)
            con.close()
            progress["pct"] = 100
            progress["status"] = "done"
            progress["row_count"] = row_count
            # Reload model context so agents see new data immediately
            global city_model
            try:
                city_model = CityModel(num_agents=num_agents, spawn_seed=spawn_seed)
            except Exception as e:
                logger.warning(f"Model reload after {source_name} download failed: {e}")
        except Exception as e:
            logger.error(f"External download {source_name} failed: {e}")
            progress["status"] = "error"
            progress["error"] = str(e)

    threading.Thread(target=run, daemon=True).start()
    return {"job_id": job_id, "status": "started"}


@app.get("/api/external/{source_name}/status/{job_id}")
async def get_external_status(source_name: str, job_id: str):
    if job_id not in _ext_jobs:
        return {"error": "Unknown job"}
    return _ext_jobs[job_id]


@app.delete("/api/external/{source_name}")
async def remove_external_source(source_name: str):
    """Drop the ext_* table for this plugin from DuckDB."""
    global city_model
    plugin = get_plugin(source_name)
    if not plugin:
        return {"error": f"Unknown source: {source_name}"}
    try:
        con = duckdb.connect(str(DB_PATH))
        con.execute("INSTALL spatial; LOAD spatial;")
        plugin.drop_table(con)
        con.close()
        city_model = CityModel(num_agents=num_agents, spawn_seed=spawn_seed)
        return {"status": "ok", "message": f"{source_name} data removed"}
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("map_server:app", host="127.0.0.1", port=8000, reload=True)
