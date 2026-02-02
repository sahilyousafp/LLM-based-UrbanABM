# Agent Movement and Decision Tracking

This folder contains the DuckDB database that tracks all agent movements and decisions during simulation runs.

## Database File

**File**: `agent_tracking.duckdb`

This database is automatically created when the simulation starts and continuously updated as agents move through the city.

## Data Structure

The database contains two main tables with spatial indexing for efficient analysis:

### Table 1: agent_movements

Tracks every position update of every agent.

| Column | Type | Description |
|--------|------|-------------|
| movement_id | INTEGER | Unique identifier for each movement record |
| agent_id | INTEGER | Agent's unique identifier |
| timestamp | TIMESTAMP | When the movement occurred |
| step_number | INTEGER | Simulation step number |
| longitude | DOUBLE | Longitude coordinate (WGS84) |
| latitude | DOUBLE | Latitude coordinate (WGS84) |
| geometry | GEOMETRY | Spatial point for spatial queries |
| edge_id | INTEGER | Current edge the agent is on |
| position_along_edge | DOUBLE | Position along edge (0.0 to 1.0) |
| speed | DOUBLE | Agent's movement speed |
| nearby_amenities_count | INTEGER | Number of amenities detected nearby |

**Indexes:**
- `idx_movements_agent_id` - Fast queries by agent
- `idx_movements_timestamp` - Fast time-based queries
- `idx_movements_step` - Fast step-based queries
- Spatial index on `geometry` column (automatic via DuckDB spatial extension)

### Table 2: agent_decisions

Tracks decision points when agents choose new edges or change direction.

| Column | Type | Description |
|--------|------|-------------|
| decision_id | INTEGER | Unique identifier for each decision |
| agent_id | INTEGER | Agent's unique identifier |
| timestamp | TIMESTAMP | When the decision was made |
| step_number | INTEGER | Simulation step number |
| decision_type | VARCHAR | Type of decision (e.g., 'edge_change') |
| from_edge_id | INTEGER | Edge the agent was on |
| to_edge_id | INTEGER | Edge the agent moved to |
| longitude | DOUBLE | Longitude where decision was made |
| latitude | DOUBLE | Latitude where decision was made |
| geometry | GEOMETRY | Spatial point for spatial queries |
| alternatives_count | INTEGER | Number of alternative choices available |
| decision_reason | VARCHAR | Reason for decision (e.g., 'prefer_unvisited', 'random_choice') |

**Indexes:**
- `idx_decisions_agent_id` - Fast queries by agent
- `idx_decisions_timestamp` - Fast time-based queries
- Spatial index on `geometry` column (automatic)

## Analysis Examples

### 1. Query Movement Data

```sql
-- Get all movements for a specific agent
SELECT * FROM agent_movements 
WHERE agent_id = 1 
ORDER BY timestamp;

-- Get movement density by area
SELECT 
    COUNT(*) as visit_count,
    AVG(longitude) as center_lon,
    AVG(latitude) as center_lat
FROM agent_movements
GROUP BY 
    CAST(longitude * 1000 AS INTEGER),
    CAST(latitude * 1000 AS INTEGER)
ORDER BY visit_count DESC;
```

### 2. Spatial Queries

```sql
-- Find all movements within a bounding box
SELECT * FROM agent_movements
WHERE ST_Within(
    geometry,
    ST_MakeEnvelope(min_lon, min_lat, max_lon, max_lat)
);

-- Find movements near a specific point (within 50m)
SELECT * FROM agent_movements
WHERE ST_DWithin(
    geometry,
    ST_Point(target_lon, target_lat),
    0.0005  -- approximately 50m in degrees
);
```

### 3. Heat Map Data

```sql
-- Generate heat map data (count visits per grid cell)
SELECT 
    FLOOR(longitude * 1000) / 1000 AS grid_lon,
    FLOOR(latitude * 1000) / 1000 AS grid_lat,
    COUNT(*) AS intensity,
    AVG(longitude) AS center_lon,
    AVG(latitude) AS center_lat
FROM agent_movements
GROUP BY grid_lon, grid_lat
HAVING intensity > 5
ORDER BY intensity DESC;
```

### 4. Agent Path Tracking

```sql
-- Get an agent's complete path
SELECT 
    step_number,
    longitude,
    latitude,
    edge_id,
    timestamp
FROM agent_movements
WHERE agent_id = 1
ORDER BY step_number;

-- Export path as GeoJSON LineString (for visualization)
SELECT ST_AsGeoJSON(
    ST_MakeLine(
        ARRAY_AGG(geometry ORDER BY step_number)
    )
) as path_geojson
FROM agent_movements
WHERE agent_id = 1;
```

### 5. Decision Analysis

```sql
-- Analyze decision patterns
SELECT 
    decision_reason,
    COUNT(*) as count,
    AVG(alternatives_count) as avg_alternatives
FROM agent_decisions
GROUP BY decision_reason
ORDER BY count DESC;

-- Find decision hotspots
SELECT 
    FLOOR(longitude * 1000) / 1000 AS grid_lon,
    FLOOR(latitude * 1000) / 1000 AS grid_lat,
    COUNT(*) AS decision_count
FROM agent_decisions
GROUP BY grid_lon, grid_lat
ORDER BY decision_count DESC
LIMIT 20;
```

### 6. Time-based Analysis

```sql
-- Movements per simulation step
SELECT 
    step_number,
    COUNT(*) as movement_count,
    COUNT(DISTINCT agent_id) as active_agents
FROM agent_movements
GROUP BY step_number
ORDER BY step_number;

-- Average speed over time
SELECT 
    step_number,
    AVG(speed) as avg_speed,
    MIN(speed) as min_speed,
    MAX(speed) as max_speed
FROM agent_movements
GROUP BY step_number
ORDER BY step_number;
```

## Visualization Examples

### Using Python (with geopandas)

```python
import duckdb
import geopandas as gpd
import matplotlib.pyplot as plt

# Connect to database
con = duckdb.connect('tracking_data/agent_tracking.duckdb')

# Load movements as GeoDataFrame
query = """
    SELECT agent_id, longitude, latitude, step_number,
           ST_AsText(geometry) as wkt
    FROM agent_movements
    WHERE step_number BETWEEN 0 AND 100
"""
df = con.execute(query).df()
gdf = gpd.GeoDataFrame(df, geometry=gpd.GeoSeries.from_wkt(df['wkt']))

# Plot heatmap
gdf.plot(column='agent_id', cmap='hot', alpha=0.5, markersize=0.5)
plt.title('Agent Movement Heatmap')
plt.show()
```

### Using Python (for path visualization)

```python
import duckdb
import folium

# Connect to database
con = duckdb.connect('tracking_data/agent_tracking.duckdb')

# Get path for specific agent
query = """
    SELECT longitude, latitude, step_number
    FROM agent_movements
    WHERE agent_id = 1
    ORDER BY step_number
"""
path = con.execute(query).fetchall()

# Create map with path
m = folium.Map(location=[path[0][1], path[0][0]], zoom_start=15)
folium.PolyLine(
    locations=[(lat, lon) for lon, lat, _ in path],
    color='red',
    weight=2,
    opacity=0.8
).add_to(m)

m.save('agent_path.html')
```

## Data Management

### Clearing Old Data

```sql
-- Clear all movement data
DELETE FROM agent_movements;

-- Clear all decision data
DELETE FROM agent_decisions;

-- Clear data for specific simulation run
DELETE FROM agent_movements WHERE step_number < 1000;
```

### Database Statistics

```sql
-- Get table sizes
SELECT 
    'agent_movements' as table_name,
    COUNT(*) as row_count
FROM agent_movements
UNION ALL
SELECT 
    'agent_decisions' as table_name,
    COUNT(*) as row_count
FROM agent_decisions;

-- Get date range of tracked data
SELECT 
    MIN(timestamp) as earliest_record,
    MAX(timestamp) as latest_record,
    MAX(step_number) as max_steps
FROM agent_movements;
```

## Notes

- The database file will grow over time as more data is collected
- Data is flushed to disk every 10 simulation steps for performance
- Spatial indexes are automatically maintained by DuckDB's spatial extension
- All coordinates are in WGS84 (EPSG:4326) coordinate system
- For large datasets, consider using spatial aggregation or sampling for visualization
- The data structure is optimized for both spatial and temporal queries

## Integration

The tracking system is automatically initialized when you create a `CityModel` instance:

```python
from model import CityModel

# Create model (tracking starts automatically)
model = CityModel(num_agents=500)

# Run simulation
for _ in range(100):
    model.step()

# Data is automatically saved to tracking_data/agent_tracking.duckdb
```

The tracker is integrated into both:
- `Backend/Agent/model.py` (Overture Maps data)
- `Backend/Agent/OSM_model.py` (OpenStreetMap data)
