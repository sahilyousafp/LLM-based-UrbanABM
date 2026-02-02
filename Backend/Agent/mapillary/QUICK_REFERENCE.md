# Quick Reference: Update Intervals & Endpoints

## Update Intervals (Current)

| Component | Interval | Frontend Files |
|-----------|----------|----------------|
| Agent Summary | 5 seconds | index.html, mapbox.html |
| Street View Images | 5 seconds | index.html, mapbox.html |
| Combined Updates | 5 seconds | Both use single timer |

## API Endpoints

### Single Agent
```
GET /api/agent/{agent_id}/summary
GET /api/agent/{agent_id}/streetview
```
**Response Time**: < 1 second  
**Use Case**: Real-time agent monitoring  
**Image Resolution**: 1024x768 pixels (thumb_1024_url)

### All Agents (Bulk)
```
GET /api/agents/summaries
```
**Response Time**: 10-30 seconds (500 agents)  
**Use Case**: Analytics, dashboards, bulk export

## Code Examples

### Frontend: Update Every 5 Seconds
```javascript
summaryUpdateInterval = setInterval(() => {
    if (selectedAgentId === agentId) {
        showAgentSummary(agentId);    // Agent perspective
        showStreetView(agentId);       // Mapillary images
    }
}, 5000);  // 5 seconds
```

### Backend: Bulk Summaries
```python
@app.get("/api/agents/summaries")
async def get_all_agent_summaries():
    summaries = []
    for agent in city_model.city_agents:
        # Generate summary for each agent
        summary = llm_service.summarize_agent_perspective(agent_data)
        summaries.append({
            "agent_id": agent.unique_id,
            "summary": summary,
            "location": agent_data["location"],
            "amenity_count": len(agent.nearby_amenities)
        })
    return {"total_agents": len(summaries), "summaries": summaries}
```

## Testing Commands

### Test Bulk Summaries Endpoint
```bash
cd Backend\Agent\mapillary
python test_bulk_summaries.py
```

### Test with curl
```bash
# Single agent summary
curl http://127.0.0.1:8000/api/agent/0/summary

# Single agent street view
curl http://127.0.0.1:8000/api/agent/0/streetview

# All agent summaries
curl http://127.0.0.1:8000/api/agents/summaries
```

## Performance Tips

### For Single Agent Updates (5s)
✅ **Good**: Fast, responsive  
✅ **Impact**: Minimal (< 1s per request)  
✅ **Recommendation**: No changes needed

### For Bulk Summaries
⚠️ **Slow**: 10-30 seconds for 500 agents  
⚠️ **Impact**: High (500 LLM calls)  
📝 **Recommendations**:
1. Add caching (Redis, in-memory)
2. Implement pagination
3. Add filtering by location/area
4. Consider background processing

## Configuration

### Change Update Frequency
**File**: `Frontend/index.html` or `Frontend/mapbox.html`
```javascript
}, 5000);  // Change this (in milliseconds)
```

Common values:
- 1000 = 1 second (very fast, high API load)
- 5000 = 5 seconds (current, balanced)
- 10000 = 10 seconds (slower, lower load)
- 30000 = 30 seconds (slow, minimal load)

### Change Mapillary Search Radius
**File**: `Backend/Agent/map_server.py`
```python
radius=50  # Change this (in meters)
```

Common values:
- 25 = Very close (fewer images)
- 50 = Default (balanced)
- 100 = Wide area (more images)
- 200 = Very wide (many images)

## Monitoring

### Check Update Frequency
Open browser console and look for:
```
Fetching LLM summary for agent: 123
Fetching Mapillary street view for agent: 123
```
Should appear every 5 seconds when agent is selected.

### Check API Performance
```bash
# Measure bulk summaries response time
time curl http://127.0.0.1:8000/api/agents/summaries
```

## Troubleshooting

### Updates Not Working
1. Check browser console for errors
2. Verify backend is running: `http://127.0.0.1:8000`
3. Ensure agent is selected (gold marker)
4. Check network tab for API calls

### Bulk Summaries Timeout
1. Increase request timeout (default 60s)
2. Consider pagination or filtering
3. Check backend logs for errors
4. Monitor server resources (CPU, memory)

### Slow Performance
1. Check network speed
2. Monitor LLM service response time
3. Consider caching summaries
4. Reduce update frequency if needed

## Best Practices

### Development
- Use 5-10 second intervals
- Test with single agent first
- Monitor browser console

### Production
- Consider 10-30 second intervals (lower load)
- Implement caching for bulk endpoints
- Add rate limiting
- Monitor API usage

### Testing
- Use test scripts before frontend
- Check response times
- Verify data format
- Test error handling

## File Locations

```
Backend/
  Agent/
    map_server.py                  # API endpoints
    mapillary/
      mapillary_service.py         # Mapillary API integration
      test_bulk_summaries.py       # Test bulk endpoint
      README.md                    # Full documentation

Frontend/
  index.html                       # Leaflet version (5s updates)
  mapbox.html                      # Mapbox version (5s updates)
```

## Version History

| Date | Change | Interval |
|------|--------|----------|
| 2026-02-02 | Added bulk summaries endpoint | N/A |
| 2026-02-02 | Updated street view interval | 15s → 5s |
| 2026-02-02 | Standardized summary interval | 3s/5s → 5s |
| 2026-02-02 | Combined update timers | Separate → Single |

---

**Last Updated**: 2026-02-02  
**Version**: 2.0
