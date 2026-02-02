# How to Inspect and Modify DuckDB Data

## Quick Reference: View Your Current OSM Data

You have a DuckDB database file: `eixample_overture.duckdb`

Here's how to inspect and modify it:

---

## Method 1: Python Interactive Session (Recommended)

### Quick Inspection Script

Create this file to quickly explore your data:

```python
# inspect_duckdb.py
import duckdb

# Connect to your existing database
con = duckdb.connect('eixample_overture.duckdb')

# Load spatial extension
con.execute("LOAD spatial;")

print("=" * 60)
print("DuckDB Database Inspection")
print("=" * 60)

# 1. List all tables
print("\n📊 Available Tables:")
tables = con.execute("SHOW TABLES;").fetchall()
for table in tables:
    print(f"  - {table[0]}")

# 2. Get row counts
print("\n📈 Row Counts:")
for table in tables:
    table_name = table[0]
    count = con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
    print(f"  {table_name}: {count:,} rows")

# 3. Show table schemas
print("\n🏗️ Table Structures:")
for table in tables:
    table_name = table[0]
    print(f"\n  Table: {table_name}")
    schema = con.execute(f"DESCRIBE {table_name}").fetchall()
    for col in schema[:10]:  # Show first 10 columns
        print(f"    - {col[0]}: {col[1]}")
    if len(schema) > 10:
        print(f"    ... and {len(schema) - 10} more columns")

# 4. Preview data from each table
print("\n👀 Data Preview:")
for table in tables:
    table_name = table[0]
    print(f"\n  {table_name} (first 3 rows):")
    preview = con.execute(f"SELECT * FROM {table_name} LIMIT 3").fetchall()
    for row in preview:
        print(f"    {row[:5]}...")  # Show first 5 columns

print("\n" + "=" * 60)
print("✓ Inspection complete!")
print("=" * 60)

con.close()
```

**Run it:**
```bash
python inspect_duckdb.py
```

---

## Method 2: Interactive Python Shell

```python
import duckdb
import pandas as pd

# Connect
con = duckdb.connect('eixample_overture.duckdb')
con.execute("LOAD spatial;")

# ====================
# EXPLORE TABLES
# ====================

# List all tables
con.execute("SHOW TABLES;").fetchdf()

# Get table info
con.execute("DESCRIBE buildings;").fetchdf()
con.execute("DESCRIBE amenities;").fetchdf()
con.execute("DESCRIBE walk_edges;").fetchdf()

# ====================
# VIEW DATA
# ====================

# Preview buildings
buildings = con.execute("""
    SELECT 
        rowid,
        ST_AsText(geometry) as geometry_wkt,
        *
    FROM buildings 
    LIMIT 10
""").fetchdf()
print(buildings)

# Preview amenities
amenities = con.execute("""
    SELECT 
        name,
        amenity,
        ST_AsText(geometry) as location
    FROM amenities 
    LIMIT 20
""").fetchdf()
print(amenities)

# Preview walk network
network = con.execute("""
    SELECT 
        rowid,
        ST_Length(geometry) as length_degrees,
        ST_AsText(ST_StartPoint(geometry)) as start_point,
        ST_AsText(ST_EndPoint(geometry)) as end_point
    FROM walk_edges 
    LIMIT 10
""").fetchdf()
print(network)

# ====================
# STATISTICS
# ====================

# Count by amenity type
stats = con.execute("""
    SELECT 
        amenity,
        COUNT(*) as count
    FROM amenities
    GROUP BY amenity
    ORDER BY count DESC
    LIMIT 20
""").fetchdf()
print("\nAmenity Types:")
print(stats)

# Building statistics
building_stats = con.execute("""
    SELECT 
        COUNT(*) as total_buildings,
        ROUND(AVG(ST_Area(geometry)), 8) as avg_area_deg,
        ROUND(SUM(ST_Area(geometry)), 8) as total_area_deg
    FROM buildings
""").fetchdf()
print("\nBuilding Stats:")
print(building_stats)

# Network statistics
network_stats = con.execute("""
    SELECT 
        COUNT(*) as total_edges,
        ROUND(SUM(ST_Length(geometry)) * 111, 2) as total_length_km,
        ROUND(AVG(ST_Length(geometry)) * 111000, 2) as avg_length_m
    FROM walk_edges
""").fetchdf()
print("\nNetwork Stats:")
print(network_stats)
```

---

## Method 3: DuckDB CLI (Command Line)

### Install DuckDB CLI
```bash
# Download from: https://duckdb.org/docs/installation/
# Or via Python:
pip install duckdb

# Access via Python module
python -m duckdb eixample_overture.duckdb
```

### CLI Commands
```sql
-- Load spatial extension
LOAD spatial;

-- Show all tables
SHOW TABLES;

-- Describe table structure
DESCRIBE buildings;
DESCRIBE amenities;
DESCRIBE walk_edges;

-- Count rows
SELECT COUNT(*) FROM buildings;
SELECT COUNT(*) FROM amenities;
SELECT COUNT(*) FROM walk_edges;

-- Preview data
SELECT * FROM amenities LIMIT 10;

-- Group by amenity type
SELECT amenity, COUNT(*) as count 
FROM amenities 
GROUP BY amenity 
ORDER BY count DESC;

-- Find specific amenities
SELECT name, amenity, ST_AsText(geometry) 
FROM amenities 
WHERE amenity = 'cafe' 
LIMIT 10;

-- Exit
.quit
```

---

## How to MODIFY/CHANGE Data

### Option 1: Add New Data

```python
import duckdb
from shapely.geometry import Point

con = duckdb.connect('eixample_overture.duckdb')
con.execute("LOAD spatial;")

# Add a new amenity
con.execute("""
    INSERT INTO amenities (name, amenity, geometry)
    VALUES (
        'My Custom Cafe',
        'cafe',
        ST_GeomFromText('POINT(2.1734 41.3851)')
    )
""")

# Verify it was added
result = con.execute("""
    SELECT name, amenity 
    FROM amenities 
    WHERE name = 'My Custom Cafe'
""").fetchall()
print(result)

con.close()
```

### Option 2: Update Existing Data

```python
import duckdb

con = duckdb.connect('eixample_overture.duckdb')
con.execute("LOAD spatial;")

# Update amenity name
con.execute("""
    UPDATE amenities 
    SET name = 'Updated Cafe Name'
    WHERE name = 'Old Cafe Name'
""")

# Update multiple records
con.execute("""
    UPDATE amenities 
    SET amenity = 'restaurant'
    WHERE amenity = 'cafe' AND name LIKE '%Restaurant%'
""")

con.close()
```

### Option 3: Delete Data

```python
import duckdb

con = duckdb.connect('eixample_overture.duckdb')
con.execute("LOAD spatial;")

# Delete specific amenities
con.execute("""
    DELETE FROM amenities 
    WHERE amenity = 'parking'
""")

# Delete by spatial query (outside a radius)
con.execute("""
    DELETE FROM amenities 
    WHERE NOT ST_DWithin(
        geometry,
        ST_GeomFromText('POINT(2.1734 41.3851)'),
        0.01  -- Keep only within ~1km
    )
""")

con.close()
```

### Option 4: Replace Entire Dataset

```python
import duckdb
import geopandas as gpd

con = duckdb.connect('eixample_overture.duckdb')
con.execute("LOAD spatial;")

# Drop existing table
con.execute("DROP TABLE IF EXISTS amenities;")

# Create new table from GeoDataFrame
gdf = gpd.read_file('new_amenities.geojson')
con.execute("""
    CREATE TABLE amenities AS 
    SELECT * FROM gdf
""")

con.close()
```

---

## Common Queries for Your ABM

### 1. Find Amenities Near a Point

```python
import duckdb

con = duckdb.connect('eixample_overture.duckdb')
con.execute("LOAD spatial;")

# Agent location
agent_lon, agent_lat = 2.1734, 41.3851

# Find nearby amenities (100m radius)
nearby = con.execute(f"""
    SELECT 
        name,
        amenity,
        ST_Distance(
            geometry,
            ST_GeomFromText('POINT({agent_lon} {agent_lat})')
        ) * 111000 as distance_m
    FROM amenities
    WHERE ST_DWithin(
        geometry,
        ST_GeomFromText('POINT({agent_lon} {agent_lat})'),
        0.001  -- ~100m in degrees
    )
    ORDER BY distance_m
    LIMIT 20
""").fetchdf()

print(nearby)
```

### 2. Find Buildings at Location

```python
# Find building containing point
building = con.execute(f"""
    SELECT 
        rowid,
        ST_Area(geometry) * 111000 * 111000 as area_sqm
    FROM buildings
    WHERE ST_Contains(
        geometry,
        ST_GeomFromText('POINT({agent_lon} {agent_lat})')
    )
    LIMIT 1
""").fetchdf()

print(building)
```

### 3. Find Connected Edges (for Agent Movement)

```python
# Find edges starting near a point
edges = con.execute(f"""
    SELECT 
        rowid,
        ST_AsText(geometry) as edge_wkt,
        ST_Length(geometry) * 111000 as length_m
    FROM walk_edges
    WHERE ST_DWithin(
        ST_StartPoint(geometry),
        ST_GeomFromText('POINT({agent_lon} {agent_lat})'),
        0.0001  -- ~10m tolerance
    )
""").fetchdf()

print(edges)
```

---

## Complete Inspection Script with Export

```python
#!/usr/bin/env python3
"""
Complete DuckDB inspection and export tool
Usage: python inspect_and_export.py
"""

import duckdb
import pandas as pd
import json

def inspect_duckdb(db_path='eixample_overture.duckdb'):
    """Comprehensive database inspection"""
    
    con = duckdb.connect(db_path)
    con.execute("LOAD spatial;")
    
    report = {}
    
    # Get tables
    tables = con.execute("SHOW TABLES;").fetchdf()
    report['tables'] = tables['name'].tolist()
    
    # Get stats for each table
    report['stats'] = {}
    for table in report['tables']:
        # Row count
        count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        
        # Schema
        schema = con.execute(f"DESCRIBE {table}").fetchdf()
        
        # Sample data
        sample = con.execute(f"SELECT * FROM {table} LIMIT 5").fetchdf()
        
        report['stats'][table] = {
            'row_count': count,
            'columns': schema['column_name'].tolist(),
            'sample': sample.to_dict('records')
        }
    
    # Export report
    print("=" * 60)
    print("DUCKDB INSPECTION REPORT")
    print("=" * 60)
    print(f"\nDatabase: {db_path}")
    print(f"Tables: {len(report['tables'])}")
    
    for table, stats in report['stats'].items():
        print(f"\n📊 {table}")
        print(f"   Rows: {stats['row_count']:,}")
        print(f"   Columns: {', '.join(stats['columns'][:5])}...")
    
    # Save to JSON
    with open('duckdb_report.json', 'w') as f:
        # Convert sample data to string (can't serialize complex types)
        for table in report['stats']:
            report['stats'][table]['sample'] = str(report['stats'][table]['sample'][:3])
        json.dump(report, f, indent=2)
    
    print("\n✓ Report saved to: duckdb_report.json")
    
    # Export each table to CSV
    print("\n📁 Exporting tables to CSV...")
    for table in report['tables']:
        con.execute(f"""
            COPY (
                SELECT * EXCLUDE geometry, 
                       ST_AsText(geometry) as geometry_wkt 
                FROM {table} 
                LIMIT 1000
            ) TO '{table}_export.csv' (HEADER, DELIMITER ',');
        """)
        print(f"   ✓ {table}_export.csv")
    
    con.close()
    print("\n" + "=" * 60)
    print("✓ Inspection complete!")
    print("=" * 60)

if __name__ == "__main__":
    inspect_duckdb()
```

**Run it:**
```bash
python inspect_and_export.py
```

**Output:**
- Console report with statistics
- `duckdb_report.json` - Full analysis
- `buildings_export.csv` - Buildings data
- `amenities_export.csv` - Amenities data
- `walk_edges_export.csv` - Network data

---

## Quick Cheat Sheet

| Task | Command |
|------|---------|
| **Connect** | `con = duckdb.connect('eixample_overture.duckdb')` |
| **List tables** | `con.execute("SHOW TABLES;").fetchdf()` |
| **Count rows** | `con.execute("SELECT COUNT(*) FROM amenities").fetchone()` |
| **Preview data** | `con.execute("SELECT * FROM amenities LIMIT 10").fetchdf()` |
| **Export to CSV** | `con.execute("COPY amenities TO 'out.csv' (HEADER)")` |
| **Add row** | `con.execute("INSERT INTO amenities VALUES (...)")` |
| **Update row** | `con.execute("UPDATE amenities SET name='X' WHERE id=1")` |
| **Delete rows** | `con.execute("DELETE FROM amenities WHERE amenity='X'")` |
| **Spatial query** | `con.execute("SELECT * FROM amenities WHERE ST_DWithin(...)")` |

---

## Need to Rebuild from Scratch?

If you want to completely replace the data:

```python
import duckdb

# Option 1: Delete the file and recreate
import os
if os.path.exists('eixample_overture.duckdb'):
    os.remove('eixample_overture.duckdb')

# Option 2: Drop tables and reimport
con = duckdb.connect('eixample_overture.duckdb')
con.execute("DROP TABLE IF EXISTS buildings;")
con.execute("DROP TABLE IF EXISTS amenities;")
con.execute("DROP TABLE IF EXISTS walk_edges;")

# Then run your data extraction script again
# (e.g., the script from GCP_BIGQUERY_ACCESS_GUIDE.md)
```

---

## Integration with Your ABM

Your Mesa model should query DuckDB like this:

```python
# In Backend/Agent/model.py

import duckdb

class CityModel(mg.GeoModel):
    def __init__(self, ...):
        # Connect to DuckDB
        self.con = duckdb.connect('eixample_overture.duckdb')
        self.con.execute("LOAD spatial;")
        
        # Load data...
        
    def get_nearby_amenities(self, point_geom):
        """Query amenities near agent"""
        query = f"""
        SELECT 
            name,
            amenity,
            ST_Distance(geometry, ST_GeomFromText('POINT({point_geom.x} {point_geom.y})')) * 111000 as dist_m
        FROM amenities
        WHERE ST_DWithin(
            geometry,
            ST_GeomFromText('POINT({point_geom.x} {point_geom.y})'),
            0.001
        )
        ORDER BY dist_m
        LIMIT 20
        """
        results = self.con.execute(query).fetchall()
        return [{'name': r[0], 'type': r[1], 'dist': r[2]} for r in results]
```

---

**Ready to explore your data?** Run the inspection script above and you'll see everything in your database!
