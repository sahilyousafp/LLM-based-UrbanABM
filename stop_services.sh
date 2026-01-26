#!/bin/bash

# Stop script for LLM-based UrbanABM services

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Stopping LLM-based UrbanABM Services${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$SCRIPT_DIR"

# Function to stop a service
stop_service() {
    local service_name=$1
    local pid_file="$PROJECT_ROOT/${service_name}.pid"
    
    if [ -f "$pid_file" ]; then
        local pid=$(cat "$pid_file")
        if ps -p $pid > /dev/null 2>&1; then
            echo -e "${BLUE}Stopping $service_name (PID: $pid)...${NC}"
            kill $pid
            sleep 2
            
            # Force kill if still running
            if ps -p $pid > /dev/null 2>&1; then
                echo -e "${RED}Force killing $service_name...${NC}"
                kill -9 $pid
            fi
            
            echo -e "${GREEN}✓ $service_name stopped${NC}"
        else
            echo -e "${RED}$service_name (PID: $pid) is not running${NC}"
        fi
        rm "$pid_file"
    else
        echo -e "${RED}No PID file found for $service_name${NC}"
    fi
}

# Stop services
stop_service "osm-update-service"
stop_service "map-server"

echo ""
echo -e "${GREEN}All services stopped${NC}"
echo ""
