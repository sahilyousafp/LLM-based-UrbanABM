# OSM Data Update Service

A FastAPI service that provides live updates for OpenStreetMap (OSM) data stored in DuckDB. This service runs independently and can be used to keep OSM data fresh for the map server and agent-based models.

## Features

✅ **REST API** for triggering OSM data updates  
✅ **Synchronous & Asynchronous** update modes  
✅ **Live status monitoring** of update progress  
✅ **Configurable** place names and database paths  
✅ **Database statistics** endpoint  
✅ **Health check** endpoint  
✅ **Background task processing** for non-blocking updates  

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Start the Service

```bash
cd Environment/OSM
python osm_update_service.py
```

The service will start on `http://localhost:8001` by default.

### 3. Trigger an Update

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

### 4. Check Status

```bash
curl http://localhost:8001/status
```

## API Endpoints

### `GET /`
Service information and available endpoints.

**Response:**
```json
{
  "service": "OSM Data Update Service",
  "version": "1.0.0",
  "status": "idle",
  "endpoints": {
    "status": "/status",
    "update": "/update (POST)",
    "update_async": "/update/async (POST)",
    "db_stats": "/db/stats",
    "health": "/health"
  }
}
```

### `GET /status`
Current update status and progress.

**Response:**
```json
{
  "last_update": "2026-01-26T18:30:00",
  "status": "completed",
  "message": "Update completed successfully",
  "place_name": "Eixample, Barcelona, Spain",
  "db_path": "/path/to/eixample_osm.duckdb"
}
```

**Status values:**
- `idle` - No update in progress
- `updating` - Update currently running
- `completed` - Last update successful
- `failed` - Last update failed

### `POST /update`
Trigger a synchronous OSM data update. The request waits until the update completes.

**Request Body:**
```json
{
  "place_name": "Eixample, Barcelona, Spain",
  "db_path": "/path/to/custom.duckdb"
}
```

Both fields are optional and will use defaults if not provided.

**Response:**
```json
{
  "status": "success",
  "message": "Update completed",
  "place_name": "Eixample, Barcelona, Spain",
  "db_path": "/path/to/eixample_osm.duckdb",
  "timestamp": "2026-01-26T18:30:00"
}
```

⚠️ **Warning:** This endpoint may take several minutes to complete depending on the area size.

### `POST /update/async`
Trigger an asynchronous OSM data update. Returns immediately while the update runs in the background.

**Request Body:**
```json
{
  "place_name": "Eixample, Barcelona, Spain",
  "db_path": "/path/to/custom.duckdb"
}
```

**Response:**
```json
{
  "status": "scheduled",
  "message": "Update scheduled in background",
  "place_name": "Eixample, Barcelona, Spain",
  "db_path": "/path/to/eixample_osm.duckdb",
  "check_status_at": "/status"
}
```

Use `GET /status` to monitor progress.

### `GET /db/stats`
Get statistics about the DuckDB database.

**Response:**
```json
{
  "db_path": "/path/to/eixample_osm.duckdb",
  "tables": {
    "buildings": 8206,
    "amenities": 10891,
    "walk_nodes": 12056,
    "walk_edges": 24112,
    "drive_nodes": 743,
    "drive_edges": 1486
  }
}
```

### `GET /health`
Health check endpoint.

**Response:**
```json
{
  "status": "healthy",
  "database_exists": true,
  "last_update": "2026-01-26T18:30:00",
  "current_status": "idle"
}
```

## Configuration

### Environment Variables

- `OSM_SERVICE_PORT` - Port for the service (default: `8001`)
- `OSM_SERVICE_HOST` - Host address (default: `127.0.0.1`)
- `DB_PATH` - Custom database path (default: `Environment/OSM/eixample_osm.duckdb`)

### Using Custom Paths

**Via environment variable:**
```bash
export DB_PATH=/path/to/custom.duckdb
python osm_update_service.py
```

**Via API request:**
```bash
curl -X POST http://localhost:8001/update \
  -H "Content-Type: application/json" \
  -d '{"db_path": "/path/to/custom.duckdb"}'
```

### Changing the Port

```bash
export OSM_SERVICE_PORT=9000
python osm_update_service.py
```

## Integration with Map Server

The map server (`AGENT/OSM/map_server.py`) automatically uses the same database path via the shared `config.py` file. Both services can be run simultaneously:

**Terminal 1 - OSM Update Service:**
```bash
cd Environment/OSM
python osm_update_service.py
```

**Terminal 2 - Map Server:**
```bash
cd AGENT/OSM
python map_server.py
```

Now you can:
1. Update OSM data via the update service
2. The map server will automatically load the new data on restart or when querying

## Data Extracted

The service extracts the following OSM data:

### Buildings
- Polygon geometries
- Building types
- Height and levels (when available)
- All OSM tags

### Amenities/POIs
- Amenities (cafes, restaurants, etc.)
- Shops
- Offices
- Leisure facilities
- Craft businesses
- Emergency services
- Tourism attractions

### Walk Network
- Nodes (intersection points)
- Edges (walkable paths)
- Path attributes

### Drive Network
- Nodes (intersection points)
- Edges (drivable roads)
- Road attributes

## Database Schema

All tables include a `geometry` column with spatial data:

- `buildings` - GEOMETRY (Polygon)
- `amenities` - GEOMETRY (Point or Polygon)
- `walk_nodes` - GEOMETRY (Point)
- `walk_edges` - GEOMETRY (LineString)
- `drive_nodes` - GEOMETRY (Point)
- `drive_edges` - GEOMETRY (LineString)

The database uses DuckDB's spatial extension with full support for spatial queries.

## Scheduling Automatic Updates

To keep OSM data fresh, you can schedule automatic updates using cron or a task scheduler.

### Using cron (Linux/Mac)

```bash
# Update every day at 3 AM
0 3 * * * curl -X POST http://localhost:8001/update/async
```

### Using Task Scheduler (Windows)

Create a scheduled task that runs:
```powershell
curl -X POST http://localhost:8001/update/async
```

### Using Python script

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

### Service won't start
- Check if port 8001 is already in use
- Try a different port: `OSM_SERVICE_PORT=9000 python osm_update_service.py`
- Verify all dependencies are installed

### Update fails
- Check network connectivity (OSM data is downloaded from the internet)
- Verify the place name is valid for osmnx
- Check disk space for the database file
- Review logs for specific error messages

### Database not found
- Ensure the database path is correct
- Run an update to create the database
- Check file permissions

### Map server can't connect to database
- Verify both services use the same database path
- Check that the update service has completed successfully
- Restart the map server after updating data

## API Documentation

Once the service is running, visit:
- Interactive API docs: `http://localhost:8001/docs`
- Alternative docs: `http://localhost:8001/redoc`

## Development

### Running with auto-reload

The service includes auto-reload by default:
```bash
python osm_update_service.py
```

Changes to the code will automatically reload the service.

### Testing the API

Use the interactive API documentation at `/docs` or use curl/Postman to test endpoints.

## License

Part of the LLM-based UrbanABM project.
