# Urban ABM Environment - Spatial Database

## 🗺️ Overview

This module manages the spatial database for the Urban Agent-Based Model using **DuckDB** with **spatial extensions**. It supports both **OpenStreetMap (OSM)** and **Overture Maps Foundation** data sources.

### 🆕 Overture Maps Integration

The system now supports **Overture Maps** - a high-quality, structured geospatial dataset provided by major tech companies (Meta, Microsoft, Amazon, TomTom).

**Current Implementation**: Overture-compatible architecture using OSM data  
**Migration Path**: Easy switch to Overture when access is configured

📖 **See [OVERTURE_GUIDE.md](OVERTURE_GUIDE.md) for complete Overture Maps documentation**

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
*   DuckDB
*   Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```

### Running the Extraction
Run the script to fetch data and create the database `Eixample_OSM.duckdb`:
```bash
python osm_to_duckdb.py
```

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
