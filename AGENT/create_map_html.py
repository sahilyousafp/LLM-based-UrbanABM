import os
import datetime

html_content = """<!DOCTYPE html>
<html>
<head>
    <title>DuckDB Spatial Data Map</title>
    <meta charset="utf-8" />
    <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate" />
    <meta http-equiv="Pragma" content="no-cache" />
    <meta http-equiv="Expires" content="0" />
    <!-- Template generated: """ + datetime.datetime.now().isoformat() + """ -->
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
        body { margin: 0; padding: 0; font-family: Arial, sans-serif; }
        #map { position: absolute; top: 0; bottom: 0; width: 100%; }
        .info-panel { 
            position: absolute; 
            top: 10px; 
            right: 10px; 
            background: white; 
            padding: 15px; 
            border-radius: 5px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.3);
            z-index: 2000; 
            max-width: 300px; 
        }
        .info-panel h3 { margin: 0 0 10px 0; font-size: 16px; }
        .layer-control { margin: 5px 0; }
        .stats { font-size: 12px; color: #666; margin-top: 10px; border-top: 1px solid #ddd; padding-top: 10px; }
        .loading { 
            position: absolute; 
            top: 50%; 
            left: 50%; 
            transform: translate(-50%, -50%);
            background: white; 
            padding: 20px; 
            border-radius: 5px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.3);
            z-index: 2000;
        }
    </style>
</head>
<body>
    <div id="loading" class="loading">Loading data from DuckDB...</div>
    <div id="map"></div>
    <div class="info-panel">
        <h3>DuckDB Spatial Data</h3>
        <div class="layer-control">
            <label><input type="checkbox" id="layer-buildings" checked> Buildings</label>
        </div>
        <div class="layer-control">
            <label><input type="checkbox" id="layer-network" checked> Walk Network (Blue)</label>
        </div>
        <div class="layer-control">
            <label><input type="checkbox" id="layer-roads" checked> Roads (Red)</label>
        </div>
        <div class="layer-control">
            <label><input type="checkbox" id="layer-amenities" checked> Amenities</label>
        </div>
        <div class="layer-control">
            <label><input type="checkbox" id="layer-agents" checked> Agents (Orange)</label>
        </div>
        <div class="stats" id="stats">Loading...</div>
        <div id="agent-info" style="display:none; margin-top:10px; padding-top:10px; border-top:1px solid #ddd;">
            <h4 style="margin:5px 0;">Agent <span id="agent-id"></span></h4>
            <p style="margin:5px 0; font-size:12px;">Type: <span id="agent-type"></span></p>
            <p style="margin:5px 0; font-size:12px;">Location: <span id="agent-location"></span></p>
            <div id="agent-nearby" style="font-size:11px; max-height:150px; overflow-y:auto;"></div>
        </div>
    </div>
    <script>
        const map = L.map('map').setView([41.39, 2.17], 15);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '© OpenStreetMap',
            maxZoom: 19
        }).addTo(map);

        const layers = {
            buildings: L.layerGroup().addTo(map),
            network: L.layerGroup().addTo(map),
            roads: L.layerGroup().addTo(map),
            amenities: L.layerGroup().addTo(map),
            agents: L.layerGroup().addTo(map)
        };

        async function loadAll() {
            try {
                console.log('Starting to load data...');
                
                // Check available tables
                const tablesRes = await fetch('/api/tables');
                const tables = await tablesRes.json();
                console.log('Available tables:', tables);
                
                // Load stats
                const statsRes = await fetch('/api/stats');
                const stats = await statsRes.json();
                console.log('Stats:', stats);
                
                document.getElementById('stats').innerHTML = 
                    '<strong>Database:</strong><br>' +
                    'Buildings: ' + (stats.buildings || 0).toLocaleString() + '<br>' +
                    'Walk Network: ' + (stats.walk_edges || 0).toLocaleString() + '<br>' +
                    'Amenities: ' + (stats.amenities || 0).toLocaleString() + '<br>' +
                    'Tables: ' + Object.keys(tables).join(', ');
                
                if (stats.bbox) {
                    map.fitBounds([
                        [stats.bbox.minLat, stats.bbox.minLon],
                        [stats.bbox.maxLat, stats.bbox.maxLon]
                    ]);
                }
                
                // Load buildings
                console.log('Loading buildings...');
                const buildingsRes = await fetch('/api/buildings');
                const buildingsData = await buildingsRes.json();
                console.log('Buildings received:', buildingsData?.features?.length || 0);
                
                if (buildingsData && buildingsData.features && buildingsData.features.length > 0) {
                    console.log('First building:', buildingsData.features[0]);
                    const buildingsLayer = L.geoJSON(buildingsData, {
                        style: {
                            fillColor: '#cccccc',
                            fillOpacity: 0.4,
                            color: '#888888',
                            weight: 0.5,
                            zIndex: 1
                        }
                    });
                    buildingsLayer.addTo(layers.buildings);
                    console.log('Buildings added to map');
                } else {
                    console.warn('No building features received!');
                }
                
                // Load walk network
                console.log('Loading walk network...');
                const networkRes = await fetch('/api/walk_network');
                const networkData = await networkRes.json();
                console.log('Walk network received:', networkData?.features?.length || 0);
                
                if (networkData && networkData.features && networkData.features.length > 0) {
                    console.log('First walk edge:', networkData.features[0]);
                    const walkLayer = L.geoJSON(networkData, {
                        style: { 
                            color: '#0066ff', 
                            weight: 4, 
                            opacity: 0.9,
                            zIndex: 1000
                        }
                    });
                    walkLayer.addTo(layers.network);
                    console.log('Walk network added to map');
                    console.log('Walk network bounds:', walkLayer.getBounds());
                } else {
                    console.warn('No walk network features received!');
                }
                
                // Load roads if available
                console.log('Loading roads...');
                const roadsRes = await fetch('/api/roads');
                const roadsData = await roadsRes.json();
                console.log('Roads received:', roadsData?.features?.length || 0);
                
                if (roadsData && roadsData.features && roadsData.features.length > 0) {
                    console.log('First road:', roadsData.features[0]);
                    const roadsLayer = L.geoJSON(roadsData, {
                        style: { 
                            color: '#ff3333', 
                            weight: 3, 
                            opacity: 0.8,
                            zIndex: 999
                        }
                    });
                    roadsLayer.addTo(layers.roads);
                    console.log('Roads added to map');
                    console.log('Roads bounds:', roadsLayer.getBounds());
                } else {
                    console.log('No roads in database');
                }
                
                // Load amenities
                console.log('Loading amenities...');
                const amenitiesRes = await fetch('/api/amenities');
                const amenitiesData = await amenitiesRes.json();
                console.log('Amenities received:', amenitiesData?.features?.length || 0);
                
                if (amenitiesData && amenitiesData.features && amenitiesData.features.length > 0) {
                    console.log('First amenity:', amenitiesData.features[0]);
                    L.geoJSON(amenitiesData, {
                        pointToLayer: (feature, latlng) => {
                            return L.circleMarker(latlng, {
                                radius: 4,
                                fillColor: '#4CAF50',
                                color: '#2E7D32',
                                weight: 1,
                                fillOpacity: 0.8
                            });
                        },
                        onEachFeature: (feature, layer) => {
                            layer.bindPopup('<strong>' + feature.properties.name + '</strong><br>' + feature.properties.amenity);
                        }
                    }).addTo(layers.amenities);
                    console.log('Amenities added to map');
                } else {
                    console.warn('No amenities received!');
                }
                
                // Load agents
                console.log('Loading agents...');
                await loadAgents();
                
                document.getElementById('loading').style.display = 'none';
                console.log('All data loaded successfully!');
                
            } catch (error) {
                console.error('Error loading data:', error);
                document.getElementById('loading').innerHTML = 'Error: ' + error.message;
            }
        }
        
        async function loadAgents() {
            console.log('Fetching agents...');
            const response = await fetch('/api/agents');
            const agentsData = await response.json();
            console.log('Agents received:', agentsData?.features?.length || 0);
            
            // Clear existing agents
            layers.agents.clearLayers();
            
            if (agentsData && agentsData.features && agentsData.features.length > 0) {
                console.log('First agent:', agentsData.features[0]);
                L.geoJSON(agentsData, {
                    pointToLayer: (feature, latlng) => {
                        return L.circleMarker(latlng, {
                            radius: 8,
                            fillColor: '#ff7800',
                            color: '#000000',
                            weight: 2,
                            fillOpacity: 1,
                            zIndex: 2000
                        });
                    },
                    onEachFeature: (feature, layer) => {
                        layer.on('click', async () => {
                            await showAgentInfo(feature.properties.id);
                        });
                    }
                }).addTo(layers.agents);
                console.log('Agents added to map');
            }
        }
        
        async function showAgentInfo(agentId) {
            console.log('Fetching info for agent:', agentId);
            const response = await fetch('/api/agent/' + agentId);
            const data = await response.json();
            
            document.getElementById('agent-info').style.display = 'block';
            document.getElementById('agent-id').innerText = data.id;
            document.getElementById('agent-type').innerText = data.type;
            document.getElementById('agent-location').innerText = 
                data.location.lon.toFixed(5) + ', ' + data.location.lat.toFixed(5);
            
            const nearbyDiv = document.getElementById('agent-nearby');
            if (data.nearby_amenities && data.nearby_amenities.length > 0) {
                let html = '<strong>What I see:</strong><ul style="margin:5px 0; padding-left:20px;">';
                data.nearby_amenities.forEach(item => {
                    html += '<li><strong>' + item.name + '</strong> (' + item.type + ')<br>';
                    html += 'Distance: ' + item.dist.toFixed(1) + 'm</li>';
                });
                html += '</ul>';
                nearbyDiv.innerHTML = html;
            } else {
                nearbyDiv.innerHTML = '<p style="margin:5px 0;"><em>I see nothing nearby</em></p>';
            }
        }
        
        // Layer toggles
        document.getElementById('layer-buildings').addEventListener('change', (e) => {
            if (e.target.checked) map.addLayer(layers.buildings);
            else map.removeLayer(layers.buildings);
        });
        
        document.getElementById('layer-network').addEventListener('change', (e) => {
            if (e.target.checked) map.addLayer(layers.network);
            else map.removeLayer(layers.network);
        });
        
        document.getElementById('layer-roads').addEventListener('change', (e) => {
            if (e.target.checked) map.addLayer(layers.roads);
            else map.removeLayer(layers.roads);
        });
        
        document.getElementById('layer-amenities').addEventListener('change', (e) => {
            if (e.target.checked) map.addLayer(layers.amenities);
            else map.removeLayer(layers.amenities);
        });
        
        document.getElementById('layer-agents').addEventListener('change', (e) => {
            if (e.target.checked) map.addLayer(layers.agents);
            else map.removeLayer(layers.agents);
        });
        
        loadAll();
    </script>
</body>
</html>"""

os.makedirs('templates', exist_ok=True)
with open('templates/map.html', 'w', encoding='utf-8') as f:
    f.write(html_content)
    
print("Created templates/map.html with debugging")
