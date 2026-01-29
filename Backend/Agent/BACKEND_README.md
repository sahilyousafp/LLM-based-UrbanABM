# Urban ABM Backend API

FastAPI server that provides agent-based modeling and spatial data from DuckDB.

## Setup

Install dependencies:
```bash
pip install fastapi uvicorn duckdb shapely mesa
```

## Running the Server

```bash
cd Backend\Agent
python map_server.py
```

The server will start on http://127.0.0.1:8000

## API Endpoints

### Data Endpoints (GET)
- `/` - API health check and endpoint list
- `/api/buildings` - Get all buildings as GeoJSON
- `/api/walk_network` - Get pedestrian network as GeoJSON
- `/api/roads` - Get road network as GeoJSON
- `/api/amenities` - Get amenities (POIs) as GeoJSON
- `/api/walk_nodes` - Get walk network nodes as GeoJSON
- `/api/agents` - Get all agents with their positions
- `/api/agent/{agent_id}` - Get specific agent details and what they see
- `/api/stats` - Get database statistics
- `/api/tables` - List all database tables
- `/api/test` - Test endpoint for agent verification

### Simulation Endpoints (POST)
- `/api/step` - Step simulation forward (returns step count)
- `/api/step_continuous` - Step simulation and return updated agent positions

## Features

- **CORS Enabled**: Frontend can fetch from any origin
- **Mesa Integration**: Agent-based modeling using Mesa framework
- **DuckDB Spatial**: Efficient spatial queries on OSM data
- **Real-time Simulation**: Agents move and perceive their environment

## Model

The `CityModel` (from model.py) initializes agents that:
- Are placed at random locations
- Query nearby amenities within their perception radius
- Can move and update their perception each step
