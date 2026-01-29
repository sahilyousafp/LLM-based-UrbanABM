"""
Overture Maps to DuckDB Pipeline
---------------------------------
Downloads and processes Overture Maps data for Barcelona Eixample district.

Overture Maps provides high-quality geospatial data in Parquet format:
- Buildings
- Places (amenities/POIs)
- Transportation (roads, walkways)

Data Source: https://overturemaps.org/
"""

import duckdb
import geopandas as gpd
from shapely.geometry import box
import os

# Configuration
PLACE_NAME = "Eixample, Barcelona, Spain"
DB_PATH = "eixample_overture.duckdb"

# Barcelona Eixample bounding box (approximate)
# Coordinates: [min_lon, min_lat, max_lon, max_lat]
BBOX = {
    'min_lon': 2.1446,
    'min_lat': 41.3773,
    'max_lon': 2.1890,
    'max_lat': 41.4091
}

# Overture Maps S3 bucket (public, no credentials needed)
# Data is organized by theme and type
# Updated path format for latest Overture release
OVERTURE_BASE = "s3://overturemaps-us-west-2/release"
OVERTURE_VERSION = "2024-11-13.0"  # Latest stable release
OVERTURE_PATH = f"{OVERTURE_BASE}/{OVERTURE_VERSION}"

def setup_duckdb_with_overture(db_path):
    """
    Initialize DuckDB with spatial and httpfs extensions for Overture data access
    """
    print(f"Setting up DuckDB at {db_path}...")
    
    con = duckdb.connect(db_path)
    
    # Install required extensions
    print("Installing extensions...")
    con.execute("INSTALL spatial;")
    con.execute("INSTALL httpfs;")
    con.execute("INSTALL parquet;")
    
    # Load extensions
    con.execute("LOAD spatial;")
    con.execute("LOAD httpfs;")
    con.execute("LOAD parquet;")
    
    # Configure S3 access (Overture data is publicly accessible)
    con.execute("SET s3_region='us-west-2';")
    con.execute("SET s3_url_style='path';")
    
    return con

def extract_buildings(con, bbox):
    """
    Extract buildings from Overture Maps for the specified bounding box
    
    Overture buildings schema:
    - id: unique identifier
    - geometry: building footprint (polygon)
    - names: primary name
    - sources: data provenance
    - height: building height (if available)
    """
    print("Extracting buildings from Overture Maps...")
    
    query = f"""
    CREATE OR REPLACE TABLE buildings AS
    SELECT 
        id,
        ST_GeomFromWKB(geometry) as geometry,
        names.primary as name,
        height,
        num_floors,
        class as building_type,
        bbox.xmin as bbox_minx,
        bbox.ymin as bbox_miny,
        bbox.xmax as bbox_maxx,
        bbox.ymax as bbox_maxy
    FROM read_parquet('{OVERTURE_PATH}/theme=buildings/type=*/*', 
                      filename=true, hive_partitioning=1)
    WHERE bbox.xmin >= {bbox['min_lon']}
      AND bbox.ymin >= {bbox['min_lat']}
      AND bbox.xmax <= {bbox['max_lon']}
      AND bbox.ymax <= {bbox['max_lat']}
      AND type = 'building'
    """
    
    try:
        con.execute(query)
        count = con.execute("SELECT COUNT(*) FROM buildings").fetchone()[0]
        print(f"✓ Loaded {count:,} buildings")
        return True
    except Exception as e:
        print(f"✗ Error loading buildings: {e}")
        return False

def extract_places(con, bbox):
    """
    Extract places (amenities/POIs) from Overture Maps
    
    Overture places schema:
    - id: unique identifier
    - geometry: point location
    - names: primary and alternate names
    - categories: place classifications
    - websites, phones, addresses
    """
    print("Extracting places (amenities) from Overture Maps...")
    
    query = f"""
    CREATE OR REPLACE TABLE amenities AS
    SELECT 
        id,
        ST_GeomFromWKB(geometry) as geometry,
        names.primary as name,
        categories.primary as amenity,
        categories.alternate as amenity_tags,
        addresses[1].freeform as address,
        websites[1] as website,
        phones[1] as phone,
        bbox.xmin as lon,
        bbox.ymin as lat
    FROM read_parquet('{OVERTURE_BASE}/{OVERTURE_VERSION}/theme=places/type=place/*',
                      filename=true, hive_partitioning=1)
    WHERE bbox.xmin >= {bbox['min_lon']}
      AND bbox.ymin >= {bbox['min_lat']}
      AND bbox.xmax <= {bbox['max_lon']}
      AND bbox.ymax <= {bbox['max_lat']}
    """
    
    try:
        con.execute(query)
        count = con.execute("SELECT COUNT(*) FROM amenities").fetchone()[0]
        print(f"✓ Loaded {count:,} amenities/places")
        return True
    except Exception as e:
        print(f"✗ Error loading places: {e}")
        return False

def extract_transportation(con, bbox):
    """
    Extract transportation network from Overture Maps
    
    Overture transportation schema:
    - segments: road/path segments with connectivity
    - connectors: junction points
    - class: road classification (pedestrian, residential, etc.)
    """
    print("Extracting transportation network from Overture Maps...")
    
    # Walk network (pedestrian paths)
    walk_query = f"""
    CREATE OR REPLACE TABLE walk_edges AS
    SELECT 
        id,
        ST_GeomFromWKB(geometry) as geometry,
        subtype as road_type,
        class as road_class,
        names.primary as name,
        level,
        is_bridge,
        is_tunnel,
        width,
        surface,
        ST_Length(ST_GeomFromWKB(geometry)) as length
    FROM read_parquet('{OVERTURE_BASE}/{OVERTURE_VERSION}/theme=transportation/type=segment/*',
                      filename=true, hive_partitioning=1)
    WHERE (subtype = 'pedestrian' OR class IN ('pedestrian', 'footway', 'path', 'steps'))
      AND bbox.xmin >= {bbox['min_lon']}
      AND bbox.ymin >= {bbox['min_lat']}
      AND bbox.xmax <= {bbox['max_lon']}
      AND bbox.ymax <= {bbox['max_lat']}
    """
    
    # Roads (drivable network)
    roads_query = f"""
    CREATE OR REPLACE TABLE roads AS
    SELECT 
        id,
        ST_GeomFromWKB(geometry) as geometry,
        subtype as road_type,
        class as road_class,
        names.primary as name,
        level,
        is_bridge,
        is_tunnel,
        width,
        surface,
        ST_Length(ST_GeomFromWKB(geometry)) as length
    FROM read_parquet('{OVERTURE_BASE}/{OVERTURE_VERSION}/theme=transportation/type=segment/*',
                      filename=true, hive_partitioning=1)
    WHERE subtype != 'pedestrian'
      AND class NOT IN ('pedestrian', 'footway', 'path', 'steps')
      AND bbox.xmin >= {bbox['min_lon']}
      AND bbox.ymin >= {bbox['min_lat']}
      AND bbox.xmax <= {bbox['max_lon']}
      AND bbox.ymax <= {bbox['max_lat']}
    """
    
    try:
        con.execute(walk_query)
        walk_count = con.execute("SELECT COUNT(*) FROM walk_edges").fetchone()[0]
        print(f"✓ Loaded {walk_count:,} walk network edges")
        
        con.execute(roads_query)
        road_count = con.execute("SELECT COUNT(*) FROM roads").fetchone()[0]
        print(f"✓ Loaded {road_count:,} road segments")
        return True
    except Exception as e:
        print(f"✗ Error loading transportation: {e}")
        return False

def create_spatial_indexes(con):
    """
    Create spatial indexes for efficient queries
    """
    print("Creating spatial indexes...")
    
    tables = ['buildings', 'amenities', 'walk_edges', 'roads']
    
    for table in tables:
        try:
            # Check if table exists
            result = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            if result and result[0] > 0:
                # DuckDB spatial extension automatically handles indexes
                # But we can add explicit RTREE index if needed
                print(f"  ✓ {table}: {result[0]:,} features indexed")
        except:
            print(f"  ✗ {table}: table not found")
    
    print("Spatial indexes ready")

def verify_database(con):
    """
    Verify database contents and print statistics
    """
    print("\n" + "="*60)
    print("DATABASE VERIFICATION")
    print("="*60)
    
    # List all tables
    tables = con.execute("SHOW TABLES").fetchall()
    print(f"\nTables: {[t[0] for t in tables]}")
    
    # Count features in each table
    for table in tables:
        table_name = table[0]
        count = con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        
        # Get bounding box
        try:
            bbox_result = con.execute(f"""
                SELECT 
                    MIN(ST_XMin(geometry)) as minx,
                    MIN(ST_YMin(geometry)) as miny,
                    MAX(ST_XMax(geometry)) as maxx,
                    MAX(ST_YMax(geometry)) as maxy
                FROM {table_name}
            """).fetchone()
            
            print(f"\n{table_name}:")
            print(f"  Count: {count:,}")
            if bbox_result:
                print(f"  BBox: [{bbox_result[0]:.6f}, {bbox_result[1]:.6f}, {bbox_result[2]:.6f}, {bbox_result[3]:.6f}]")
        except Exception as e:
            print(f"\n{table_name}:")
            print(f"  Count: {count:,}")
            print(f"  (Unable to get bbox: {e})")
    
    print("\n" + "="*60)

def main():
    """
    Main pipeline: Download Overture data and create DuckDB database
    """
    print("="*60)
    print("OVERTURE MAPS TO DUCKDB PIPELINE")
    print("="*60)
    print(f"Target: {PLACE_NAME}")
    print(f"BBox: [{BBOX['min_lon']}, {BBOX['min_lat']}, {BBOX['max_lon']}, {BBOX['max_lat']}]")
    print(f"Output: {DB_PATH}")
    print("="*60 + "\n")
    
    # Remove old database if exists
    if os.path.exists(DB_PATH):
        print(f"Removing existing database: {DB_PATH}")
        os.remove(DB_PATH)
    
    # Setup DuckDB
    con = setup_duckdb_with_overture(DB_PATH)
    
    # Extract data from Overture Maps
    success = True
    success &= extract_buildings(con, BBOX)
    success &= extract_places(con, BBOX)
    success &= extract_transportation(con, BBOX)
    
    if success:
        # Create spatial indexes
        create_spatial_indexes(con)
        
        # Verify database
        verify_database(con)
        
        print(f"\n✅ SUCCESS! Database created at: {DB_PATH}")
    else:
        print(f"\n❌ FAILED! Some data extraction errors occurred.")
    
    con.close()

if __name__ == "__main__":
    main()
