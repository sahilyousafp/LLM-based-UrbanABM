# Update Summary: 5-Second Intervals and Bulk Summaries

## Changes Made (2026-02-02)

### Overview
Updated the system to use 5-second refresh intervals for both agent summaries and Mapillary street views (previously 15 seconds for street views). Added a new endpoint to fetch all agent summaries simultaneously.

---

## Backend Changes

### 1. New Endpoint: `/api/agents/summaries`

**File**: `Backend/Agent/map_server.py`

**Purpose**: Fetch LLM-generated summaries for ALL agents simultaneously

**Response Format**:
```json
{
  "total_agents": 500,
  "summaries": [
    {
      "agent_id": 0,
      "summary": "I'm walking through a bustling neighborhood...",
      "location": {
        "lon": 2.1734,
        "lat": 41.3951
      },
      "amenity_count": 12
    },
    ...
  ]
}
```

**Implementation**:
```python
@app.get("/api/agents/summaries")
async def get_all_agent_summaries():
    """Get LLM-generated summaries for all agents simultaneously"""
    summaries = []
    
    for agent in city_model.city_agents:
        agent_data = {
            "id": agent.unique_id,
            "type": agent.agent_type,
            "location": {
                "lon": agent.geometry.x,
                "lat": agent.geometry.y
            },
            "nearby_amenities": agent.nearby_amenities
        }
        
        summary = llm_service.summarize_agent_perspective(agent_data)
        
        summaries.append({
            "agent_id": agent.unique_id,
            "summary": summary,
            "location": agent_data["location"],
            "amenity_count": len(agent.nearby_amenities)
        })
    
    return {
        "total_agents": len(summaries),
        "summaries": summaries
    }
```

**Performance Notes**:
- Processes all 500 agents
- May take several seconds to complete
- Consider caching or pagination for production use

### 2. Updated API Documentation

**File**: `Backend/Agent/map_server.py`

Added `/api/agents/summaries` to the root endpoint documentation.

---

## Frontend Changes

### 1. index.html (Leaflet Version)

**File**: `Frontend/index.html`

**Changes**:
- Combined update intervals into single 5-second timer
- Removed separate `streetviewUpdateInterval` variable
- Both summary and street view now update together every 5 seconds
- Updated UI text: "Updates every 5s" (was "Updates every 15s")

**Before**:
```javascript
// Two separate intervals
summaryUpdateInterval = setInterval(() => {
    showAgentSummary(agentId);
}, 5000);

streetviewUpdateInterval = setInterval(() => {
    showStreetView(agentId);
}, 15000);
```

**After**:
```javascript
// Single combined interval
summaryUpdateInterval = setInterval(() => {
    if (selectedAgentId === agentId) {
        showAgentSummary(agentId);
        showStreetView(agentId);
    }
}, 5000);
```

### 2. mapbox.html (Mapbox Version)

**File**: `Frontend/mapbox.html`

**Changes**:
- Updated from 15-second to 5-second interval
- Both summary and street view update together

**Before**:
```javascript
}, 15000); // Update every 15 seconds
```

**After**:
```javascript
}, 5000); // Update every 5 seconds
```

---

## Documentation Updates

### 1. README.md

**File**: `Backend/Agent/mapillary/README.md`

**Updates**:
- Changed all references from 15 seconds to 5 seconds
- Added documentation for new `/api/agents/summaries` endpoint
- Added API response format for bulk summaries
- Added performance notes about bulk endpoint
- Updated configuration examples
- Added troubleshooting for bulk summaries

### 2. New Test Script

**File**: `Backend/Agent/mapillary/test_bulk_summaries.py`

**Purpose**: Test the new bulk summaries endpoint

**Features**:
- Measures response time
- Displays first 3 agent summaries
- Calculates statistics (avg, min, max amenities)
- Identifies agent with most amenities
- Proper error handling

**Usage**:
```bash
cd Backend\Agent\mapillary
python test_bulk_summaries.py
```

---

## Summary of Timing Changes

| Feature | Before | After |
|---------|--------|-------|
| Agent Summary Updates | 3s (mapbox), 5s (index) | 5s (both) |
| Street View Updates | 15s | 5s |
| Combined Interval | Separate timers | Single 5s timer |

---

## Benefits of Changes

### 1. Faster Updates
- Street view images refresh 3x faster (5s vs 15s)
- More responsive user experience
- Better real-time visualization

### 2. Simplified Code
- Single interval instead of two separate timers
- Easier to maintain
- Reduced complexity

### 3. Consistent Timing
- Both frontends now use same 5-second interval
- Uniform behavior across interfaces

### 4. Bulk Summaries Endpoint
- Can fetch all agent summaries in one request
- Useful for dashboards and analytics
- Enables new visualization possibilities

---

## Performance Considerations

### Single Agent Updates (5s interval)
**Impact**: Minimal
- Single agent data is lightweight
- Quick LLM response
- Mapillary API handles individual requests well

### Bulk Summaries Endpoint
**Impact**: Significant
- Processes 500 agents
- 500 LLM calls (if not cached)
- May take 10-30 seconds depending on LLM service
- **Recommendation**: Add caching or pagination for production

---

## API Endpoints Summary

| Endpoint | Method | Purpose | Update Frequency |
|----------|--------|---------|------------------|
| `/api/agent/{id}/summary` | GET | Single agent summary | On-demand / 5s |
| `/api/agent/{id}/streetview` | GET | Single agent street views | On-demand / 5s |
| `/api/agents/summaries` | GET | All agent summaries | On-demand |

---

## Testing

### Test Single Agent Updates
1. Start backend: `python Backend\Agent\map_server.py`
2. Open `Frontend/index.html` or `Frontend/mapbox.html`
3. Click an agent
4. Observe updates every 5 seconds

### Test Bulk Summaries
1. Ensure backend is running
2. Run: `python Backend\Agent\mapillary\test_bulk_summaries.py`
3. Check console output for statistics

**Or test with curl**:
```bash
curl http://127.0.0.1:8000/api/agents/summaries
```

---

## Files Modified

### Backend
1. ✅ `Backend/Agent/map_server.py`
   - Added `/api/agents/summaries` endpoint
   - Updated API documentation

### Frontend
2. ✅ `Frontend/index.html`
   - Combined intervals to 5 seconds
   - Removed `streetviewUpdateInterval` variable
   - Updated UI text

3. ✅ `Frontend/mapbox.html`
   - Changed interval from 15s to 5s

### Documentation
4. ✅ `Backend/Agent/mapillary/README.md`
   - Updated all timing references
   - Added bulk summaries documentation
   - Added performance notes

### Testing
5. ✅ `Backend/Agent/mapillary/test_bulk_summaries.py` (NEW)
   - Test script for bulk endpoint

---

## Future Enhancements

### Short Term
- Add caching to bulk summaries endpoint
- Implement pagination (e.g., 50 agents per page)
- Add filtering options (by location, amenity count)

### Medium Term
- Add WebSocket support for real-time updates
- Implement server-sent events (SSE) for streaming summaries
- Add rate limiting to bulk endpoint

### Long Term
- Implement agent summary caching with Redis
- Add incremental updates (only changed agents)
- Create dedicated dashboard for bulk analytics

---

## Breaking Changes

**None** - All changes are backward compatible

---

## Migration Guide

If you have custom code using the old timing:

1. **If you were using 15-second intervals**: Update to 5 seconds
2. **If you had separate intervals**: Combine into single interval
3. **If you need bulk data**: Use new `/api/agents/summaries` endpoint

---

## Rollback Instructions

If needed, to revert to previous timing:

```javascript
// In index.html and mapbox.html, change:
}, 5000);
// back to:
}, 15000);
```

And remove the bulk summaries endpoint from `map_server.py`.

---

## Support

**Issues**: Check backend console for errors
**Performance**: Monitor response times with test script
**Questions**: See README.md for detailed documentation
