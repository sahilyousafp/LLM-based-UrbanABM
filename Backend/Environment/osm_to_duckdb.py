import osmnx as ox
import duckdb
import pandas as pd
import geopandas as gpd
from shapely import wkt
import os

# Configuration
PLACE_NAME = "Eixample, Barcelona, Spain"
DB_PATH = "Eixample_OSM.duckdb"
OUTPUT_DIR = "data"

# Ensure output directory exists
os.makedirs(OUTPUT_DIR, exist_ok=True)

def extract_osm_data(place_name):
    print(f"Extracting data for: {place_name}")
    
    # 1. Buildings (Polygons)
    tags_buildings = {'building': True}
    print("Fetching buildings...")
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
    print("Fetching amenities/POIs...")
    gdf_amenities = ox.features_from_place(place_name, tags=tags_amenities)
    
    # 3. Walk Network
    print("Fetching walk network...")
    G_walk = ox.graph_from_place(place_name, network_type='walk')
    gdf_nodes_walk, gdf_edges_walk = ox.graph_to_gdfs(G_walk)
    
    # 4. Drive Network
    print("Fetching drive network...")
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

def clean_and_prepare_gdf(gdf, geom_name='geometry'):
    """
    Prepares a GeoDataFrame for DuckDB:
    - Converts geometry to WKT (so DuckDB can parse it with ST_GeomFromText).
    - Converts other complex types (lists/dicts) to strings.
    """
    # Create a copy to avoid distinct warnings
    gdf = gdf.copy()
    
    # Convert geometry to WKT
    if 'geometry' in gdf.columns:
        gdf['geom_wkt'] = gdf['geometry'].apply(lambda x: x.wkt if x else None)
        # Drop original geometry column as DuckDB ingest might struggle with mixed object types or shapely objs
        gdf = gdf.drop(columns=['geometry'])
    
    # Convert list/dict columns to string to avoid serialization issues
    for col in gdf.columns:
        if gdf[col].dtype == 'object':
            gdf[col] = gdf[col].astype(str)
            
    return gdf

def setup_duckdb(db_path, data_dict):
    print(f"Setting up DuckDB at {db_path}...")
    
    # Connect to DuckDB
    con = duckdb.connect(db_path)
    
    # Install and load spatial extension
    print("Installing spatial extension...")
    con.install_extension("spatial")
    con.load_extension("spatial")
    
    for name, gdf in data_dict.items():
        print(f"Loading table: {name} ({len(gdf)} rows)...")
        
        # Prepare data
        df_prepared = clean_and_prepare_gdf(gdf)
        
        # Register as standard pandas dataframe for DuckDB
        con.register(f"{name}_view", df_prepared)
        
        # Create table with Spatial type
        # We explicitly convert the WKT column to a GEOMETRY type
        create_query = f"""
        CREATE OR REPLACE TABLE {name} AS 
        SELECT 
            * EXCLUDE (geom_wkt), 
            ST_GeomFromText(geom_wkt) AS geometry 
        FROM {name}_view
        """
        con.execute(create_query)
        con.unregister(f"{name}_view")
        
        # Create Spatial Index (RTREE) - DuckDB Spatial often does this automatically, 
        # but manual index creation on geometry columns is good practice if supported/needed.
        # Note: As of late versions, DuckDB Spatial creates min-max indexes. 
        # Explicit RTREE index support syntax can vary, but standard indexes work on bounding boxes.
        # For now, we trust the spatial extension's storage model.
        
    # Verify tables
    tables = con.execute("SHOW TABLES").fetchall()
    print("Tables created:", [t[0] for t in tables])
    
    con.close()
    print("Database setup complete.")

if __name__ == "__main__":
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        
    data = extract_osm_data(PLACE_NAME)
    setup_duckdb(DB_PATH, data)
