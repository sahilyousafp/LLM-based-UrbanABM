from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import duckdb
from shapely import wkt
import json
from pathlib import Path
from OSM_model import CityModel

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Use absolute path for templates directory
SCRIPT_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(SCRIPT_DIR / "templates"))
# Disable template caching for development
templates.env.auto_reload = True
templates.env.cache_size = 0

# Database path
DB_PATH = r"..\Environment\eixample_osm.duckdb"

# Initialize Mesa model with agents
city_model = CityModel(num_agents=200)

def get_db_connection():
    """Get DuckDB connection"""
    con = duckdb.connect(DB_PATH, read_only=True)
    con.install_extension("spatial")
    con.load_extension("spatial")
    return con

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """Main page with map"""
    return templates.TemplateResponse("map.html", {"request": request})

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
        # Check if drive_edges table exists
        tables = con.execute("SHOW TABLES").fetchall()
        table_names = [t[0] for t in tables]
        
        print(f"Available tables: {table_names}")
        
        if 'drive_edges' in table_names:
            query = "SELECT ST_AsText(geometry) as wkt FROM drive_edges"
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
        else:
            print("No drive_edges table found")
            return {
                "type": "FeatureCollection",
                "features": []
            }
    finally:
        con.close()

@app.get("/api/agents")
async def get_agents():
    """Get agents as GeoJSON with their current query results"""
    features = []
    for agent in city_model.city_agents:
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [agent.geometry.x, agent.geometry.y]
            },
            "properties": {
                "id": agent.unique_id,
                "type": agent.agent_type,
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

@app.post("/api/step")
async def step_simulation():
    """Step the simulation forward"""
    city_model.step()
    return {
        "step": city_model.steps,
        "agents": len(city_model.city_agents)
    }

@app.post("/api/step_continuous")
async def step_continuous():
    """Step simulation and return updated agent positions"""
    city_model.step()
    
    # Return updated agent positions
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
        "agents": agents_data
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
        query = "SELECT name, amenity, ST_AsText(geometry) as wkt FROM amenities"
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("OSM_map_server:app", host="127.0.0.1", port=8000, reload=True)
