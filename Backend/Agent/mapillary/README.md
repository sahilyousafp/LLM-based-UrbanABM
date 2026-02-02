# Mapillary Street View Integration

This module provides integration with the Mapillary API to fetch street view images for agent locations in the Urban ABM system.

## Features

- **Automatic street view fetching**: When an agent is selected in the frontend, street view images within a 50-meter radius are automatically fetched from Mapillary
- **Periodic updates**: Street view images and agent summaries are refreshed every 5 seconds while an agent is selected
- **Interactive display**: Images can be clicked to open the full view on Mapillary's website
- **Bulk summaries endpoint**: New endpoint to fetch all agent summaries simultaneously
- **Optimized resolution**: Always fetches 1024px resolution images (thumb_1024_url) for optimal balance between quality and performance

## Image Resolution

**Primary Resolution: 1024x768 pixels**

The system is configured to always use **1024px resolution** (`thumb_1024_url`) for displaying street view images:
- **Quality**: High enough for detailed viewing
- **Performance**: Fast loading times
- **Bandwidth**: Reasonable data usage

Alternative resolutions are also fetched but not displayed by default:
- `thumb_256_url` (256px) - Low-res thumbnail
- `thumb_2048_url` (2048px) - High-res for future features

To change the displayed resolution, modify the `thumb_url` mapping in `mapillary_service.py` line 53.

## API Key

The Mapillary API key is configured in `map_server.py`:

```python
MAPILLARY_API_KEY = "MLY|33533093396335529|30cb7c42be1a23189b63952f439551bd"
```

## Architecture

### Backend Components

1. **`mapillary_service.py`**: Core service that handles API communication with Mapillary
   - `get_images_near_location(lon, lat, radius)`: Fetches images within a specified radius
   - `_calculate_bbox(lon, lat, radius)`: Calculates bounding box for API query

2. **API Endpoints** (`map_server.py`):
   - `GET /api/agent/{agent_id}/streetview`: Returns Mapillary images near the agent's current location
   - `GET /api/agent/{agent_id}/summary`: Returns LLM-generated summary for a single agent
   - `GET /api/agents/summaries`: Returns LLM-generated summaries for ALL agents simultaneously

### Frontend Components

Located in `Frontend/index.html` and `Frontend/mapbox.html`:

- **Street View Container**: Displays images below the agent summary
- **`showStreetView(agentId)`**: Fetches and displays street view images
- **Auto-refresh**: Updates every 5 seconds when an agent is selected (both summary and street views)

## Usage

1. **Start the backend server**:
   ```bash
   cd Backend\Agent
   python map_server.py
   ```

2. **Open the frontend**: Open `Frontend/index.html` or `Frontend/mapbox.html` in a web browser

3. **Select an agent**: Click on any agent (orange circle) on the map

4. **View street views**: Street view images will appear below the agent's summary, updating every 5 seconds

## API Response Formats

### Single Agent Streetview
**Endpoint**: `GET /api/agent/{agent_id}/streetview`

```json
{
  "agent_id": 123,
  "location": {
    "lon": 2.1734,
    "lat": 41.3951
  },
  "images": [
    {
      "id": "image_id",
      "thumb_url": "https://...",
      "thumb_small": "https://...",
      "thumb_large": "https://...",
      "captured_at": "2024-01-01T12:00:00Z",
      "compass_angle": 180.5,
      "coordinates": [2.1734, 41.3951]
    }
  ],
  "image_count": 5
}
```

### All Agent Summaries
**Endpoint**: `GET /api/agents/summaries`

```json
{
  "total_agents": 500,
  "summaries": [
    {
      "agent_id": 0,
      "summary": "I'm walking through...",
      "location": {
        "lon": 2.1734,
        "lat": 41.3951
      },
      "amenity_count": 12
    },
    {
      "agent_id": 1,
      "summary": "I notice several...",
      "location": {
        "lon": 2.1745,
        "lat": 41.3960
      },
      "amenity_count": 8
    }
  ]
}
```

## Configuration

### Adjust Update Frequency

To change the 5-second update interval, modify this line in `Frontend/index.html` or `Frontend/mapbox.html`:

```javascript
summaryUpdateInterval = setInterval(() => {
    if (selectedAgentId === agentId) {
        showAgentSummary(agentId);
        showStreetView(agentId);
    }
}, 5000); // Change this value (in milliseconds)
```

### Adjust Search Radius

To change the 50-meter search radius, modify the `radius` parameter in `map_server.py`:

```python
images = mapillary_service.get_images_near_location(
    lon=agent.geometry.x,
    lat=agent.geometry.y,
    radius=50  # Change this value (in meters)
)
```

## Update Intervals

- **Agent Summary**: Refreshes every 5 seconds
- **Street View Images**: Refreshes every 5 seconds
- **Combined**: Both use a single interval to reduce overhead

## Error Handling

- If no images are available in the area, a message is displayed: "No street views available in this area"
- If the API fails, an error message is shown: "Error loading street views"
- API errors are logged to the backend console

## Dependencies

- `requests`: For making HTTP requests to the Mapillary API (already in `requirements.txt`)
- `math`: For calculating bounding boxes (Python standard library)

## Troubleshooting

**No images displayed**:
- Check backend console for API error messages
- Verify the API key is valid
- Ensure the selected location has Mapillary coverage (urban areas typically have better coverage)

**Images not updating**:
- Check browser console for JavaScript errors
- Verify the backend server is running
- Check that the agent is still selected (marker should be gold/yellow)

**Bulk summaries slow**:
- The `/api/agents/summaries` endpoint processes all 500 agents
- This may take several seconds to complete
- Consider adding pagination or limiting to visible agents only

## Future Enhancements

Potential improvements:
- Cache recently fetched images to reduce API calls
- Add image filtering options (date range, compass angle)
- Display image metadata (capture date, direction) in UI
- Add support for viewing 360° panoramas
- Integrate street view direction with agent movement direction
- Add pagination to bulk summaries endpoint
- Add filtering to bulk summaries (by location, amenity count, etc.)

