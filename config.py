"""
Configuration file for LLM-based UrbanABM project.
Manages paths and settings across different modules.
"""

import os
from pathlib import Path

# Get the project root directory
PROJECT_ROOT = Path(__file__).parent.absolute()

# Environment/OSM paths
ENVIRONMENT_OSM_DIR = PROJECT_ROOT / "Environment" / "OSM"
DEFAULT_DB_NAME = "eixample_osm.duckdb"
DB_PATH = ENVIRONMENT_OSM_DIR / DEFAULT_DB_NAME

# AGENT/OSM paths
AGENT_OSM_DIR = PROJECT_ROOT / "AGENT" / "OSM"

# OSM Update Service Configuration
OSM_SERVICE_PORT = int(os.getenv("OSM_SERVICE_PORT", "8001"))
OSM_SERVICE_HOST = os.getenv("OSM_SERVICE_HOST", "127.0.0.1")

# Map Server Configuration
MAP_SERVER_PORT = int(os.getenv("MAP_SERVER_PORT", "8000"))
MAP_SERVER_HOST = os.getenv("MAP_SERVER_HOST", "127.0.0.1")

# Default place for OSM data
DEFAULT_PLACE_NAME = "Eixample, Barcelona, Spain"


def get_db_path():
    """
    Get the DuckDB database path.
    Can be overridden with DB_PATH environment variable.
    """
    custom_path = os.getenv("DB_PATH")
    if custom_path:
        return Path(custom_path)
    return DB_PATH


def get_osm_service_url():
    """Get the OSM update service URL."""
    return f"http://{OSM_SERVICE_HOST}:{OSM_SERVICE_PORT}"


def get_map_server_url():
    """Get the map server URL."""
    return f"http://{MAP_SERVER_HOST}:{MAP_SERVER_PORT}"


# Ensure directories exist
ENVIRONMENT_OSM_DIR.mkdir(parents=True, exist_ok=True)
AGENT_OSM_DIR.mkdir(parents=True, exist_ok=True)
