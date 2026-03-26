# Agent Behavior Recording Guide

## Overview

The Urban ABM system includes a **Recording** feature that captures all agent behaviors to a **GeoParquet** file for spatial machine learning and analytics. This includes:

- **Spatial coordinates** (longitude, latitude) at each step
- **Agent decisions** and LLM reasoning
- **Needs state** (hunger, energy, social)
- **Cognition state** (mood, curiosity, fatigue)
- **Visited amenities** and perception points
- **Thought streams** (mobility, cognition, needs events)
- **Street perception data** (walkability, vegetation, architectural style, etc.)

---

## Quick Start

### 1. Start the System

```bash
# Start the backend server
cd Backend\Agent
python map_server.py

# Open Frontend\index.html in your browser
```

### 2. Start Recording

1. Click the **red record button** (●) next to the Run button
2. The recording panel will appear showing:
   - Session name
   - Steps recorded
   - Total records
   - Agents tracked
3. Configure options:
   - **Thoughts**: Include agent thought streams (mobility decisions, cognition updates)
   - **Perception**: Include street view perception data

### 3. Run Simulation

- Click **Run** to start continuous simulation
- Click **Step** to advance one step at a time
- The recording panel updates in real-time

### 4. Stop Recording

- Click the **red record button** again to stop
- A download link will appear
- Click **⬇ Download GeoParquet** to save the file

---

## GeoParquet Schema

The recorded data is saved as a GeoParquet file with the following schema:

### Spatial Columns

| Column | Type | Description |
|--------|------|-------------|
| `geometry` | GeoPoint | Point geometry (EPSG:4326) |
| `longitude` | float64 | Longitude coordinate |
| `latitude` | float64 | Latitude coordinate |

### Agent Identity

| Column | Type | Description |
|--------|------|-------------|
| `agent_id` | int64 | Unique agent identifier |
| `step` | int64 | Simulation step number |
| `timestamp` | string | ISO 8601 timestamp |
| `archetype` | string | Agent type: resident, commuter, tourist, student |
| `age` | int64 | Agent age (18-70) |

### Network Position

| Column | Type | Description |
|--------|------|-------------|
| `edge_id` | int64 | Current edge ID (-1 if none) |
| `position_along_edge` | float64 | Position along edge (0.0-1.0) |

### Internal State (JSON)

| Column | Type | Description |
|--------|------|-------------|
| `needs_json` | string | `{"hunger": 0.5, "energy": 0.8, "social": 0.3}` |
| `cognition_state_json` | string | `{"mood": "happy", "curiosity": 0.7, "fatigue": 0.2}` |
| `current_plan_json` | string | `{"goal": "explore", "target_edge_id": 123}` |
| `visited_edges_json` | string | `{"123": 3, "456": 1}` (edge_id: visit_count) |
| `visited_amenities_json` | string | Last 20 amenity visits with timestamps |
| `nearby_amenities_json` | string | Nearest 10 amenities at current position |

### Perception & Decisions

| Column | Type | Description |
|--------|------|-------------|
| `street_perception_json` | string | Street view analysis (if enabled) |
| `thought_stream_json` | string | Recent mobility/cognition/needs events |
| `decision_reason` | string | LLM reasoning for last movement decision |
| `is_fallback` | bool | True if rule-based fallback was used |

---

## Loading and Analyzing Data

### Python (GeoPandas)

```python
import geopandas as gpd
import pandas as pd
import json

# Load GeoParquet file
gdf = gpd.read_parquet("Documentation/agent_recording_session_20260326_143000.parquet")

# Basic statistics
print(f"Total records: {len(gdf)}")
print(f"Unique agents: {gdf['agent_id'].nunique()}")
print(f"Steps: {gdf['step'].min()} to {gdf['step'].max()}")

# Parse JSON columns
gdf['needs'] = gdf['needs_json'].apply(json.loads)
gdf['cognition'] = gdf['cognition_state_json'].apply(json.loads)

# Extract needs into separate columns
gdf['hunger'] = gdf['needs'].apply(lambda x: x.get('hunger', 0))
gdf['energy'] = gdf['needs'].apply(lambda x: x.get('energy', 0))
gdf['social'] = gdf['needs'].apply(lambda x: x.get('social', 0))

# Filter by archetype
residents = gdf[gdf['archetype'] == 'resident']
tourists = gdf[gdf['archetype'] == 'tourist']

# Spatial analysis: create trajectory for each agent
trajectories = gdf.dissolve(by='agent_id', as_index=False)

# Visualize agent density
from shapely.geometry import Point
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(12, 8))
gdf.plot(ax=ax, markersize=2, alpha=0.3, column='archetype', categorical=True)
plt.title("Agent Spatial Distribution")
plt.show()
```

### DuckDB

```sql
-- Load and query GeoParquet
SELECT 
    archetype,
    COUNT(*) as record_count,
    COUNT(DISTINCT agent_id) as unique_agents,
    AVG(needs_json:hunger::DOUBLE) as avg_hunger
FROM read_parquet('Documentation/agent_recording_*.parquet')
GROUP BY archetype;

-- Spatial query: agents within 500m of a point
SELECT 
    agent_id,
    step,
    archetype,
    decision_reason
FROM read_parquet('Documentation/agent_recording_*.parquet')
WHERE ST_DWithin(
    geometry,
    ST_Point(2.1734, 41.3851),  -- Barcelona center
    0.005  -- ~500m in degrees
)
ORDER BY step;

-- Extract and analyze thought streams
SELECT 
    agent_id,
    step,
    json_extract_string(thought_stream_json, '$[0].topic') as topic,
    json_extract_string(thought_stream_json, '$[0].description') as thought
FROM read_parquet('Documentation/agent_recording_*.parquet')
WHERE thought_stream_json IS NOT NULL
LIMIT 100;
```

### QGIS

1. Open QGIS
2. Go to **Layer** → **Add Layer** → **Add Vector Layer**
3. Select the GeoParquet file
4. The layer will load with all attributes
5. Use **Processing Toolbox** for spatial analysis:
   - Heatmaps
   - Trajectory analysis
   - Spatial joins with buildings/amenities

---

## Spatial ML Use Cases

### 1. Trajectory Clustering

```python
from sklearn.cluster import DBSCAN
import numpy as np

# Extract trajectories
agent_trajectories = {}
for agent_id in gdf['agent_id'].unique():
    agent_data = gdf[gdf['agent_id'] == agent_id].sort_values('step')
    coords = np.vstack([agent_data['longitude'], agent_data['latitude']]).T
    agent_trajectories[agent_id] = coords

# Cluster similar trajectories
# (Requires trajectory distance metric like Fréchet or DTW)
```

### 2. Hotspot Analysis

```python
from scipy.stats import gaussian_kde

# Create density estimate
coords = np.vstack([gdf['longitude'], gdf['latitude']])
kde = gaussian_kde(coords)

# Evaluate on grid
xx, yy = np.mgrid[2.16:2.18:100j, 41.38:41.40:100j]
positions = np.vstack([xx.ravel(), yy.ravel()])
density = kde(positions).reshape(xx.shape)

plt.contourf(xx, yy, density, levels=20, cmap='hot')
plt.title("Agent Activity Hotspots")
```

### 3. Decision Prediction

```python
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

# Prepare features
features = ['hunger', 'energy', 'social', 'curiosity', 'fatigue']
X = np.column_stack([
    gdf['hunger'],
    gdf['energy'],
    gdf['social'],
    gdf['cognition'].apply(lambda x: x.get('curiosity', 0)),
    gdf['cognition'].apply(lambda x: x.get('fatigue', 0)),
])

# Target: whether agent used LLM or fallback
y = (gdf['is_fallback'] == False).astype(int)

# Train classifier
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2)
clf = RandomForestClassifier(n_estimators=100)
clf.fit(X_train, y_train)

# Feature importance
importances = clf.feature_importances_
for feat, imp in zip(features, importances):
    print(f"{feat}: {imp:.3f}")
```

### 4. Archetype Classification

```python
# Predict agent archetype from movement patterns
from sklearn.multioutput import MultiOutputClassifier

# Features: movement statistics per agent
agent_stats = gdf.groupby('agent_id').agg({
    'longitude': ['mean', 'std'],
    'latitude': ['mean', 'std'],
    'edge_id': 'count',
    'step': 'max',
})

# Target: archetype (one-hot encoded)
archetypes = pd.get_dummies(gdf.groupby('agent_id')['archetype'].first())

# Train classifier
clf = MultiOutputClassifier(RandomForestClassifier())
clf.fit(agent_stats, archetypes)
```

### 5. Spatial Regression

```python
import statsmodels.api as sm

# Predict agent count per location
grid = gdf.copy()
grid['count'] = 1

# Aggregate to grid cells
grid['lon_bin'] = (grid['longitude'] * 1000).astype(int)
grid['lat_bin'] = (grid['latitude'] * 1000).astype(int)
aggregated = grid.groupby(['lon_bin', 'lat_bin']).agg({
    'count': 'sum',
    'hunger': 'mean',
    'energy': 'mean',
}).reset_index()

# Spatial regression
X = aggregated[['hunger', 'energy']]
X = sm.add_constant(X)
y = aggregated['count']

model = sm.OLS(y, X).fit()
print(model.summary())
```

---

## Performance Considerations

### File Size Estimates

| Agents | Steps | With Thoughts | With Perception | Total Size |
|--------|-------|---------------|-----------------|------------|
| 15 | 100 | Yes | Yes | ~5 MB |
| 15 | 1000 | Yes | Yes | ~50 MB |
| 50 | 100 | Yes | Yes | ~15 MB |
| 50 | 1000 | Yes | Yes | ~150 MB |
| 500 | 100 | Yes | Yes | ~150 MB |
| 500 | 1000 | Yes | Yes | ~1.5 GB |

**Tips:**
- Disable **Perception** to reduce size by ~30%
- Disable **Thoughts** to reduce size by ~20%
- Record every Nth step for long simulations

### Memory Usage

The recorder buffers data in memory before flushing to disk:
- Default buffer size: 5,000 records
- Auto-flush when buffer is full
- Manual flush on stop

### Recording Overhead

- **Without recording**: ~100-500ms per step (depends on LLM calls)
- **With recording**: ~150-700ms per step (5-50% overhead)
- Overhead increases with more agents and data fields

---

## Troubleshooting

### Recording doesn't start

1. Check backend is running at `http://127.0.0.1:8000`
2. Verify `geopandas` and `pyarrow` are installed:
   ```bash
   pip install geopandas pyarrow
   ```
3. Check browser console for errors

### File export fails

1. Ensure `Documentation/` directory exists and is writable
2. Check disk space (GeoParquet files can be large)
3. Try reducing buffer size in API call

### Missing data fields

1. **No thoughts**: Enable "Thoughts" checkbox before starting
2. **No perception**: Enable "Perception" checkbox; ensure street view data exists
3. **Empty decision_reason**: Agent may have used rule-based fallback

### Performance issues

1. Reduce number of agents
2. Disable perception data (largest field)
3. Use step mode instead of continuous run
4. Increase buffer size to reduce disk I/O

---

## API Reference

### Start Recording

```bash
POST /api/recording/start?session_name=my_experiment&include_thoughts=true&include_perception=true
```

Response:
```json
{
  "status": "recording_started",
  "session_id": "my_experiment_123456",
  "session_name": "my_experiment",
  "include_thoughts": true,
  "include_perception": true,
  "output_dir": "D:\\IaaC\\2ND_YEAR\\THESIS\\LLM_Based_UrbanABM\\Documentation"
}
```

### Stop Recording

```bash
POST /api/recording/stop
```

Response:
```json
{
  "status": "recording_stopped",
  "file_path": "D:\\...\\Documentation\\agent_recording_my_experiment.parquet",
  "file_name": "agent_recording_my_experiment.parquet",
  "total_records": 1500,
  "agents_tracked": 15,
  "steps_recorded": 100,
  "records_written": 1500
}
```

### Get Status

```bash
GET /api/recording/status
```

Response:
```json
{
  "is_recording": true,
  "session_id": "my_experiment_123456",
  "session_name": "my_experiment",
  "start_time": "2026-03-26T14:30:00.123456",
  "start_step": 0,
  "total_records": 750,
  "agents_tracked": 15,
  "steps_recorded": 50,
  "buffer_size": 750,
  "output_path": "D:\\...\\Documentation\\agent_recording_my_experiment.parquet"
}
```

### Download File

```bash
GET /api/recording/download/agent_recording_my_experiment.parquet
```

Returns the GeoParquet file as a binary download.

---

## Best Practices

### For Spatial ML

1. **Record multiple sessions** with different random seeds
2. **Balance agent archetypes** for diverse data
3. **Run long enough** to capture meaningful patterns (100+ steps)
4. **Include perception data** for environment-behavior correlations
5. **Document session parameters** (LLM model, agent count, etc.)

### For Visualization

1. **Use QGIS** for interactive exploration
2. **Create heatmaps** to identify activity hotspots
3. **Animate trajectories** to show temporal patterns
4. **Join with building/amenity data** for context

### For Analysis

1. **Aggregate by agent** to avoid pseudoreplication
2. **Control for step number** in statistical models
3. **Use spatial autocorrelation** metrics (Moran's I)
4. **Validate against real pedestrian data** if available

---

## Citation

If you use this recording feature in your research, please cite:

```
[Add citation information when publishing]
```

## License

[Add license information]
