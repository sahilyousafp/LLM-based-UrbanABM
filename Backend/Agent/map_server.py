import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import duckdb
from shapely import wkt

# Load .env from project root before anything else
PROJECT_ROOT = Path(__file__).parent.parent.parent
load_dotenv(PROJECT_ROOT / ".env")

from model import CityModel
from geoparquet_recorder import create_recorder, get_recorder, clear_recorder

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

# Street view grid output directory
SV_OUTPUT_DIR = PROJECT_ROOT / "Backend" / "Environment" / "output"
SV_IMAGES_DIR = SV_OUTPUT_DIR / "images"
SV_RESULTS_DIR = SV_OUTPUT_DIR / "results"

# Initialize Mesa model with agents (LLMClient is initialised inside CityModel)
city_model = CityModel(num_agents=15)


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

    messages = [
        {"role": "system", "content": "You are narrating an urban simulation agent in Barcelona Eixample. Be concise (2-3 sentences)."},
        {"role": "user", "content": (
            f"Agent {agent_id} is a {profile.get('archetype','pedestrian')} at "
            f"lon={agent.geometry.x:.5f}, lat={agent.geometry.y:.5f}. "
            f"Needs: hunger={needs.get('hunger',0.5):.2f}, energy={needs.get('energy',1.0):.2f}, social={needs.get('social',0.5):.2f}. "
            f"Mood: {cognition.get('mood','neutral')}. "
            f"Nearby: {', '.join(a.get('type','?') for a in agent.nearby_amenities[:5]) or 'nothing notable'}."
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
        agents_data.append({
            "id": agent.unique_id,
            "lon": agent.geometry.x,
            "lat": agent.geometry.y,
            "nearby_count": len(agent.nearby_amenities)
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
    return {
        "agent_id": agent_id,
        "archetype": profile.get("archetype", "unknown"),
        "cognition_state": cognition,
        "needs": needs,
        "current_plan": plan,
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
perception_mode = "both"  # Default: amenities + perception points

@app.post("/api/config/perception-mode")
async def update_perception_mode(mode: str = Body(..., embed=True)):
    """Update what agents perceive: 'amenities', 'perception', or 'both'."""
    global perception_mode
    if mode not in ["amenities", "perception", "both"]:
        return {"error": "Invalid mode. Must be 'amenities', 'perception', or 'both'"}
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
    """Return GeoJSON of streetview scene analysis data read directly from JSON result files."""
    import json as json_lib
    import re

    features = []
    if not SV_RESULTS_DIR.is_dir():
        return {"type": "FeatureCollection", "features": [], "error": "Results directory not found"}

    for json_file in sorted(SV_RESULTS_DIR.glob("*_analysis.json")):
        # Parse lat/lon from filename: {lat}_{lon}_analysis.json
        m = re.match(r"^(-?\d+\.\d+)_(-?\d+\.\d+)_analysis\.json$", json_file.name)
        if not m:
            continue
        lat, lon = float(m.group(1)), float(m.group(2))
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
                "lat": lat,
                "lon": lon,
                "heading": meta.get("heading", 0.0),
                "image_url": f"/api/streetview_grid/image/{src_img}" if src_img else "",
                "model": meta.get("model", ""),
                "timestamp": meta.get("timestamp", ""),
                "scene_overview": scene.get("scene_overview", ""),
                "buildings": scene.get("buildings", ""),
                "materials": scene.get("materials", ""),
                "building_condition": scene.get("building_condition", ""),
                "street_furniture": scene.get("street_furniture", ""),
                "vegetation": scene.get("vegetation", ""),
                "signage": scene.get("signage", ""),
                "ground_surfaces": scene.get("ground_surfaces", ""),
                "spatial_enclosure": scene.get("spatial_enclosure", ""),
                "pedestrian_activity": scene.get("pedestrian_activity", ""),
                "lighting_atmosphere": scene.get("lighting_atmosphere", ""),
                "as_resident": scene.get("as_resident", ""),
                "as_commuter": scene.get("as_commuter", ""),
                "as_tourist": scene.get("as_tourist", ""),
                "as_student": scene.get("as_student", ""),
            },
        })
    return {"type": "FeatureCollection", "features": features}


@app.get("/api/streetview_grid/image/{filename}")
async def get_streetview_image(filename: str):
    """Serve a street view grid image file."""
    filepath = SV_IMAGES_DIR / filename
    if not filepath.is_file():
        return {"error": "Image not found"}
    return FileResponse(filepath, media_type="image/jpeg")


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
        "available_providers": [
            {"id": "gemini", "name": "Google Gemini 2.0 Flash Lite", "description": "Fast, efficient cloud LLM by Google — no local setup required"},
            {"id": "ollama", "name": "Ollama (Local)", "description": "Local LLM via Ollama — no GPU required"},
            {"id": "vllm", "name": "vLLM (Docker GPU)", "description": "High-performance GPU inference via vLLM Docker"},
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
    # Stop any existing recording
    clear_recorder()
    
    # Create new recorder
    recorder = create_recorder(
        output_dir=PROJECT_ROOT / "Documentation",
        max_buffer_size=5000,
        include_thoughts=include_thoughts,
        include_perception=include_perception,
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
        "output_dir": str(PROJECT_ROOT / "Documentation"),
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("map_server:app", host="127.0.0.1", port=8000, reload=True)
