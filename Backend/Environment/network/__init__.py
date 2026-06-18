from .connector import (
    merge_pedestrian_roads,
    bridge_gaps,
    insert_bridges,
    backup_database,
    PEDESTRIAN_ACCESSIBLE_TYPES,
    GAP_THRESHOLD_METERS,
    DB_PATH,
)
from .validator import build_graph_from_edges, analyze_components, validate_final_network

__all__ = [
    "merge_pedestrian_roads", "bridge_gaps", "insert_bridges", "backup_database",
    "build_graph_from_edges", "analyze_components", "validate_final_network",
    "PEDESTRIAN_ACCESSIBLE_TYPES", "GAP_THRESHOLD_METERS", "DB_PATH",
    "main",
]


def main():
    """Full pipeline: backup → merge roads → bridge gaps → validate."""
    import duckdb

    print("=" * 60)
    print("NETWORK CONNECTOR - Fixing Walk Network Fragmentation")
    print("=" * 60)
    print(f"Database: {DB_PATH}")
    print(f"Gap threshold: {GAP_THRESHOLD_METERS}m")
    print("=" * 60)

    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}")
        return

    print("\nStep 0: Creating backup...")
    backup_database()

    con = duckdb.connect(str(DB_PATH))
    con.install_extension("spatial")
    con.load_extension("spatial")
    print("[OK] Database connected")

    roads_added = merge_pedestrian_roads(con)

    G, node_map, id_to_coord, edge_geoms = build_graph_from_edges(con)
    components = analyze_components(G, id_to_coord)

    max_iterations = 3
    total_bridges = 0

    for iteration in range(max_iterations):
        print(f"\n{'=' * 60}")
        print(f"BRIDGE ITERATION {iteration + 1}/{max_iterations}")
        print(f"{'=' * 60}")

        G, node_map, id_to_coord, edge_geoms = build_graph_from_edges(con)
        components = analyze_components(G, id_to_coord)

        largest = max(components, key=len)
        largest_pct = len(largest) / G.number_of_nodes() * 100

        if largest_pct >= 90 or len(components) <= 10:
            print(f"\n  Network sufficiently connected ({largest_pct:.1f}% in main component)")
            break

        bridges = bridge_gaps(G, components, id_to_coord)
        bridges_added = insert_bridges(con, bridges)
        total_bridges += bridges_added

        if bridges_added == 0:
            print("  No more bridges to add")
            break

    success = validate_final_network(con)

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Roads merged: {roads_added}")
    print(f"  Total bridges added: {total_bridges}")
    print(f"  Network connectivity: {'[OK] GOOD' if success else '[FAIL] NEEDS WORK'}")
    print("=" * 60)

    if success:
        print("\n[OK] Network connector completed successfully!")
        print("  You can now run your simulation with full pathfinding.")
    else:
        print("\n[WARN] Network still has connectivity issues.")
        print("  Consider increasing GAP_THRESHOLD_METERS or investigating the data.")

    con.close()
