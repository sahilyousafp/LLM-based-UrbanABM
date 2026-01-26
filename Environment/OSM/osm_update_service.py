"""
FastAPI service for live OSM data updates.
This service provides endpoints to trigger OSM data extraction and updates to DuckDB.
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, Dict, Any
import os
import sys
import logging
from datetime import datetime
from pathlib import Path
import duckdb
import osmnx as ox
import pandas as pd
import geopandas as gpd
from shapely import wkt

# Add project root to path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from config import get_db_path, DEFAULT_PLACE_NAME, OSM_SERVICE_PORT

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
DEFAULT_DB_PATH = get_db_path()

app = FastAPI(
    title="OSM Data Update Service",
    description="Live updates for OpenStreetMap data stored in DuckDB",
    version="1.0.0"
)

# Store update status
update_status = {
    "last_update": None,
    "status": "idle",
    "message": "",
    "place_name": DEFAULT_PLACE_NAME,
    "db_path": str(DEFAULT_DB_PATH)
}


class UpdateRequest(BaseModel):
    place_name: Optional[str] = DEFAULT_PLACE_NAME
    db_path: Optional[str] = None


def clean_and_prepare_gdf(gdf, geom_name='geometry'):
    """
    Prepares a GeoDataFrame for DuckDB:
    - Converts geometry to WKT (so DuckDB can parse it with ST_GeomFromText).
    - Converts other complex types (lists/dicts) to strings.
    """
    gdf = gdf.copy()
    
    if 'geometry' in gdf.columns:
        gdf['geom_wkt'] = gdf['geometry'].apply(lambda x: x.wkt if x else None)
        gdf = gdf.drop(columns=['geometry'])
    
    for col in gdf.columns:
        if gdf[col].dtype == 'object':
            gdf[col] = gdf[col].astype(str)
            
    return gdf


def extract_osm_data(place_name: str) -> Dict[str, Any]:
    """Extract OSM data for a given place."""
    logger.info(f"Extracting data for: {place_name}")
    
    try:
        # 1. Buildings (Polygons)
        tags_buildings = {'building': True}
        logger.info("Fetching buildings...")
        gdf_buildings = ox.features_from_place(place_name, tags=tags_buildings)
        
        # 2. Amenities / POIs (Points & Polygons)
        tags_amenities = {
            'amenity': True, 
            'shop': True, 
            'office': True, 
            'leisure': True,
            'craft': True,
            'emergency': True,
            'tourism': True
        }
        logger.info("Fetching amenities/POIs...")
        gdf_amenities = ox.features_from_place(place_name, tags=tags_amenities)
        
        # 3. Walk Network
        logger.info("Fetching walk network...")
        G_walk = ox.graph_from_place(place_name, network_type='walk')
        gdf_nodes_walk, gdf_edges_walk = ox.graph_to_gdfs(G_walk)
        
        # 4. Drive Network
        logger.info("Fetching drive network...")
        G_drive = ox.graph_from_place(place_name, network_type='drive')
        gdf_nodes_drive, gdf_edges_drive = ox.graph_to_gdfs(G_drive)
        
        return {
            'buildings': gdf_buildings,
            'amenities': gdf_amenities,
            'walk_nodes': gdf_nodes_walk,
            'walk_edges': gdf_edges_walk,
            'drive_nodes': gdf_nodes_drive,
            'drive_edges': gdf_edges_drive
        }
    except Exception as e:
        logger.error(f"Error extracting OSM data: {e}")
        raise


def setup_duckdb(db_path: str, data_dict: Dict[str, Any]) -> None:
    """Setup DuckDB with OSM data."""
    logger.info(f"Setting up DuckDB at {db_path}...")
    
    con = duckdb.connect(db_path)
    
    try:
        # Install and load spatial extension
        logger.info("Installing spatial extension...")
        con.install_extension("spatial")
        con.load_extension("spatial")
        
        for name, gdf in data_dict.items():
            logger.info(f"Loading table: {name} ({len(gdf)} rows)...")
            
            # Prepare data
            df_prepared = clean_and_prepare_gdf(gdf)
            
            # Register as standard pandas dataframe for DuckDB
            con.register(f"{name}_view", df_prepared)
            
            # Create table with Spatial type
            create_query = f"""
            CREATE OR REPLACE TABLE {name} AS 
            SELECT 
                * EXCLUDE (geom_wkt), 
                ST_GeomFromText(geom_wkt) AS geometry 
            FROM {name}_view
            """
            con.execute(create_query)
            con.unregister(f"{name}_view")
        
        # Verify tables
        tables = con.execute("SHOW TABLES").fetchall()
        logger.info(f"Tables created: {[t[0] for t in tables]}")
        
    finally:
        con.close()
    
    logger.info("Database setup complete.")


def perform_update(place_name: str, db_path: str) -> None:
    """Perform the actual OSM data update."""
    global update_status
    
    try:
        update_status["status"] = "updating"
        update_status["message"] = "Extracting OSM data..."
        update_status["place_name"] = place_name
        update_status["db_path"] = db_path
        
        # Extract data
        data = extract_osm_data(place_name)
        
        update_status["message"] = "Saving to DuckDB..."
        
        # Setup database
        setup_duckdb(db_path, data)
        
        # Success
        update_status["status"] = "completed"
        update_status["message"] = "Update completed successfully"
        update_status["last_update"] = datetime.now().isoformat()
        
        logger.info(f"Update completed for {place_name}")
        
    except Exception as e:
        update_status["status"] = "failed"
        update_status["message"] = f"Update failed: {str(e)}"
        logger.error(f"Update failed: {e}")
        raise


@app.get("/")
async def root():
    """Root endpoint with service information."""
    return {
        "service": "OSM Data Update Service",
        "version": "1.0.0",
        "status": update_status["status"],
        "endpoints": {
            "status": "/status",
            "update": "/update (POST)",
            "update_async": "/update/async (POST)",
            "db_stats": "/db/stats",
            "health": "/health"
        }
    }


@app.get("/status")
async def get_status():
    """Get the current update status."""
    return update_status


@app.post("/update")
async def trigger_update(request: UpdateRequest):
    """
    Trigger an immediate OSM data update (synchronous).
    Warning: This may take several minutes to complete.
    """
    place_name = request.place_name or DEFAULT_PLACE_NAME
    db_path = request.db_path or str(DEFAULT_DB_PATH)
    
    # Ensure the directory exists
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    
    try:
        perform_update(place_name, db_path)
        return {
            "status": "success",
            "message": "Update completed",
            "place_name": place_name,
            "db_path": db_path,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/update/async")
async def trigger_update_async(background_tasks: BackgroundTasks, request: UpdateRequest):
    """
    Trigger an OSM data update in the background (asynchronous).
    Returns immediately while the update runs in the background.
    Check /status to monitor progress.
    """
    place_name = request.place_name or DEFAULT_PLACE_NAME
    db_path = request.db_path or str(DEFAULT_DB_PATH)
    
    # Ensure the directory exists
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    
    # Check if an update is already running
    if update_status["status"] == "updating":
        raise HTTPException(
            status_code=409, 
            detail="An update is already in progress"
        )
    
    # Schedule the update in background
    background_tasks.add_task(perform_update, place_name, db_path)
    
    return {
        "status": "scheduled",
        "message": "Update scheduled in background",
        "place_name": place_name,
        "db_path": db_path,
        "check_status_at": "/status"
    }


@app.get("/db/stats")
async def get_db_stats():
    """Get statistics about the DuckDB database."""
    db_path = update_status["db_path"]
    
    if not os.path.exists(db_path):
        raise HTTPException(status_code=404, detail="Database file not found")
    
    try:
        con = duckdb.connect(db_path, read_only=True)
        con.install_extension("spatial")
        con.load_extension("spatial")
        
        # Get table information
        tables = con.execute("SHOW TABLES").fetchall()
        stats = {
            "db_path": db_path,
            "tables": {}
        }
        
        for table in tables:
            table_name = table[0]
            try:
                count = con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
                stats["tables"][table_name] = count
            except:
                stats["tables"][table_name] = 0
        
        con.close()
        
        return stats
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    db_path = update_status["db_path"]
    
    return {
        "status": "healthy",
        "database_exists": os.path.exists(db_path),
        "last_update": update_status["last_update"],
        "current_status": update_status["status"]
    }


if __name__ == "__main__":
    import uvicorn
    
    logger.info(f"Starting OSM Update Service on port {OSM_SERVICE_PORT}")
    logger.info(f"Default DB path: {DEFAULT_DB_PATH}")
    
    uvicorn.run(
        "osm_update_service:app", 
        host="0.0.0.0", 
        port=OSM_SERVICE_PORT, 
        reload=True
    )
