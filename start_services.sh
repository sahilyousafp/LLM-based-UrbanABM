#!/bin/bash

# Startup script for LLM-based UrbanABM services
# This script starts both the OSM Update Service and the Map Server

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}LLM-based UrbanABM Service Launcher${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$SCRIPT_DIR"

# Check if virtual environment exists
if [ ! -d "$PROJECT_ROOT/venv" ]; then
    echo -e "${RED}Warning: No virtual environment found at $PROJECT_ROOT/venv${NC}"
    echo "Consider creating one with: python -m venv venv"
    echo ""
fi

# Function to start a service
start_service() {
    local service_name=$1
    local service_dir=$2
    local service_script=$3
    local port=$4
    
    echo -e "${GREEN}Starting $service_name on port $port...${NC}"
    cd "$PROJECT_ROOT/$service_dir"
    
    # Start the service in background
    nohup python "$service_script" > "$PROJECT_ROOT/${service_name}.log" 2>&1 &
    local pid=$!
    
    echo "$pid" > "$PROJECT_ROOT/${service_name}.pid"
    echo -e "${GREEN}✓ $service_name started (PID: $pid)${NC}"
    echo "  Log: $PROJECT_ROOT/${service_name}.log"
    echo ""
}

# Function to check if a port is in use
check_port() {
    local port=$1
    if lsof -Pi :$port -sTCP:LISTEN -t >/dev/null 2>&1 ; then
        return 0  # Port is in use
    else
        return 1  # Port is free
    fi
}

# Check for existing processes
if check_port 8001; then
    echo -e "${RED}Warning: Port 8001 is already in use${NC}"
    echo "OSM Update Service may already be running"
    echo ""
fi

if check_port 8000; then
    echo -e "${RED}Warning: Port 8000 is already in use${NC}"
    echo "Map Server may already be running"
    echo ""
fi

# Start services
echo -e "${BLUE}Starting services...${NC}"
echo ""

start_service "osm-update-service" "Environment/OSM" "osm_update_service.py" "8001"
start_service "map-server" "AGENT/OSM" "map_server.py" "8000"

echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}All services started successfully!${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo "Services:"
echo "  • OSM Update Service: http://localhost:8001"
echo "  • Map Server:         http://localhost:8000"
echo ""
echo "API Documentation:"
echo "  • OSM Service Docs:   http://localhost:8001/docs"
echo "  • Map Server Docs:    http://localhost:8000/docs"
echo ""
echo "To stop services, run: ./stop_services.sh"
echo "Or manually kill processes:"
echo "  kill \$(cat osm-update-service.pid)"
echo "  kill \$(cat map-server.pid)"
echo ""
