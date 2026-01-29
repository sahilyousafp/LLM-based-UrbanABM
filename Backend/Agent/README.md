# Mesa-Geo Agents on Interactive Map

Interactive web map showing Mesa-Geo agents on DuckDB spatial data. Click agents to see what they observe nearby.

## Features

✅ **20 Mesa-Geo agents** randomly placed on walk network nodes  
✅ **Click agents** to see what amenities they can see within 50m  
✅ **All spatial layers** - buildings, walk network, roads, amenities  
✅ **Interactive toggles** for each layer  
✅ **Real-time queries** - agents query DuckDB when clicked  

## Quick Start

### 1. Recreate the HTML template
```bash
cd "D:\IaaC\2ND YEAR\THESIS\CODE EXPLORATIONS\Agent\Term 02\MesaGeo_DuckDB"
python create_map_html.py
```

### 2. Start the server
```bash
python map_server.py
```

### 3. Open browser
```
http://127.0.0.1:8000
```

## How to Use

### Viewing Agents
- **Orange circles** = Mesa-Geo agents (8px radius)
- Agents are placed randomly on walk_nodes from DuckDB
- 20 agents by default

### Interacting with Agents
1. **Click any orange circle** (agent)
2. **Right panel shows:**
   - Agent ID
   - Agent type
   - Current location (lon, lat)
   - **"What I see"** - List of nearby amenities within 50m
   - Each amenity shows name, type, and distance

### Example Output:
```
Agent 5
Type: CityAgent
Location: 2.15432, 41.38765

What I see:
• Bar Tinto (bar)
  Distance: 23.4m
• Supermercat (supermarket)  
  Distance: 47.8m
• Parc Infantil (playground)
  Distance: 12.1m
```

## Map Layers

Toggle visibility with checkboxes:

- ☑ **Buildings** (gray polygons) - 8,206 buildings
- ☑ **Walk Network** (blue lines) - 24,112 pedestrian paths
- ☑ **Roads** (red lines) - 1,486 drive edges
- ☑ **Amenities** (green dots) - 10,891 points of interest
- ☑ **Agents** (orange circles) - 20 Mesa-Geo agents

## API Endpoints

- `GET /` - Interactive map interface
- `GET /api/agents` - Get all agents as GeoJSON
- `GET /api/agent/{id}` - Get agent details and nearby amenities
- `POST /api/step` - Step simulation forward
- `GET /api/buildings` - Buildings GeoJSON
- `GET /api/walk_network` - Walk network GeoJSON
- `GET /api/roads` - Roads GeoJSON
- `GET /api/amenities` - Amenities GeoJSON
- `GET /api/tables` - List database tables
- `GET /api/stats` - Database statistics

## Agent Behavior

### Current:
- Agents spawn at random walk_nodes
- Stationary (don't move yet)
- Query nearby amenities on click

### How It Works:
1. Agent clicked → sends request to `/api/agent/{id}`
2. Server gets agent location from Mesa model
3. Server queries DuckDB: `ST_DWithin(geometry, agent_point, 50m)`
4. Returns list of amenities within 50m
5. Browser displays results in side panel

## Technical Details

### Agent Placement:
```python
# In model.py
nodes_df = con.execute("SELECT ST_AsText(geometry) FROM walk_nodes LIMIT 100")
for agent in agents:
    random_node = nodes_df.sample(1)
    agent.geometry = Point(node.x, node.y)  # Already in WGS84
```

### Spatial Query:
```python
# When agent clicked
query = f"""
    SELECT name, amenity, ST_Distance(geometry, agent_point) as dist
    FROM amenities
    WHERE ST_DWithin(geometry, agent_point, 0.0005)  # ~50m in degrees
    ORDER BY dist
    LIMIT 5
"""
```

### Coordinate System:
- Database: **WGS84 (EPSG:4326)** - longitude, latitude
- No transformation needed
- Distance approximation: 0.0005° ≈ 50m at Barcelona latitude

## Customization

### Change number of agents:
Edit `map_server.py`:
```python
city_model = CityModel(num_agents=50)  # Default: 20
```

### Change detection radius:
Edit `model.py` in `get_nearby_amenities()`:
```python
buffer_deg = 0.001  # ~100m (default: 0.0005 = ~50m)
```

### Add more agent attributes:
Edit `model.py` CityAgent class and `to_dict()` method.

## Next Steps

### To implement:
- [ ] Agent movement along walk network
- [ ] Step simulation (agents move)
- [ ] Agent trails/paths visualization
- [ ] Agent-agent interactions
- [ ] Different agent types (pedestrian, cyclist)
- [ ] Activity patterns (going to shops, parks)
- [ ] Heatmaps of agent density
- [ ] Time-based animations

## Troubleshooting

**No agents visible:**
- Check console: "Agents received: X"
- Check server logs for errors
- Verify model.py initializes correctly

**Clicking agent does nothing:**
- Open browser console (F12) for errors
- Check server logs for `/api/agent/{id}` requests
- Verify agent_id exists

**No nearby amenities:**
- Agent might be in area with no amenities
- Try clicking different agents
- Check amenities layer is visible

## Files

- `map_server.py` - FastAPI server with Mesa model
- `model.py` - Mesa-Geo model with agents
- `create_map_html.py` - Generates map interface
- `templates/map.html` - Interactive map (auto-generated)

