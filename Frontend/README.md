# Urban ABM Frontend

This is the frontend visualization for the Urban Agent-Based Model. It fetches data from the backend API and displays:
- Buildings
- Walk networks
- Road networks
- Amenities
- Agent positions and behaviors

## Usage

1. **Start the Backend Server** (from Backend/Agent directory):
   ```bash
   cd Backend\Agent
   python map_server.py
   ```
   The backend will run on http://127.0.0.1:8000

2. **Open the Frontend**:
   - Simply open `index.html` in your web browser
   - Or use a local web server:
     ```bash
     # Using Python
     python -m http.server 8080
     
     # Using Node.js
     npx http-server -p 8080
     ```
   - Navigate to http://localhost:8080

3. **Configure Backend URL** (if needed):
   - The default backend URL is `http://127.0.0.1:8000`
   - You can change it in the input field at the top of the info panel

## Features

- **Layer Controls**: Toggle visibility of buildings, networks, amenities, and agents
- **Agent Simulation**: Play/pause agent movement with adjustable speed
- **Agent Search**: Find and focus on specific agents by ID
- **Agent Details**: Click on agents to see what they perceive (nearby amenities)
- **Real-time Updates**: Agents move and query their environment continuously

## Architecture

The frontend is a standalone HTML file with:
- Leaflet.js for mapping
- Pure JavaScript (no build step required)
- Fetches GeoJSON data from the backend API
- Completely decoupled from backend (can be served separately)
