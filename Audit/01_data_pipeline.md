# 01 — Data Pipeline

Everything in this simulation is grounded in real Barcelona geography. This document explains how raw map data becomes the DuckDB database that every agent queries at runtime.

---

## Two Data Sources

| Source | Script | Status | Use |
|--------|--------|--------|-----|
| **Overture Maps** (S3 / BigQuery) | `Backend/Environment/overture_to_duckdb.py` | **Primary** | Buildings, amenities, transport, walk network |
| **OpenStreetMap** (local via OSMnx) | `Backend/Environment/osm_to_duckdb.py` | Legacy / fallback | Original network + building import |

Benchmark 03 (`benchmark/03_map_data_comparison.ipynb`) found Overture Maps has better POI coverage and more consistent road classification for the Eixample area.

---

## Overture Maps Pipeline

### What Overture Maps Is
[Overture Maps Foundation](https://overturemaps.org/) releases quarterly snapshots of global map data (buildings, POIs, roads, administrative boundaries) stored on AWS S3 and Google BigQuery as Apache Parquet files.

**Current release:** `OVERTURE_RELEASE=2024-11-13.0` (configurable in `.env`)

### Download Path

```
AWS S3 (public, no auth)
  s3://overturemaps-us-west-2/release/{release}/theme=buildings/type=*
  s3://overturemaps-us-west-2/release/{release}/theme=places/type=*
  s3://overturemaps-us-west-2/release/{release}/theme=transportation/type=segment
        │
        │  DuckDB httpfs extension reads Parquet directly
        │  (no local download needed)
        ▼
  DuckDB spatial query (bbox filter for Eixample)
        │
        ▼
  Backend/Environment/eixample_overture.duckdb   ← 8.1 MB
```

**Fallback:** If S3 is slow, the script falls back to **Google BigQuery** (`bigquery-public-data.overture_maps.*`). This requires GCP credentials — see `GCP_BIGQUERY_ACCESS_GUIDE.md`.

### Key Script: `Backend/Environment/overture_to_duckdb.py`

```python
# Simplified flow:
con = duckdb.connect("eixample_overture.duckdb")
con.install_extension("httpfs")
con.install_extension("spatial")

# Eixample bounding box
BBOX = (2.145, 41.380, 2.185, 41.400)  # (west, south, east, north)

# Download buildings
con.execute("""
    CREATE TABLE buildings AS
    SELECT id, geometry, names.primary AS name,
           height, num_floors, building_type
    FROM read_parquet('s3://overturemaps-us-west-2/release/.../buildings/*')
    WHERE bbox.minx > 2.145 AND bbox.maxx < 2.185
      AND bbox.miny > 41.380 AND bbox.maxy < 41.400
""")
```

---

## DuckDB Database Schema

**File:** `Backend/Environment/eixample_overture.duckdb`

### `buildings`
| Column | Type | Description |
|--------|------|-------------|
| `id` | VARCHAR | Overture feature ID |
| `geometry` | GEOMETRY | WKB Polygon |
| `name` | VARCHAR | Building name (if any) |
| `height` | FLOAT | Estimated height (metres) |
| `num_floors` | INTEGER | Floor count |
| `building_type` | VARCHAR | residential / commercial / civic / etc. |
| `bbox_minx/miny/maxx/maxy` | FLOAT | Pre-computed bounding box for fast spatial filtering |

### `amenities`
| Column | Type | Description |
|--------|------|-------------|
| `id` | VARCHAR | Overture place ID |
| `geometry` | GEOMETRY | WKB Point |
| `name` | VARCHAR | Place name |
| `amenity` | VARCHAR | Type: restaurant / cafe / bar / pharmacy / park / library / gym / supermarket / … |
| `lon`, `lat` | FLOAT | Unpacked coordinates (fast lookup without ST_X/ST_Y) |
| `amenity_tags` | JSON | Additional tags from Overture |

**Queried by:** `CityModel._query_nearby_amenities()` and `PlanBlock` destination resolution.

### `walk_edges`
| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER | Edge ID (used as primary key throughout) |
| `geometry` | GEOMETRY | WKB LineString |
| `road_class` | VARCHAR | footway / pedestrian / path / residential / … |
| `name` | VARCHAR | Street name |
| `direction` | VARCHAR | forward / backward (both directions stored as separate rows) |

**This is the pedestrian movement graph.** Loaded entirely into memory at `CityModel.__init__()` as `self.edges` and `self.node_to_edges`.

### `walk_nodes`
Implicit from edge endpoints. Not a separate table — the node-to-edges lookup is built in Python from `walk_edges` geometries.

### `streetview_perception` ← lives in `perception.duckdb`, NOT in `eixample_overture.duckdb`

**File:** `Backend/Environment/perception.duckdb`  
This table is kept in a **dedicated separate DuckDB file** so that:
- The main spatial DB stays independent (no lock conflict on Windows when reimporting)
- VLM results can be updated at runtime via `POST /api/streetview/reimport-perception` without touching the main file
- `CityModel` holds two connections: `self.con` → main DB, `self.perception_con` → perception DB

| Column | Type | Description |
|--------|------|-------------|
| `latitude`, `longitude` | DOUBLE | Capture point coordinates |
| `geometry` | GEOMETRY | Point (for spatial joins) |
| `heading` | DOUBLE | Camera heading (degrees) |
| `scene_overview` | VARCHAR | 2–3 sentence scene description |
| `buildings` | VARCHAR | Building materials, style, condition |
| `vegetation_text` | VARCHAR | Trees, greenery presence |
| `pedestrian_activity` | DOUBLE | Crowd density 0 (empty) – 3 (crowded) |
| `lighting_atmosphere` | VARCHAR | Time-of-day lighting, shadows |
| `spatial_impression` | VARCHAR | Street width feel, enclosure ratio |
| `as_resident` | VARCHAR | How a resident would perceive this |
| `as_tourist` | VARCHAR | How a tourist would perceive this |
| `as_commuter` | VARCHAR | How a commuter would perceive this |
| `as_student` | VARCHAR | How a student would perceive this |
| `walkability` | DOUBLE | Derived 0–10 walkability score |
| `architectural_style` | VARCHAR | Dominant style from spatial_character |
| `building_condition` | VARCHAR | Dominant condition label |
| `lighting_json` | VARCHAR | JSON array: raw VLM lighting entries |
| `spatial_character_json` | VARCHAR | JSON array: raw VLM spatial character |
| `crowdedness_json` | VARCHAR | JSON array: raw VLM crowdedness entries |
| `greenery_json` | VARCHAR | JSON array: raw VLM greenery entries |

Full 39-column schema in `Backend/Environment/ingestion/perception.py` (`_CREATE_TABLE_SQL`).  
Populated at server startup from `output/results/*_analysis.json`. Can be refreshed without restart via `POST /api/streetview/reimport-perception`. See `06_spatial_reasoning.md`.

### Tracking Tables (runtime, separate file)
These live in `Documentation/tracking_data/agent_tracking.duckdb`:
- `agent_movements` — position + needs snapshot per step per agent
- `agent_decisions` — edge chosen + reasoning + is_fallback flag per step per agent

---

## Plugin System

**Location:** `Backend/Environment/plugins/`

The plugin system loads external data into additional DuckDB tables at model startup. Each plugin inherits from `base_plugin.py`:

```python
class BasePlugin:
    table_name: str       # DuckDB table to populate
    def fetch(self, bbox, con): ...   # download + insert
    def verify(self, con): ...        # check row count
```

| Plugin | Table | Data Source |
|--------|-------|-------------|
| `weather_plugin.py` | `ext_weather` | [Open-Meteo](https://open-meteo.com/) (free, no key) — daily Barcelona weather snapshot |
| `gtfs_plugin.py` | `ext_transit_stops` | [Transitland](https://www.transit.land/) — GTFS transit stops (auto-resolved by city bbox) |
| `template_plugin.py` | — | Boilerplate for custom data sources |

Weather modulates need decay rates (rain → faster comfort decay, heat → faster energy decay). Transit stops appear in agent prompts as nearby options.

---

## Inspecting the Database

Use `Backend/Environment/verify_db.py` for a quick health check:

```bash
cd Backend/Environment
python verify_db.py
# Outputs: table list, row counts, sample spatial query
```

For deeper inspection, see `DUCKDB_INSPECTION_GUIDE.md`:
```python
import duckdb
con = duckdb.connect("eixample_overture.duckdb", read_only=True)
con.load_extension("spatial")

# Count amenities by type
con.execute("SELECT amenity, COUNT(*) FROM amenities GROUP BY amenity ORDER BY 2 DESC").df()

# Find cafes near a point
con.execute("""
    SELECT name, lon, lat,
           ST_Distance(ST_Point(2.162, 41.386), geometry::GEOMETRY) * 111320 AS dist_m
    FROM amenities WHERE amenity = 'cafe'
    ORDER BY dist_m LIMIT 5
""").df()
```

---

## External References

| Resource | URL |
|----------|-----|
| Overture Maps S3 paths | https://docs.overturemaps.org/getting-data/amazon-s3/ |
| Overture Maps BigQuery | https://docs.overturemaps.org/getting-data/google-bigquery/ |
| DuckDB spatial extension | https://duckdb.org/docs/extensions/spatial.html |
| DuckDB httpfs extension | https://duckdb.org/docs/extensions/httpfs.html |
| Open-Meteo API | https://open-meteo.com/en/docs |
| Transitland (GTFS) | https://www.transit.land/ |
| OSMnx documentation | https://osmnx.readthedocs.io/ |
| GCP BigQuery public datasets | https://cloud.google.com/bigquery/public-data |

---

**Next:** [`02_simulation_core.md`](02_simulation_core.md) — how the Mesa model is structured and how agents step through the simulation.
