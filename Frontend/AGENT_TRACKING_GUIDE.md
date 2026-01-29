# Agent Tracking & Highlighting Guide

## 🎯 New Features Added to Mapbox Frontend

### 1. **Agent Highlighting** ✨

When you select an agent (by clicking or searching), it now gets highlighted with:
- **Gold outer ring** (15px radius, pulsing effect with transparency)
- **Bright gold marker** (10px radius, solid)
- **Orange stroke** (3px width) for high visibility

The selected agent stands out clearly from regular agents (orange, 7px).

### 2. **Movement Trail** 🔮

Selected agents leave a **purple trail** showing their path:
- **Line**: Purple (#9B59B6), 4px width, 80% opacity
- **Dots**: Purple circles at each recorded position
- **History**: Last 50 positions tracked
- **Auto-updates**: Trail grows as agent moves during simulation

### 3. **Layer Control**

New toggle added to control panel:
- **Agent Trail** checkbox with purple color indicator
- Toggle visibility on/off without losing trail data

## 🎮 How to Use

### Select an Agent

**Method 1: Click on Map**
1. Click any orange agent circle
2. Agent highlights in gold
3. Agent panel appears on left
4. Map flies to agent location

**Method 2: Search by ID**
1. Enter agent ID in search box (right panel)
2. Click "Find" button (or press Enter)
3. Same highlighting and panel behavior

### Watch the Trail

1. Select an agent (either method)
2. Click **▶ Play Simulation**
3. Watch the purple trail grow behind the agent
4. Trail shows last 50 positions

### Clear Selection

Click the **×** button in the agent panel to:
- Hide agent panel
- Remove highlight
- Clear trail
- Stop automatic updates

## 🎨 Visual Design

### Color Scheme
```
Regular Agents:    Orange (#F39C12)
Selected Agent:    Gold (#FFD700)
Agent Trail:       Purple (#9B59B6)
Trail Dots:        Purple (#9B59B6)
```

### Layering Order (Bottom to Top)
1. Buildings (gray)
2. Walk Network (blue)
3. Roads (red)
4. Amenities (green)
5. Regular Agents (orange)
6. Agent Trail (purple)
7. Selected Agent Ring (gold, transparent)
8. Selected Agent Marker (gold, solid)

## ⚙️ Technical Details

### Trail Tracking
- **Storage**: `agentTrails` object maps agent ID to coordinate array
- **Format**: `[[lon, lat], [lon, lat], ...]`
- **Max Length**: 50 positions (configurable via `maxTrailLength`)
- **Update Frequency**: Every simulation step (adjustable with speed slider)

### Performance
- Only tracks trail for **currently selected agent**
- Previous trails cleared when selecting new agent
- Efficient GeoJSON updates using Mapbox GL JS
- No performance impact on unselected agents

### Highlight System
- Uses separate Mapbox layer (`selected-agent`)
- Two circle layers for layered effect (ring + marker)
- Position updates in real-time during simulation
- Automatically clears when agent deselected

## 🔧 Customization

### Change Trail Color
Edit line 923 in `mapbox.html`:
```javascript
'line-color': '#9B59B6',  // Change to your color
```

### Adjust Trail Length
Edit line 515 in `mapbox.html`:
```javascript
let maxTrailLength = 50;  // Change to desired length
```

### Modify Highlight Size
Edit lines 743-758 in `mapbox.html`:
```javascript
'circle-radius': 15,  // Outer ring size
'circle-radius': 10,  // Inner marker size
```

### Change Highlight Color
Edit lines 745 and 753 in `mapbox.html`:
```javascript
'circle-color': '#FFD700',  // Your highlight color
```

## 🐛 Troubleshooting

### Trail Not Showing
- **Check**: Is "Agent Trail" layer enabled? (checkbox in panel)
- **Check**: Has agent moved? (need at least 2 positions for line)
- **Solution**: Start simulation and wait for agent to move

### Highlight Not Visible
- **Check**: Is agent actually selected? (panel should be open)
- **Check**: Are you zoomed out too far? (highlight more visible at zoom 15+)
- **Solution**: Zoom in or use search to fly to agent

### Trail Disappears
- **Expected**: Trail clears when you select a different agent
- **Expected**: Trail clears when you close agent panel (click ×)
- **Solution**: This is normal behavior to keep map clean

## 📊 Data Structure

### Agent Trail Format
```javascript
agentTrails = {
    42: [
        [2.17234, 41.39876],  // Oldest position
        [2.17235, 41.39877],
        [2.17236, 41.39878],
        // ... up to 50 positions
        [2.17289, 41.39923]   // Current position
    ]
}
```

### Selected Agent GeoJSON
```javascript
{
    type: 'FeatureCollection',
    features: [{
        type: 'Feature',
        geometry: {
            type: 'Point',
            coordinates: [lon, lat]
        },
        properties: {
            id: agentId
        }
    }]
}
```

## 🚀 Future Enhancements

Possible improvements:
- **Fade trail**: Gradient opacity from old to new
- **Multiple trails**: Track multiple agents simultaneously
- **Trail persistence**: Save/load trails across sessions
- **Heatmap mode**: Show aggregate movement patterns
- **Speed indicator**: Color trail by agent speed
- **Path prediction**: Show likely future path

---

**Enjoy tracking your agents!** 🎉
