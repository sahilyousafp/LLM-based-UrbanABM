# Urban ABM Environment - Spatial Database

## 🗺️ Overview

This module manages the spatial database for the Urban Agent-Based Model using **DuckDB** with **spatial extensions**. It supports both **Overture Maps Foundation (via BigQuery)** and **OpenStreetMap (OSM)** data sources.

## 🚀 Quick Start - Pick Your Path

### Path A: Overture Maps via BigQuery (Recommended ⚡)

**Best for**: High-quality data, faster queries, production use

**3 Steps**:
```bash
# 1. Set up GCP project (see QUICKSTART.md)
gcloud config set project YOUR-PROJECT-ID
gcloud services enable bigquery.googleapis.com
gcloud auth application-default login

# 2. Run extraction
python overture_to_duckdb.py

# 3. Verify
duckdb eixample_overture.duckdb -c "SHOW TABLES;"
```

📖 **Documentation**:
- 🏃 **[QUICKSTART.md](./QUICKSTART.md)** ← Start here for 3-minute setup
- 📘 **[OVERTURE_BIGQUERY_GUIDE.md](./OVERTURE_BIGQUERY_GUIDE.md)** ← Complete guide
- 🔧 **[ERROR_FIX.md](./ERROR_FIX.md)** ← Fix permission errors

### Path B: OpenStreetMap (Simple ✅)

**Best for**: Quick testing, no GCP account needed

```bash
python osm_to_duckdb.py  # No authentication needed!
```

## 📊 Comparison

| Feature | Overture (BigQuery) | OpenStreetMap |
|---------|-------------------|---------------|
| Setup | Needs GCP project | No setup |
| Quality | Curated, high-quality | Community-sourced |
| Speed | Fast (server-side filter) | Moderate |
| Cost | Free (1TB/month) | Free |
| Authentication | Required | Not required |

## 🆘 Got an Error?

### "User does not have bigquery.jobs.create permission"
👉 See **[ERROR_FIX.md](./ERROR_FIX.md)** - This explains exactly how to fix it!

**TL;DR**: You need a GCP project with BigQuery enabled:
```bash
# Visit: https://console.cloud.google.com/projectcreate
# Then:
gcloud config set project YOUR-NEW-PROJECT-ID
gcloud services enable bigquery.googleapis.com
gcloud auth application-default login
```

---

## 🆕 Overture Maps with BigQuery

The system uses **Overture Maps via Google Cloud BigQuery** - high-quality, curated geospatial data.

**Benefits**:
- ⚡ 2-3x faster queries (server-side filtering)
- 💰 Free tier: 1TB queries/month (~5,800 Barcelona runs)
- 🎯 SQL interface for data access
- 📊 Reduced data transfer (170MB vs 500MB)

📖 **Complete Guide**: [OVERTURE_BIGQUERY_GUIDE.md](./OVERTURE_BIGQUERY_GUIDE.md)

## Pipeline Overview

1.  **Extraction**: Python script (`osm_to_duckdb.py`) uses `osmnx` to download data:
    *   **Buildings**: Footprints with types.
    *   **Amenities**: POIs like cafes, shops, offices.
    *   **Networks**: Walkable and Drivable street networks (nodes and edges).
2.  **Processing**: GeoPandas is used to clean and format the data. Geometries are converted to WKT (Well-Known Text) for compatibility.
3.  **Loading**: DuckDB ingests the data, converts WKT to native Geometry types using the `spatial` extension, and stores them in tables.

## Quick Start

### Prerequisites
*   Python 3.9+
*   Google Cloud SDK (for Overture Maps)
*   Install dependencies:
    ```bash
    pip install -r ../../requirements.txt
    ```

### Option 1: Overture Maps (BigQuery) - Recommended ⚡

**📖 See complete guide**: [OVERTURE_BIGQUERY_GUIDE.md](./OVERTURE_BIGQUERY_GUIDE.md)

```bash
# 1. Set up GCP project (one-time)
gcloud config set project YOUR-PROJECT-ID
gcloud services enable bigquery.googleapis.com

# 2. Authenticate (one-time)
gcloud auth application-default login

# 3. Run extraction
python overture_to_duckdb.py
```

### Option 2: OpenStreetMap (Simple) ✅

```bash
# No authentication needed
python osm_to_duckdb.py
```

### Running the Extraction
Both scripts create a database: `eixample_overture.duckdb`

### Database Schema

#### `buildings`
*   `geometry`: GEOMETRY (Polygon)
*   `building`: Type (e.g., apartments, retail)
*   `height`, `levels`: Physical attributes (if available)
*   ...other OSM tags

#### `amenities`
*   `geometry`: GEOMETRY (Point or Polygon)
*   `amenity`: Type (e.g., cafe, pharmacy)
*   `name`: Name of the POI
*   ...other OSM tags

#### `walk_nodes`, `drive_nodes`
*   `geometry`: GEOMETRY (Point)
*   `osmid`: Unique Node ID

#### `walk_edges`, `drive_edges`
*   `geometry`: GEOMETRY (LineString)
*   `u`, `v`: Start/End Node IDs
*   `length`: Length in meters
