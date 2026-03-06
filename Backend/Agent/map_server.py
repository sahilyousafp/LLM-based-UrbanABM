from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import duckdb
from shapely import wkt
from model import CityModel
import sys
from pathlib import Path

# Ensure Backend root on path (model.py also does this, but be explicit here)
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import Mapillary service
from mapillary import MapillaryService

app = FastAPI(title="Urban ABM Backend API")

# Configure CORS to allow frontend to fetch from backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database path - use absolute path from project root
PROJECT_ROOT = Path(__file__).parent.parent.parent
DB_PATH = PROJECT_ROOT / "Backend" / "Environment" / "eixample_overture.duckdb"

# Initialize Mesa model with agents (LLMClient is initialised inside CityModel)
city_model = CityModel(num_agents=500)

# Initialize Mapillary service
MAPILLARY_API_KEY = "MLY|33533093396335529|30cb7c42be1a23189b63952f439551bd"
mapillary_service = MapillaryService(MAPILLARY_API_KEY)

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
            "/api/step_continuous (POST)",
            "/api/step (POST)"
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
        "nearby_amenities": agent.nearby_amenities
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

    messages = [
        {"role": "system", "content": "You are narrating an urban simulation agent in Barcelona Eixample. Be concise (2-3 sentences)."},
        {"role": "user", "content": (
            f"Agent {agent_id} is a {profile.get('archetype','pedestrian')} at "
            f"lon={agent.geometry.x:.5f}, lat={agent.geometry.y:.5f}. "
            f"Needs: hunger={needs.get('hunger',0.5):.2f}, energy={needs.get('energy',1.0):.2f}, social={needs.get('social',0.5):.2f}. "
            f"Mood: {cognition.get('mood','neutral')}. "
            f"Nearby: {', '.join(a.get('type','?') for a in agent.nearby_amenities[:5]) or 'nothing notable'}. "
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

@app.get("/api/agent/{agent_id}/streetview")
async def get_agent_streetview(agent_id: int):
    """Get Mapillary street view images near the agent's location"""
    # Find the agent
    agent = next((a for a in city_model.city_agents if a.unique_id == agent_id), None)
    
    if not agent:
        return {"error": "Agent not found"}
    
    # Fetch Mapillary images near agent's location
    images = mapillary_service.get_images_near_location(
        lon=agent.geometry.x,
        lat=agent.geometry.y,
        radius=50  # 50 meter radius
    )
    
    return {
        "agent_id": agent.unique_id,
        "location": {
            "lon": agent.geometry.x,
            "lat": agent.geometry.y
        },
        "images": images,
        "image_count": len(images)
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


@app.get("/api/llm/stats")
async def get_llm_stats():
    """Return LLM usage statistics (token counts, latency)."""
    return city_model.llm_client.stats()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("map_server:app", host="127.0.0.1", port=8000, reload=True)
