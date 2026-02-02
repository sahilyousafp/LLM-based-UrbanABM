# How to Access Overture Maps and OSM Data in GCP BigQuery

## Problem: Can't Find Overture Maps in GCP Console

**Common Issue:** Overture Maps data is **NOT yet available** as a public dataset in BigQuery as of January 2026. You need to import it manually.

## Current Status of Geospatial Data in BigQuery

### ✅ Available Public Datasets (You Can Access Now)

1. **OpenStreetMap (OSM)** - Available in BigQuery
2. **US Census Data** - Available
3. **NOAA Weather** - Available
4. **Geo Boundaries** - Available

### ❌ NOT Available as Public Dataset

- **Overture Maps** - Must be downloaded and imported manually
- You need to download from Overture's cloud buckets and load into your own BigQuery project

---

## Method 1: Access OSM Data in BigQuery (Available Now)

### Step-by-Step Navigation:

1. **Go to BigQuery Console:**
   - Visit: https://console.cloud.google.com/bigquery
   - Sign in with your Google account

2. **Enable BigQuery API** (if first time):
   - You may be prompted to enable the API
   - Click "Enable API" button
   - May require creating a project (free tier available)

3. **Access OSM Public Dataset:**
   
   **Option A: Through Explorer Panel**
   ```
   BigQuery Console → Left Panel → "Explorer"
   → Click "+ ADD DATA" button
   → Select "Public Datasets"
   → Search for "OpenStreetMap"
   → Click "geo_openstreetmap"
   → Click "VIEW DATASET"
   ```

   **Option B: Direct SQL Query**
   - Click "Compose New Query" button
   - Paste this query:

   ```sql
   -- List all OSM tables
   SELECT 
     table_name,
     ROUND(size_bytes/1024/1024/1024, 2) as size_gb
   FROM `bigquery-public-data.geo_openstreetmap.__TABLES__`
   ORDER BY size_gb DESC;
   ```

   - Click "RUN" button

4. **Available OSM Tables:**
   ```
   bigquery-public-data.geo_openstreetmap.
   ├── planet_features        (Main table - 500GB)
   ├── planet_nodes           (Points - 350GB)
   ├── planet_ways            (Lines/Polygons - 80GB)
   ├── planet_relations       (Complex features - 5GB)
   └── planet_history         (Historical data - 2TB)
   ```

5. **Test Query - Barcelona Buildings:**
   ```sql
   SELECT 
     way_id,
     ST_ASTEXT(ST_CENTROID(ST_GEOGFROMTEXT(geometry))) as center_point,
     (SELECT value FROM UNNEST(all_tags) WHERE key = 'building') as building_type
   FROM `bigquery-public-data.geo_openstreetmap.planet_features`
   WHERE 
     feature_type = 'multipolygons'
     AND EXISTS(SELECT 1 FROM UNNEST(all_tags) WHERE key = 'building')
     AND ST_DWITHIN(
       ST_GEOGFROMTEXT(geometry),
       ST_GEOGPOINT(2.1734, 41.3851),  -- Barcelona center
       5000  -- 5km radius
     )
   LIMIT 100;
   ```

   **Expected Cost:** ~$0.50 (scans ~100GB)
   
   ⚠️ **Warning:** First click "Query Validator" to see estimated cost before running!

---

## Method 2: Import Overture Maps Data (Manual Process)

Since Overture is not a BigQuery public dataset yet, you must import it yourself.

### Option A: Download and Import via Cloud Storage

#### Step 1: Download Overture Data

Overture hosts data in **AWS S3** and **Azure Blob Storage** (not GCP native):

```bash
# Install AWS CLI
pip install awscli

# Download Overture data (Parquet format)
# Example: Buildings theme for Barcelona region
aws s3 cp \
  s3://overturemaps-us-west-2/release/2024-01-17-alpha.0/theme=buildings/ \
  ./overture_buildings/ \
  --recursive \
  --no-sign-request
```

**Direct Download Links:**
- Overture Downloads: https://overturemaps.org/download/
- Data Explorer: https://explore.overturemaps.org/

#### Step 2: Upload to Google Cloud Storage

```bash
# Install Google Cloud SDK
# Download from: https://cloud.google.com/sdk/docs/install

# Authenticate
gcloud auth login

# Create a bucket
gsutil mb gs://your-overture-data/

# Upload Parquet files
gsutil -m cp -r ./overture_buildings gs://your-overture-data/
```

#### Step 3: Create BigQuery Table from Cloud Storage

```sql
-- In BigQuery Console, create external table
CREATE OR REPLACE EXTERNAL TABLE `your-project.overture.buildings`
OPTIONS (
  format = 'PARQUET',
  uris = ['gs://your-overture-data/overture_buildings/*.parquet']
);

-- Query the imported data
SELECT 
  id,
  ST_GEOGFROMTEXT(geometry) as geography,
  height,
  class
FROM `your-project.overture.buildings`
LIMIT 100;
```

**Estimated Cost:**
- Download: Free (Overture provides data free)
- GCS Storage: ~$0.02/GB/month
- BigQuery queries: $5/TB scanned

### Option B: Use DuckDB with Overture (Easier Alternative)

Instead of BigQuery, use DuckDB locally (NO cloud costs):

```python
import duckdb

# Connect to DuckDB
con = duckdb.connect('overture_data.duckdb')
con.execute("INSTALL spatial; LOAD spatial;")
con.execute("INSTALL httpfs; LOAD httpfs;")

# Query Overture data directly from S3 (no download needed!)
query = """
SELECT 
  id,
  geometry,
  names,
  categories,
  confidence
FROM read_parquet('s3://overturemaps-us-west-2/release/2024-01-17-alpha.0/theme=places/type=place/*', 
                  hive_partitioning=1)
WHERE 
  bbox.minX > 2.05 AND bbox.maxX < 2.25
  AND bbox.minY > 41.30 AND bbox.maxY < 41.47
LIMIT 1000;
"""

results = con.execute(query).fetchdf()
print(results.head())
```

**Advantages:**
- ✅ No cloud costs
- ✅ No data download required
- ✅ Direct S3 access
- ✅ Fast local queries
- ✅ No GCP account needed

---

## Method 3: Pre-Processed Overture Data (Community Sources)

Some organizations provide Overture data pre-loaded in BigQuery:

### Carto's Overture Data (May Require Subscription)
- Website: https://carto.com/spatial-data-catalog/browser/dataset/over_4382ad41/
- Provides Overture data in BigQuery-compatible format
- May have free tier or trial

### Other Options:
- **Felt.com** - Interactive Overture map viewer
- **Protomaps** - Vector tiles from Overture
- **OpenMapTiles** - Hybrid OSM + Overture tiles

---

## Recommended Workflow for Your ABM Project

### Hybrid Approach (Best for Research):

```
┌─────────────────────────────────────────────────┐
│ Step 1: Use BigQuery for OSM Data (Available)   │
│                                                 │
│  • Extract Barcelona buildings, amenities       │
│  • Export to CSV/GeoJSON                        │
│  • Cost: ~$2-5 for full Barcelona extraction    │
└─────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────┐
│ Step 2: Use DuckDB for Overture (Local)        │
│                                                 │
│  • Query S3 directly (no download)              │
│  • Filter Barcelona bounding box                │
│  • Cost: FREE                                   │
└─────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────┐
│ Step 3: Merge in Python                        │
│                                                 │
│  • Spatial conflation script                    │
│  • Combine OSM + Overture features              │
│  • Create hybrid dataset                        │
└─────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────┐
│ Step 4: Load into Local DuckDB                 │
│                                                 │
│  • Import merged data                           │
│  • Create spatial indices                       │
│  • Use for ABM real-time queries                │
└─────────────────────────────────────────────────┘
```

---

## Complete Python Script: OSM (BigQuery) + Overture (DuckDB)

```python
#!/usr/bin/env python3
"""
Hybrid data extraction: OSM from BigQuery + Overture from S3
"""

import duckdb
from google.cloud import bigquery
import pandas as pd
import geopandas as gpd
from shapely import wkt
import json

# ============================================
# Step 1: Extract OSM data from BigQuery
# ============================================

def extract_osm_buildings():
    """Extract Barcelona buildings from BigQuery OSM"""
    client = bigquery.Client()
    
    query = """
    SELECT 
      way_id as id,
      geometry as wkt_geometry,
      (SELECT value FROM UNNEST(all_tags) WHERE key = 'building') as building_type,
      (SELECT value FROM UNNEST(all_tags) WHERE key = 'height') as height,
      (SELECT value FROM UNNEST(all_tags) WHERE key = 'name') as name
    FROM `bigquery-public-data.geo_openstreetmap.planet_features`
    WHERE 
      feature_type = 'multipolygons'
      AND EXISTS(SELECT 1 FROM UNNEST(all_tags) WHERE key = 'building')
      AND ST_DWITHIN(
        ST_GEOGFROMTEXT(geometry),
        ST_GEOGPOINT(2.1734, 41.3851),
        5000
      )
    """
    
    print("Querying BigQuery for OSM buildings...")
    df = client.query(query).to_dataframe()
    print(f"Retrieved {len(df)} OSM buildings")
    
    # Convert WKT to geometry
    df['geometry'] = df['wkt_geometry'].apply(wkt.loads)
    gdf = gpd.GeoDataFrame(df, geometry='geometry', crs='EPSG:4326')
    
    return gdf

def extract_osm_amenities():
    """Extract Barcelona amenities from BigQuery OSM"""
    client = bigquery.Client()
    
    query = """
    SELECT 
      id,
      ST_ASTEXT(ST_GEOGPOINT(longitude, latitude)) as wkt_geometry,
      (SELECT value FROM UNNEST(tags) WHERE key = 'amenity') as amenity_type,
      (SELECT value FROM UNNEST(tags) WHERE key = 'name') as name
    FROM `bigquery-public-data.geo_openstreetmap.planet_nodes`
    WHERE 
      EXISTS(SELECT 1 FROM UNNEST(tags) WHERE key = 'amenity')
      AND latitude BETWEEN 41.30 AND 41.47
      AND longitude BETWEEN 2.05 AND 2.25
    """
    
    print("Querying BigQuery for OSM amenities...")
    df = client.query(query).to_dataframe()
    print(f"Retrieved {len(df)} OSM amenities")
    
    df['geometry'] = df['wkt_geometry'].apply(wkt.loads)
    gdf = gpd.GeoDataFrame(df, geometry='geometry', crs='EPSG:4326')
    
    return gdf

# ============================================
# Step 2: Extract Overture data from S3
# ============================================

def extract_overture_buildings():
    """Extract Barcelona buildings from Overture (via DuckDB + S3)"""
    con = duckdb.connect(':memory:')
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute("INSTALL httpfs; LOAD httpfs;")
    
    # Note: Update the release date to the latest Overture release
    query = """
    SELECT 
      id,
      geometry,
      height,
      class,
      names.primary as name,
      confidence
    FROM read_parquet(
      's3://overturemaps-us-west-2/release/2024-01-17-alpha.0/theme=buildings/type=building/*',
      hive_partitioning=1
    )
    WHERE 
      bbox.minX > 2.05 AND bbox.maxX < 2.25
      AND bbox.minY > 41.30 AND bbox.maxY < 41.47
    """
    
    print("Querying Overture S3 for buildings...")
    df = con.execute(query).fetchdf()
    print(f"Retrieved {len(df)} Overture buildings")
    
    # Convert WKT to geometry
    df['geometry'] = df['geometry'].apply(wkt.loads)
    gdf = gpd.GeoDataFrame(df, geometry='geometry', crs='EPSG:4326')
    
    return gdf

def extract_overture_places():
    """Extract Barcelona places from Overture"""
    con = duckdb.connect(':memory:')
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute("INSTALL httpfs; LOAD httpfs;")
    
    query = """
    SELECT 
      id,
      geometry,
      names.primary as name,
      categories.main as category,
      categories.alternate as subcategories,
      confidence
    FROM read_parquet(
      's3://overturemaps-us-west-2/release/2024-01-17-alpha.0/theme=places/type=place/*',
      hive_partitioning=1
    )
    WHERE 
      bbox.minX > 2.05 AND bbox.maxX < 2.25
      AND bbox.minY > 41.30 AND bbox.maxY < 41.47
    """
    
    print("Querying Overture S3 for places...")
    df = con.execute(query).fetchdf()
    print(f"Retrieved {len(df)} Overture places")
    
    df['geometry'] = df['geometry'].apply(wkt.loads)
    gdf = gpd.GeoDataFrame(df, geometry='geometry', crs='EPSG:4326')
    
    return gdf

# ============================================
# Step 3: Merge and save
# ============================================

def main():
    print("=" * 60)
    print("Extracting OSM + Overture data for Barcelona ABM")
    print("=" * 60)
    
    # Extract OSM data (via BigQuery)
    osm_buildings = extract_osm_buildings()
    osm_amenities = extract_osm_amenities()
    
    # Extract Overture data (via DuckDB + S3)
    overture_buildings = extract_overture_buildings()
    overture_places = extract_overture_places()
    
    # Save to files
    print("\nSaving to GeoJSON files...")
    osm_buildings.to_file('osm_buildings.geojson', driver='GeoJSON')
    osm_amenities.to_file('osm_amenities.geojson', driver='GeoJSON')
    overture_buildings.to_file('overture_buildings.geojson', driver='GeoJSON')
    overture_places.to_file('overture_places.geojson', driver='GeoJSON')
    
    # Load into DuckDB for ABM
    print("\nLoading into local DuckDB database...")
    con = duckdb.connect('barcelona_abm.duckdb')
    con.execute("INSTALL spatial; LOAD spatial;")
    
    # Create tables
    con.execute("""
        CREATE TABLE osm_buildings AS 
        SELECT * FROM ST_Read('osm_buildings.geojson')
    """)
    con.execute("""
        CREATE TABLE overture_buildings AS 
        SELECT * FROM ST_Read('overture_buildings.geojson')
    """)
    con.execute("""
        CREATE TABLE osm_amenities AS 
        SELECT * FROM ST_Read('osm_amenities.geojson')
    """)
    con.execute("""
        CREATE TABLE overture_places AS 
        SELECT * FROM ST_Read('overture_places.geojson')
    """)
    
    print("\n✓ Complete! Data saved to barcelona_abm.duckdb")
    print("\nSummary:")
    print(f"  OSM Buildings:      {len(osm_buildings):,}")
    print(f"  OSM Amenities:      {len(osm_amenities):,}")
    print(f"  Overture Buildings: {len(overture_buildings):,}")
    print(f"  Overture Places:    {len(overture_places):,}")

if __name__ == "__main__":
    main()
```

---

## Quick Start Commands

### 1. Install Required Packages
```bash
pip install google-cloud-bigquery duckdb geopandas shapely
```

### 2. Authenticate with Google Cloud
```bash
gcloud auth application-default login
```

### 3. Run Extraction Script
```bash
python extract_osm_overture.py
```

### 4. Verify Data in DuckDB
```python
import duckdb

con = duckdb.connect('barcelona_abm.duckdb')
con.execute("LOAD spatial;")

# Count records
print(con.execute("SELECT COUNT(*) FROM osm_buildings").fetchone())
print(con.execute("SELECT COUNT(*) FROM overture_places").fetchone())
```

---

## Troubleshooting

### "Overture Maps not found in BigQuery"
**Solution:** Overture is not a public dataset yet. Use DuckDB to query S3 directly (see Method 2, Option B).

### "Access Denied" in BigQuery
**Solution:** 
1. Ensure you have a GCP project created
2. Enable BigQuery API: https://console.cloud.google.com/apis/library/bigquery.googleapis.com
3. Check billing is enabled (free tier: 1TB queries/month)

### "S3 Access Denied" when querying Overture
**Solution:** Overture data is public. No AWS credentials needed. Ensure you're using `httpfs` extension in DuckDB.

### "Query too expensive" in BigQuery
**Solution:** Add `LIMIT` clauses and spatial filters (`ST_DWITHIN`) to reduce data scanned.

---

## Cost Summary

| Method | Storage Cost | Query Cost | Total (Barcelona) |
|--------|-------------|------------|-------------------|
| **BigQuery OSM only** | $0 (public data) | $2-5 | $2-5 |
| **DuckDB Overture only** | $0 | $0 | $0 (FREE) |
| **Hybrid (Recommended)** | $0 | $2-5 | $2-5 |
| **Import Overture to BigQuery** | $0.02/GB/month | $5/TB | ~$10-20 |

**Recommendation:** Use hybrid approach (BigQuery for OSM, DuckDB for Overture) to minimize costs.

---

## Next Steps

1. ✅ Run the Python extraction script
2. ✅ Verify data in DuckDB
3. ✅ Integrate with Mesa ABM (update `model.py` to query DuckDB)
4. ✅ Test agent spatial perception with hybrid dataset

**Need help?** Check these resources:
- BigQuery Geospatial Docs: https://cloud.google.com/bigquery/docs/geospatial-data
- Overture Docs: https://docs.overturemaps.org/
- DuckDB Spatial: https://duckdb.org/docs/extensions/spatial

---

**Document Version:** 1.0  
**Last Updated:** 2026-01-30
