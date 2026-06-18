"""
Backwards-compatibility shim.

All logic has moved to the network/ package.
Import directly from there for new code:

    from network import main
    from network.connector import merge_pedestrian_roads, bridge_gaps, insert_bridges
    from network.validator import build_graph_from_edges, validate_final_network
"""

from network import main
from network.connector import (
    merge_pedestrian_roads,
    bridge_gaps,
    insert_bridges,
    backup_database,
    PEDESTRIAN_ACCESSIBLE_TYPES,
    GAP_THRESHOLD_METERS,
    DB_PATH,
    meters_to_degrees,
    distance_degrees_to_meters,
)
from network.validator import (
    build_graph_from_edges,
    analyze_components,
    validate_final_network,
)

__all__ = [
    "main",
    "merge_pedestrian_roads", "bridge_gaps", "insert_bridges", "backup_database",
    "build_graph_from_edges", "analyze_components", "validate_final_network",
    "PEDESTRIAN_ACCESSIBLE_TYPES", "GAP_THRESHOLD_METERS", "DB_PATH",
    "meters_to_degrees", "distance_degrees_to_meters",
]

if __name__ == "__main__":
    main()
