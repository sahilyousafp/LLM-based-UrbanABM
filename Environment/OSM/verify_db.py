import duckdb
import os

DB_PATH = "Eixample_OSM.duckdb"

def verify_database():
    if not os.path.exists(DB_PATH):
        print(f"Error: Database {DB_PATH} not found.")
        return

    print(f"Connecting to {DB_PATH}...")
    con = duckdb.connect(DB_PATH)
    con.install_extension("spatial")
    con.load_extension("spatial")

    # 1. Check Tables
    tables = con.execute("SHOW TABLES").fetchall()
    table_names = [t[0] for t in tables]
    print(f"Found tables: {table_names}")

    # 2. Check counts
    for table in table_names:
        count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"Table '{table}' has {count} rows.")

    # 3. Sample Spatial Query
    print("\nRunning sample spatial query: Find amenities within 50m of a sample building.")
    
    # Get a sample building
    try:
        sample_building = con.execute("SELECT geometry FROM buildings LIMIT 1").fetchone()[0]
        # Note: We need to handle how python receives the geometry. 
        # By default it might be binary WKB. 
        # Let's use ST_AsText in query to see WKT.
        
        sample_wkt_res = con.execute("SELECT ST_AsText(geometry) FROM buildings LIMIT 1").fetchone()
        if sample_wkt_res:
            sample_wkt = sample_wkt_res[0]
            print(f"Sample Building WKT (truncated): {sample_wkt[:50]}...")
            
            # Simple intersection or distance query
            query = f"""
            SELECT type, name, ST_Distance(geometry, ST_GeomFromText('{sample_wkt}')) as dist
            FROM amenities
            WHERE ST_DWithin(geometry, ST_GeomFromText('{sample_wkt}'), 0.001) -- using degrees roughly, or project first.
            LIMIT 5;
            """
            # Note: OSM data is usually lat/lon (EPSG:4326). ST_DWithin in degrees? 
            # DuckDB spatial handles planar mostly unless spherical geography is specified.
            # 0.001 degrees is roughly 100m.
            
            results = con.execute(query).fetchall()
            print("Nearby amenities:", results)
    except Exception as e:
        print(f"Query check failed: {e}")

    con.close()

if __name__ == "__main__":
    verify_database()
