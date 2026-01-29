# Urban ABM - Mapbox Frontend

A clean, minimal frontend visualization for the Urban Agent-Based Model using **Mapbox GL JS**.

## 🎨 Design Principles

- **Minimalist Interface**: Clean panels with subtle shadows and blur effects
- **Perceptually Uniform Colors**: Carefully selected color palette for optimal visibility
- **Responsive Layout**: Fluid panel system that adapts to content
- **Performance-First**: GPU-accelerated rendering via Mapbox GL JS
- **Visual Hierarchy**: Clear separation between controls, data, and information

## ✨ Features

### Map Visualization
- **Mapbox Light Style**: Minimal basemap that keeps focus on your data
- **Smooth Interactions**: Hardware-accelerated pan, zoom, and animations
- **Multiple Layers**:
  - Buildings (gray fill with subtle outlines)
  - Walk Network (blue lines)
  - Roads (red lines)
  - Amenities (green circles)
  - Agents (orange circles)

### Controls
- **Layer Toggles**: Show/hide individual layers with color indicators
- **Animation System**: Play/pause with adjustable speed (0.1s - 5s per step)
- **Agent Search**: Find and focus on specific agents by ID
- **Real-time Stats**: Step count, query metrics, and data counts

### Agent Interaction
- **Click to Select**: Click any agent to view detailed information
- **Auto-follow**: Map automatically flies to selected agent location
- **LLM Perspective**: View AI-generated summaries of agent context
- **Periodic Updates**: Agent panel refreshes every 3 seconds during simulation

## 🚀 Quick Start

### 1. Get a Mapbox Access Token

1. Go to [https://account.mapbox.com/access-tokens/](https://account.mapbox.com/access-tokens/)
2. Sign up for a free account (includes 50,000 free map loads per month)
3. Copy your default public token (or create a new one)

### 2. Configure the Frontend

Open `mapbox.html` and replace line 389:

```javascript
mapboxgl.accessToken = 'YOUR_MAPBOX_TOKEN_HERE';
```

With your actual token:

```javascript
mapboxgl.accessToken = 'pk.eyJ1IjoieW91cnVzZXJuYW1lIiwiYSI6ImNscmF...';
```

### 3. Start the Backend

```bash
cd Backend\Agent
python map_server.py
```

Backend runs on `http://127.0.0.1:8000` by default.

### 4. Open the Frontend

Simply open `mapbox.html` in your browser:

```bash
# Windows
start mapbox.html

# Or use a local server (optional)
python -m http.server 8080
```

## 🎯 Usage Guide

### Layer Management
- Use checkboxes in the right panel to toggle layer visibility
- Color indicators show each layer's color scheme
- Layers are rendered in optimal order for visual clarity

### Running Simulations
1. Click **▶ Play Simulation** to start agent movement
2. Adjust speed slider for faster/slower updates
3. Click **⏸ Pause** to stop the simulation
4. Step count and query metrics update in real-time

### Exploring Agents
1. **Click on Map**: Click any orange circle to select an agent
2. **Search by ID**: Enter agent ID and click "Find"
3. **View Details**: Agent panel shows location, nearby amenities, and LLM perspective
4. **Close Panel**: Click × to deselect agent

### Backend Configuration
- Change API URL in the input field if backend runs on different port
- Default: `http://127.0.0.1:8000`

## 🏗️ Technical Architecture

### Technology Stack
- **Mapbox GL JS v3.0.1**: WebGL-based map rendering
- **Pure JavaScript**: No build tools required
- **Vanilla CSS**: Modern flexbox/grid layouts
- **GeoJSON**: Standard format for spatial data

### Data Flow
```
Backend API (FastAPI/Flask)
    ↓
GeoJSON Endpoints
    ↓
Mapbox GL JS Sources
    ↓
Styled Layers
    ↓
Interactive Visualization
```

### Performance Optimizations
- **GPU Rendering**: All map layers use WebGL
- **Efficient Updates**: Only agent positions update during simulation
- **Smart Redraws**: Mapbox optimizes rendering automatically
- **Layer Caching**: Static data (buildings, network) loaded once

## 🎨 Customization

### Change Map Style
Replace the style in `mapbox.html` (line 393):

```javascript
style: 'mapbox://styles/mapbox/light-v11',
```

Available Mapbox styles:
- `mapbox://styles/mapbox/light-v11` (current - minimal)
- `mapbox://styles/mapbox/dark-v11` (dark theme)
- `mapbox://styles/mapbox/streets-v12` (detailed streets)
- `mapbox://styles/mapbox/outdoors-v12` (terrain-focused)
- `mapbox://styles/mapbox/satellite-v9` (satellite imagery)

Or create your own custom style in [Mapbox Studio](https://studio.mapbox.com/).

### Adjust Colors
Edit the `paint` properties in layer definitions:

```javascript
// Example: Change agent color
map.addLayer({
    id: 'agents-circles',
    type: 'circle',
    source: 'agents',
    paint: {
        'circle-color': '#YOUR_COLOR_HERE',
        'circle-radius': 7,
        // ... other properties
    }
});
```

### Modify Panel Positioning
Adjust CSS classes in the `<style>` section:

```css
.control-panel {
    top: 16px;
    right: 16px;  /* Change to 'left' for left side */
    width: 280px;
}
```

## 🔧 Troubleshooting

### Map Doesn't Load
- **Check Token**: Ensure Mapbox access token is valid
- **Browser Console**: Press F12 and check for errors
- **Network**: Verify internet connection for map tiles

### No Data Visible
- **Backend Running**: Ensure `map_server.py` is active
- **API URL**: Check API configuration in panel
- **Layer Toggles**: Verify layers are enabled (checkboxes checked)

### Poor Performance
- **Hardware**: Requires WebGL-capable GPU
- **Browser**: Use latest Chrome, Firefox, Edge, or Safari
- **Data Size**: Large datasets (>10k features) may slow rendering

## 📊 Comparison: Mapbox vs Leaflet

| Feature | Mapbox (`mapbox.html`) | Leaflet (`index.html`) |
|---------|------------------------|------------------------|
| **Rendering** | WebGL (GPU) | Canvas/SVG (CPU) |
| **Performance** | Excellent (100k+ points) | Good (10k points) |
| **Style Flexibility** | High (custom styles) | Medium (tile layers) |
| **File Size** | ~30KB HTML | ~15KB HTML |
| **Dependencies** | Mapbox GL JS | Leaflet.js |
| **Free Tier** | 50k loads/month | Unlimited |
| **3D Support** | Yes (pitch/bearing) | No |
| **Design** | Modern, minimal | Traditional |

## 📝 License & Attribution

- **Mapbox**: Requires attribution per [Terms of Service](https://www.mapbox.com/legal/tos/)
- **Map Data**: © Mapbox, © OpenStreetMap contributors
- **Your Code**: Add your license here

## 🤝 Contributing

To enhance this frontend:

1. **Add New Layers**: Define new sources and layers in Mapbox
2. **Style Improvements**: Modify CSS classes and paint properties
3. **Features**: Extend with popups, filters, or analysis tools
4. **Optimization**: Profile performance and optimize rendering

## 📚 Resources

- [Mapbox GL JS Documentation](https://docs.mapbox.com/mapbox-gl-js/)
- [Mapbox Style Specification](https://docs.mapbox.com/mapbox-gl-js/style-spec/)
- [GeoJSON Specification](https://geojson.org/)
- [Mapbox Examples](https://docs.mapbox.com/mapbox-gl-js/example/)

---

**Built with ❤️ for Urban Agent-Based Modeling**
