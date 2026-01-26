# LLM-based UrbanABM - Integrated Services

This project provides integrated services for OpenStreetMap (OSM) data management and agent-based modeling with live map visualization.

## Architecture

The system consists of two main services:

1. **OSM Update Service** (`Environment/OSM/osm_update_service.py`) - Port 8001
   - FastAPI service for live OSM data updates
   - Extracts data from OpenStreetMap
   - Stores data in DuckDB with spatial extension
   - Provides REST API for data updates

2. **Map Server** (`AGENT/OSM/map_server.py`) - Port 8000
   - FastAPI service with Mesa-Geo agents
   - Interactive web map visualization
   - Real-time agent simulation
   - Queries DuckDB for spatial data

Both services share the same DuckDB database through a centralized configuration system.

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Start Both Services

**Option A: Using the startup script (Linux/Mac)**
```bash
./start_services.sh
```

**Option B: Manually start each service**

Terminal 1 - OSM Update Service:
```bash
cd Environment/OSM
python osm_update_service.py
```

Terminal 2 - Map Server:
```bash
cd AGENT/OSM
python map_server.py
```

### 3. Initialize OSM Data

Trigger an initial data update:
```bash
curl -X POST http://localhost:8001/update/async
```

Monitor the update progress:
```bash
curl http://localhost:8001/status
```

### 4. Access the Services

- **OSM Update Service**: http://localhost:8001
- **OSM Service API Docs**: http://localhost:8001/docs
- **Map Server**: http://localhost:8000
- **Map Server API Docs**: http://localhost:8000/docs

## Configuration

All paths and settings are managed through `config.py` in the project root. This provides a single source of truth for configuration across all services.

### Environment Variables

```bash
# Database path (optional, uses default if not set)
export DB_PATH=/path/to/custom.duckdb

# OSM Update Service configuration
export OSM_SERVICE_PORT=8001
export OSM_SERVICE_HOST=127.0.0.1

# Map Server configuration
export MAP_SERVER_PORT=8000
export MAP_SERVER_HOST=127.0.0.1
```

### Default Paths

- **Database**: `Environment/OSM/eixample_osm.duckdb`
- **OSM Service**: `http://127.0.0.1:8001`
- **Map Server**: `http://127.0.0.1:8000`

## Services Integration

### Workflow

1. **Update OSM Data** via OSM Update Service
   ```bash
   curl -X POST http://localhost:8001/update/async \
     -H "Content-Type: application/json" \
     -d '{"place_name": "Eixample, Barcelona, Spain"}'
   ```

2. **Data is saved** to shared DuckDB database

3. **Map Server automatically uses** the updated data for:
   - Building visualization
   - Road network display
   - Agent path-finding
   - Spatial queries

4. **Agents interact** with the spatial data:
   - Move along walk networks
   - Query nearby amenities
   - Display real-time information

### Data Flow

```
OpenStreetMap → OSM Update Service → DuckDB → Map Server → Web UI
                      ↓                         ↓
                 REST API               Mesa-Geo Agents
```

## Common Operations

### Update OSM Data

**Synchronous (waits for completion):**
```bash
curl -X POST http://localhost:8001/update \
  -H "Content-Type: application/json" \
  -d '{"place_name": "Eixample, Barcelona, Spain"}'
```

**Asynchronous (returns immediately):**
```bash
curl -X POST http://localhost:8001/update/async \
  -H "Content-Type: application/json" \
  -d '{"place_name": "Eixample, Barcelona, Spain"}'
```

### Check Update Status

```bash
curl http://localhost:8001/status
```

### View Database Statistics

```bash
curl http://localhost:8001/db/stats
```

### Step Agent Simulation

```bash
curl -X POST http://localhost:8000/api/step
```

### Get Agent Information

```bash
curl http://localhost:8000/api/agents
```

## Directory Structure

```
LLM-based-UrbanABM/
├── config.py                  # Centralized configuration
├── start_services.sh          # Start both services
├── stop_services.sh           # Stop both services
├── requirements.txt           # Python dependencies
│
├── Environment/
│   └── OSM/
│       ├── osm_update_service.py      # FastAPI OSM update service
│       ├── osm_to_duckdb.py           # Original extraction script
│       ├── eixample_osm.duckdb        # Shared database
│       ├── OSM_SERVICE_README.md      # OSM service documentation
│       └── README.md                  # OSM pipeline docs
│
└── AGENT/
    └── OSM/
        ├── map_server.py      # FastAPI map visualization server
        ├── model.py           # Mesa-Geo agent model
        ├── templates/         # HTML templates
        └── README.md          # Map server documentation
```

## Features

### OSM Update Service

✅ REST API for OSM data updates  
✅ Synchronous & asynchronous update modes  
✅ Live status monitoring  
✅ Configurable place names and paths  
✅ Database statistics endpoint  
✅ Health check endpoint  
✅ Background task processing  

### Map Server

✅ Interactive web map with Leaflet  
✅ Mesa-Geo agent-based modeling  
✅ Real-time agent visualization  
✅ Agent movement along road networks  
✅ Spatial queries (nearby amenities)  
✅ Multiple data layers (buildings, roads, amenities)  
✅ Agent simulation controls  

## Scheduling Automatic Updates

Keep OSM data fresh with automatic updates.

### Using cron (Linux/Mac)

```bash
# Add to crontab (crontab -e)
# Update every day at 3 AM
0 3 * * * curl -X POST http://localhost:8001/update/async
```

### Using Python

```python
import requests
import schedule
import time

def update_osm_data():
    response = requests.post("http://localhost:8001/update/async")
    print(f"Update triggered: {response.json()}")

# Run every day at 3 AM
schedule.every().day.at("03:00").do(update_osm_data)

while True:
    schedule.run_pending()
    time.sleep(60)
```

## Troubleshooting

### Ports Already in Use

If you get "Address already in use" errors:

```bash
# Check what's using the ports
lsof -i :8001  # OSM service
lsof -i :8000  # Map server

# Kill the processes
kill <PID>
```

Or use different ports:
```bash
export OSM_SERVICE_PORT=9001
export MAP_SERVER_PORT=9000
```

### Database Not Found

1. Check the database path in `config.py`
2. Run an update to create the database:
   ```bash
   curl -X POST http://localhost:8001/update/async
   ```
3. Verify the file exists:
   ```bash
   ls -lh Environment/OSM/eixample_osm.duckdb
   ```

### Map Server Can't Load Data

1. Ensure OSM Update Service has completed successfully
2. Check the status:
   ```bash
   curl http://localhost:8001/status
   ```
3. Verify database stats:
   ```bash
   curl http://localhost:8001/db/stats
   ```
4. Restart the map server

### Network Errors During Update

- Check internet connectivity
- Verify the place name is valid for osmnx
- Try a smaller area first
- Check OpenStreetMap API status

## Stopping Services

**Option A: Using the stop script**
```bash
./stop_services.sh
```

**Option B: Manually**
```bash
# Find and kill the processes
ps aux | grep osm_update_service
ps aux | grep map_server
kill <PID>
```

## Development

### Running with Auto-Reload

Both services support auto-reload for development:
- Changes to Python files automatically reload the service
- No need to manually restart during development

### Adding New Endpoints

1. Add endpoint to the appropriate service file
2. Update the documentation
3. Test with the interactive API docs at `/docs`

### Modifying Configuration

1. Edit `config.py` for system-wide changes
2. Use environment variables for runtime overrides
3. Both services automatically pick up configuration changes

## Testing

### Test OSM Update Service

```bash
# Health check
curl http://localhost:8001/health

# Trigger update
curl -X POST http://localhost:8001/update/async

# Check status
curl http://localhost:8001/status

# View stats
curl http://localhost:8001/db/stats
```

### Test Map Server

```bash
# Open in browser
open http://localhost:8000

# Check agents
curl http://localhost:8000/api/agents

# Step simulation
curl -X POST http://localhost:8000/api/step

# View tables
curl http://localhost:8000/api/tables
```

## API Documentation

Both services provide interactive API documentation:

- **OSM Service**: http://localhost:8001/docs
- **Map Server**: http://localhost:8000/docs

These provide:
- Complete endpoint reference
- Request/response schemas
- Interactive testing interface
- Example requests

## License

Part of the LLM-based UrbanABM project.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test both services
5. Submit a pull request

## Support

For issues or questions:
1. Check the troubleshooting section
2. Review the API documentation
3. Check service logs
4. Open an issue on GitHub
