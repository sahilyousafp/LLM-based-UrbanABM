# Mapillary Integration - Data Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                         USER INTERACTION                         │
└─────────────────────────────────────────────────────────────────┘
                                │
                                │ 1. User clicks on agent
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                    FRONTEND (index.html)                         │
│                                                                   │
│  ┌────────────────────────────────────────────────────────┐    │
│  │  selectAgent(agentId)                                   │    │
│  │  ├─ showAgentSummary(agentId) [every 5s]              │    │
│  │  └─ showStreetView(agentId) [every 15s] ───────┐      │    │
│  └────────────────────────────────────────────────┼───────┘    │
│                                                     │            │
└─────────────────────────────────────────────────────┼───────────┘
                                                      │
                    2. HTTP GET Request               │
                    /api/agent/{id}/streetview        │
                                                      ▼
┌─────────────────────────────────────────────────────────────────┐
│                  BACKEND API (map_server.py)                     │
│                                                                   │
│  ┌────────────────────────────────────────────────────────┐    │
│  │  @app.get("/api/agent/{agent_id}/streetview")          │    │
│  │  ├─ Find agent by ID                                   │    │
│  │  ├─ Get agent location (lon, lat)                      │    │
│  │  └─ Call mapillary_service.get_images_near_location() │    │
│  └────────────────────────────────┬───────────────────────┘    │
│                                    │                             │
└────────────────────────────────────┼────────────────────────────┘
                                     │
                3. Service Call      │
                                     ▼
┌─────────────────────────────────────────────────────────────────┐
│              MAPILLARY SERVICE (mapillary_service.py)            │
│                                                                   │
│  ┌────────────────────────────────────────────────────────┐    │
│  │  MapillaryService.get_images_near_location()            │    │
│  │  ├─ Calculate bounding box (50m radius)                │    │
│  │  ├─ Build API request                                  │    │
│  │  └─ HTTP GET to Mapillary API ─────────────┐          │    │
│  └────────────────────────────────────────────┼───────────┘    │
│                                                │                 │
└────────────────────────────────────────────────┼────────────────┘
                                                 │
                        4. External API Call     │
                                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                     MAPILLARY API                                │
│                  (graph.mapillary.com)                           │
│                                                                   │
│  • Search images within bounding box                             │
│  • Filter by location (lon, lat ± 50m)                          │
│  • Return top 10 images with:                                   │
│    - Image IDs                                                   │
│    - Thumbnail URLs (256px, 1024px, 2048px)                     │
│    - Capture timestamps                                          │
│    - Compass angles                                              │
│    - Coordinates                                                 │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
                                                 │
                        5. API Response          │
                                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│              MAPILLARY SERVICE (mapillary_service.py)            │
│                                                                   │
│  • Parse API response                                            │
│  • Format image data                                             │
│  • Return list of image objects                                  │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
                                                 │
                        6. Return data           │
                                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                  BACKEND API (map_server.py)                     │
│                                                                   │
│  • Build JSON response with:                                     │
│    {                                                              │
│      "agent_id": 123,                                            │
│      "location": {"lon": 2.173, "lat": 41.395},                 │
│      "images": [...],                                            │
│      "image_count": 8                                            │
│    }                                                              │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
                                                 │
                        7. HTTP Response         │
                                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                    FRONTEND (index.html)                         │
│                                                                   │
│  ┌────────────────────────────────────────────────────────┐    │
│  │  showStreetView(agentId)                                │    │
│  │  ├─ Clear previous images                              │    │
│  │  ├─ Create image elements                              │    │
│  │  ├─ Add click handlers (open on Mapillary.com)         │    │
│  │  └─ Display in 2-column grid                           │    │
│  └────────────────────────────────────────────────────────┘    │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
                                                 │
                        8. Display               │
                                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                         USER SEES                                │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Agent Summary Panel                                      │  │
│  │  ├─ Agent ID and location                                │  │
│  │  ├─ LLM-generated perspective                            │  │
│  │  └─ Street View Images (2×4 grid)                        │  │
│  │     ├─ [Image 1] [Image 2]                               │  │
│  │     ├─ [Image 3] [Image 4]                               │  │
│  │     ├─ [Image 5] [Image 6]                               │  │
│  │     └─ [Image 7] [Image 8]                               │  │
│  │     "Updates every 15s | Powered by Mapillary"           │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘

                                ⟳
              [Automatic refresh every 15 seconds]
```

## Update Intervals

- **Agent Summary**: Refreshes every 5 seconds
- **Street View Images**: Refreshes every 15 seconds
- Both stop when agent is deselected

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/agent/{id}/summary` | GET | Get LLM-generated agent perspective |
| `/api/agent/{id}/streetview` | GET | Get Mapillary images near agent |

## Configuration Variables

| Variable | Location | Default | Description |
|----------|----------|---------|-------------|
| `MAPILLARY_API_KEY` | map_server.py | MLY\|... | API key |
| `radius` | map_server.py:318 | 50 | Search radius (meters) |
| `limit` | mapillary_service.py:37 | 10 | Max images |
| Update interval | index.html:433 | 15000 | Refresh time (ms) |
