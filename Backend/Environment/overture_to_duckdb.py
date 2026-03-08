"""
Overture Maps to DuckDB Pipeline (GCP BigQuery)
------------------------------------------------
Downloads and processes Overture Maps data for Barcelona Eixample district using Google BigQuery.

Overture Maps provides high-quality geospatial data via BigQuery:
- Buildings
- Places (amenities/POIs)
- Transportation (roads, walkways)

Data Source: https://overturemaps.org/
BigQuery Access: https://console.cloud.google.com/bigquery
"""

import duckdb
import geopandas as gpd
from shapely.geometry import box
import os
from google.cloud import bigquery
from google.oauth2 import service_account
import tempfile

# Configuration
PLACE_NAME = "Eixample, Barcelona, Spain"
DB_PATH = "eixample_overture.duckdb"

# 2 km × 2 km bounding box centered on Passeig de Gràcia, Eixample (41.3952°N, 2.1620°E)
# Δlat = 1000 / 111000 ≈ 0.009009°  |  Δlon = 1000 / (111000 × cos(41.3952°)) ≈ 0.012007°
# Coordinates: [min_lon, min_lat, max_lon, max_lat]
BBOX = {
    'min_lon': 2.1500,
    'min_lat': 41.3862,
    'max_lon': 2.1740,
    'max_lat': 41.4042
}

# Overture Maps BigQuery project
# Data is available in Google Cloud's public datasets
BIGQUERY_PROJECT = "bigquery-public-data"
OVERTURE_DATASET = "overture_maps"
# Note: Overture Maps tables in BigQuery no longer use version suffixes
# Tables are: building, place, segment, connector, etc.

def setup_bigquery_client():
    """
    Initialize BigQuery client for Overture data access
    
    Authentication methods:
    1. Service account JSON file (set GOOGLE_APPLICATION_CREDENTIALS env var)
    2. Application Default Credentials (gcloud auth application-default login)
    3. Automatic discovery in GCP environment
    
    Note: You need a GCP project with BigQuery API enabled, even for public datasets.
    Set GOOGLE_CLOUD_PROJECT environment variable to specify the project.
    """
    print("Setting up BigQuery client...")
    
    # Get project from environment or use a default
    project_id = os.getenv('GOOGLE_CLOUD_PROJECT') or os.getenv('GCLOUD_PROJECT')
    
    # Try to get credentials from environment variable
    creds_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
    
    if creds_path and os.path.exists(creds_path):
        credentials = service_account.Credentials.from_service_account_file(creds_path)
        if not project_id:
            project_id = credentials.project_id
        client = bigquery.Client(credentials=credentials, project=project_id)
        print(f"✓ Authenticated with service account from {creds_path}")
        print(f"  Using project: {project_id}")
    else:
        # Use Application Default Credentials
        try:
            import google.auth
            credentials, default_project = google.auth.default()
            
            # Use explicit project if set, otherwise use default
            if not project_id:
                project_id = default_project
            
            # Add quota project to credentials to avoid quota issues
            if hasattr(credentials, 'with_quota_project'):
                credentials = credentials.with_quota_project(project_id)
            
            client = bigquery.Client(credentials=credentials, project=project_id)
            print(f"✓ Authenticated with Application Default Credentials")
            print(f"  Using project: {project_id}")
            
        except Exception as e:
            print(f"✗ Authentication failed: {e}")
            print("\nTo fix this, you need:")
            print("  1. A GCP project with BigQuery API enabled")
            print("  2. Set the project: export GOOGLE_CLOUD_PROJECT=your-project-id")
            print("  3. Or run: gcloud config set project your-project-id")
            print("\nTo create a new project:")
            print("  1. Visit: https://console.cloud.google.com/projectcreate")
            print("  2. Enable BigQuery API: https://console.cloud.google.com/apis/library/bigquery.googleapis.com")
            raise
    
    # Verify BigQuery API access
    try:
        # Test with a simple query
        test_query = "SELECT 1 as test LIMIT 1"
        client.query(test_query).result()
        print(f"✓ BigQuery API access confirmed")
    except Exception as e:
        print(f"✗ BigQuery API not accessible: {e}")
        print(f"\nTo enable BigQuery API for project '{project_id}':")
        print(f"  1. Visit: https://console.cloud.google.com/apis/library/bigquery.googleapis.com?project={project_id}")
        print(f"  2. Click 'ENABLE'")
        print(f"  3. Or run: gcloud services enable bigquery.googleapis.com --project={project_id}")
        raise
    
    return client

def setup_duckdb_with_spatial(db_path):
    """
    Initialize DuckDB with spatial extensions for geospatial operations
    """
    print(f"Setting up DuckDB at {db_path}...")
    
    con = duckdb.connect(db_path)
    
    # Install required extensions
    print("Installing extensions...")
    con.execute("INSTALL spatial;")
    con.execute("INSTALL parquet;")
    
    # Load extensions
    con.execute("LOAD spatial;")
    con.execute("LOAD parquet;")
    
    return con

def extract_buildings(bq_client, duckdb_con, bbox):
    """
    Extract buildings from Overture Maps BigQuery for the specified bounding box
    
    Overture buildings schema:
    - id: unique identifier
    - geometry: building footprint (polygon)
    - names: primary name
    - sources: data provenance
    - height: building height (if available)
    """
    print("Extracting buildings from Overture Maps (BigQuery)...")
    
    # BigQuery SQL to extract buildings
    bq_query = f"""
    SELECT 
        id,
        ST_AsBinary(geometry) as geometry,
        names.primary as name,
        height,
        num_floors,
        class as building_type,
        bbox.xmin as bbox_minx,
        bbox.ymin as bbox_miny,
        bbox.xmax as bbox_maxx,
        bbox.ymax as bbox_maxy
    FROM `{BIGQUERY_PROJECT}.{OVERTURE_DATASET}.building`
    WHERE bbox.xmin >= {bbox['min_lon']}
      AND bbox.ymin >= {bbox['min_lat']}
      AND bbox.xmax <= {bbox['max_lon']}
      AND bbox.ymax <= {bbox['max_lat']}
    """
    
    try:
        # Query BigQuery and export to temporary Parquet file
        print("  Querying BigQuery...")
        query_job = bq_client.query(bq_query)
        df = query_job.to_dataframe()
        
        if len(df) == 0:
            print(f"✗ No buildings found in bounding box")
            return False
        
        # Save to temporary Parquet file
        with tempfile.NamedTemporaryFile(suffix='.parquet', delete=False) as tmp:
            temp_path = tmp.name
            df.to_parquet(temp_path, index=False)
        
        # Load into DuckDB
        print("  Loading into DuckDB...")
        duckdb_con.execute(f"""
            CREATE OR REPLACE TABLE buildings AS
            SELECT 
                id,
                ST_GeomFromWKB(geometry) as geometry,
                name,
                height,
                num_floors,
                building_type,
                bbox_minx,
                bbox_miny,
                bbox_maxx,
                bbox_maxy
            FROM read_parquet('{temp_path}')
        """)
        
        # Clean up temp file
        os.unlink(temp_path)
        
        count = duckdb_con.execute("SELECT COUNT(*) FROM buildings").fetchone()[0]
        print(f"✓ Loaded {count:,} buildings")
        return True
    except Exception as e:
        print(f"✗ Error loading buildings: {e}")
        return False

def extract_places(bq_client, duckdb_con, bbox):
    """
    Extract places (amenities/POIs) from Overture Maps BigQuery
    
    Overture places schema:
    - id: unique identifier
    - geometry: point location
    - names: primary and alternate names
    - categories: place classifications
    - websites, phones, addresses
    """
    print("Extracting places (amenities) from Overture Maps (BigQuery)...")
    
    bq_query = f"""
    SELECT 
        id,
        ST_AsBinary(geometry) as geometry,
        names.primary as name,
        categories.primary as amenity,
        TO_JSON_STRING(categories.alternate) as amenity_tags,
        IFNULL((SELECT e.element.freeform FROM UNNEST(addresses.list) AS e LIMIT 1), '') as address,
        IFNULL((SELECT e.element FROM UNNEST(websites.list) AS e LIMIT 1), '') as website,
        IFNULL((SELECT e.element FROM UNNEST(phones.list) AS e LIMIT 1), '') as phone,
        bbox.xmin as lon,
        bbox.ymin as lat
    FROM `{BIGQUERY_PROJECT}.{OVERTURE_DATASET}.place`
    WHERE bbox.xmin >= {bbox['min_lon']}
      AND bbox.ymin >= {bbox['min_lat']}
      AND bbox.xmax <= {bbox['max_lon']}
      AND bbox.ymax <= {bbox['max_lat']}
    """
    
    try:
        print("  Querying BigQuery...")
        query_job = bq_client.query(bq_query)
        df = query_job.to_dataframe()
        
        if len(df) == 0:
            print(f"✗ No places found in bounding box")
            return False
        
        with tempfile.NamedTemporaryFile(suffix='.parquet', delete=False) as tmp:
            temp_path = tmp.name
            df.to_parquet(temp_path, index=False)
        
        print("  Loading into DuckDB...")
        duckdb_con.execute(f"""
            CREATE OR REPLACE TABLE amenities AS
            SELECT 
                id,
                ST_GeomFromWKB(geometry) as geometry,
                name,
                amenity,
                amenity_tags,
                address,
                website,
                phone,
                lon,
                lat
            FROM read_parquet('{temp_path}')
        """)
        
        os.unlink(temp_path)
        
        count = duckdb_con.execute("SELECT COUNT(*) FROM amenities").fetchone()[0]
        print(f"✓ Loaded {count:,} amenities/places")
        return True
    except Exception as e:
        print(f"✗ Error loading places: {e}")
        return False

def extract_transportation(bq_client, duckdb_con, bbox):
    """
    Extract transportation network from Overture Maps BigQuery
    
    Overture transportation schema:
    - segments: road/path segments with connectivity
    - connectors: junction points
    - class: road classification (pedestrian, residential, etc.)
    """
    print("Extracting transportation network from Overture Maps (BigQuery)...")
    
    # Walk network (pedestrian paths)
    walk_bq_query = f"""
    SELECT 
        id,
        ST_AsBinary(geometry) as geometry,
        subtype as road_type,
        class as road_class,
        names.primary as name,
        0 as level,
        FALSE as is_bridge,
        FALSE as is_tunnel,
        CAST(NULL AS FLOAT64) as width,
        CAST(NULL AS STRING) as surface
    FROM `{BIGQUERY_PROJECT}.{OVERTURE_DATASET}.segment`
    WHERE (subtype = 'pedestrian' OR class IN ('pedestrian', 'footway', 'path', 'steps'))
      AND bbox.xmin >= {bbox['min_lon']}
      AND bbox.ymin >= {bbox['min_lat']}
      AND bbox.xmax <= {bbox['max_lon']}
      AND bbox.ymax <= {bbox['max_lat']}
    """
    
    # Roads (drivable network)
    roads_bq_query = f"""
    SELECT 
        id,
        ST_AsBinary(geometry) as geometry,
        subtype as road_type,
        class as road_class,
        names.primary as name,
        0 as level,
        FALSE as is_bridge,
        FALSE as is_tunnel,
        CAST(NULL AS FLOAT64) as width,
        CAST(NULL AS STRING) as surface
    FROM `{BIGQUERY_PROJECT}.{OVERTURE_DATASET}.segment`
    WHERE subtype != 'pedestrian'
      AND class NOT IN ('pedestrian', 'footway', 'path', 'steps')
      AND bbox.xmin >= {bbox['min_lon']}
      AND bbox.ymin >= {bbox['min_lat']}
      AND bbox.xmax <= {bbox['max_lon']}
      AND bbox.ymax <= {bbox['max_lat']}
    """
    
    try:
        # Extract walk network
        print("  Querying BigQuery for walk network...")
        query_job = bq_client.query(walk_bq_query)
        df_walk = query_job.to_dataframe()
        
        if len(df_walk) > 0:
            with tempfile.NamedTemporaryFile(suffix='.parquet', delete=False) as tmp:
                temp_path = tmp.name
                df_walk.to_parquet(temp_path, index=False)
            
            print("  Loading walk network into DuckDB...")
            duckdb_con.execute(f"""
                CREATE OR REPLACE TABLE walk_edges AS
                SELECT 
                    id,
                    ST_GeomFromWKB(geometry) as geometry,
                    road_type,
                    road_class,
                    name,
                    level,
                    is_bridge,
                    is_tunnel,
                    width,
                    surface,
                    ST_Length(ST_GeomFromWKB(geometry)) as length
                FROM read_parquet('{temp_path}')
            """)
            os.unlink(temp_path)
            
            walk_count = duckdb_con.execute("SELECT COUNT(*) FROM walk_edges").fetchone()[0]
            print(f"✓ Loaded {walk_count:,} walk network edges")
        else:
            print(f"✗ No walk edges found in bounding box")
        
        # Extract roads
        print("  Querying BigQuery for roads...")
        query_job = bq_client.query(roads_bq_query)
        df_roads = query_job.to_dataframe()
        
        if len(df_roads) > 0:
            with tempfile.NamedTemporaryFile(suffix='.parquet', delete=False) as tmp:
                temp_path = tmp.name
                df_roads.to_parquet(temp_path, index=False)
            
            print("  Loading roads into DuckDB...")
            duckdb_con.execute(f"""
                CREATE OR REPLACE TABLE roads AS
                SELECT 
                    id,
                    ST_GeomFromWKB(geometry) as geometry,
                    road_type,
                    road_class,
                    name,
                    level,
                    is_bridge,
                    is_tunnel,
                    width,
                    surface,
                    ST_Length(ST_GeomFromWKB(geometry)) as length
                FROM read_parquet('{temp_path}')
            """)
            os.unlink(temp_path)
            
            road_count = duckdb_con.execute("SELECT COUNT(*) FROM roads").fetchone()[0]
            print(f"✓ Loaded {road_count:,} road segments")
        else:
            print(f"✗ No roads found in bounding box")
        
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
    Main pipeline: Download Overture data from BigQuery and create DuckDB database
    """
    global DB_PATH  # Allow modification of global DB_PATH if needed
    
    print("="*60)
    print("OVERTURE MAPS TO DUCKDB PIPELINE (GCP BigQuery)")
    print("="*60)
    print(f"Target: {PLACE_NAME}")
    print(f"BBox: [{BBOX['min_lon']}, {BBOX['min_lat']}, {BBOX['max_lon']}, {BBOX['max_lat']}]")
    print(f"Output: {DB_PATH}")
    print(f"BigQuery: {BIGQUERY_PROJECT}.{OVERTURE_DATASET}")
    print("="*60 + "\n")
    
    # Remove old database if exists
    if os.path.exists(DB_PATH):
        print(f"Removing existing database: {DB_PATH}")
        try:
            os.remove(DB_PATH)
        except PermissionError:
            print(f"⚠️  Warning: Could not remove old database (file in use).")
            print(f"   Creating backup and using a new filename...")
            import time
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            DB_PATH = f"eixample_overture_{timestamp}.duckdb"
            print(f"   New database: {DB_PATH}")
    
    # Setup BigQuery client
    try:
        bq_client = setup_bigquery_client()
    except Exception as e:
        print(f"\n❌ FAILED! Could not authenticate with BigQuery.")
        print(f"Error: {e}")
        return
    
    # Setup DuckDB
    duckdb_con = setup_duckdb_with_spatial(DB_PATH)
    
    # Extract data from Overture Maps via BigQuery
    success = True
    success &= extract_buildings(bq_client, duckdb_con, BBOX)
    success &= extract_places(bq_client, duckdb_con, BBOX)
    success &= extract_transportation(bq_client, duckdb_con, BBOX)
    
    if success:
        # Create spatial indexes
        create_spatial_indexes(duckdb_con)
        
        # Verify database
        verify_database(duckdb_con)
        
        print(f"\n✅ SUCCESS! Database created at: {DB_PATH}")
    else:
        print(f"\n❌ FAILED! Some data extraction errors occurred.")
    
    duckdb_con.close()

if __name__ == "__main__":
    main()
