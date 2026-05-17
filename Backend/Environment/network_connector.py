"""
Network Connector - Merge pedestrian-accessible roads and bridge gaps.

This script fixes the fragmented walk network by:
1. Merging pedestrian-accessible road types (residential, service, living_street, etc.) into walk_edges
2. Finding disconnected components and adding bridge edges for small gaps (< 5m)
3. Validating the resulting network connectivity

Run this script to regenerate walk_edges with full connectivity:
    python network_connector.py
"""

import duckdb
import networkx as nx
from shapely.geometry import LineString, Point
from shapely import wkt
from pathlib import Path
from collections import defaultdict
import uuid

DB_DIR = Path(__file__).parent
DB_PATH = DB_DIR / "eixample_overture.duckdb"

# Road types that pedestrians can reasonably walk on
PEDESTRIAN_ACCESSIBLE_TYPES = {
    'residential',
    'service',
    'living_street',
    'tertiary',
    'unclassified',
    'footway',
    'pedestrian',
    'cycleway',
    'path',
    'steps',
    'track',
}

# Maximum gap distance to bridge (meters) - increased to connect major components
GAP_THRESHOLD_METERS = 50.0

# Convert meters to degrees (approximate at Barcelona latitude ~41.4°)
# 1 degree latitude ≈ 111,000 m
# 1 degree longitude ≈ 111,000 * cos(41.4°) ≈ 83,500 m
DEG_TO_M_LAT = 111000.0
DEG_TO_M_LON = 83500.0


def meters_to_degrees(meters):
    """Convert meters to approximate degree distance at Barcelona latitude."""
    return meters / DEG_TO_M_LAT


def distance_degrees_to_meters(dx, dy):
    """Convert degree differences to approximate meters."""
    return ((dx * DEG_TO_M_LON) ** 2 + (dy * DEG_TO_M_LAT) ** 2) ** 0.5


def backup_database():
    """Create a backup of the current database."""
    backup_path = DB_PATH.with_suffix('.backup.duckdb')
    if backup_path.exists():
        print(f"  Backup already exists: {backup_path}")
        return
    print(f"  Creating backup: {backup_path}")
    import shutil
    shutil.copy2(DB_PATH, backup_path)
    print(f"  [OK] Backup created")


def merge_pedestrian_roads(con):
    """
    Merge pedestrian-accessible roads into walk_edges.
    
    Returns the number of new edges added.
    """
    print("\n" + "=" * 60)
    print("PHASE 1: Merging pedestrian-accessible roads into walk_edges")
    print("=" * 60)
    
    # Get current walk_edges count
    walk_count = con.execute("SELECT COUNT(*) FROM walk_edges").fetchone()[0]
    print(f"Current walk_edges: {walk_count}")
    
    # Get existing edge IDs to avoid duplicates
    existing_ids = set(row[0] for row in con.execute("SELECT id FROM walk_edges").fetchall())
    
    # Find roads with pedestrian-accessible types
    road_types_str = ", ".join(f"'{t}'" for t in PEDESTRIAN_ACCESSIBLE_TYPES)
    
    # First, check what's in roads table
    road_count = con.execute("SELECT COUNT(*) FROM roads").fetchone()[0]
    print(f"Total roads in database: {road_count}")
    
    # Query roads with WKT conversion for proper geometry handling
    query = f"""
        SELECT id, ST_AsText(geometry) as wkt, road_type, road_class, name, level, width, surface,
               ST_Length(geometry) as length
        FROM roads
        WHERE road_class IN ({road_types_str})
    """
    
    roads_to_add = con.execute(query).fetchall()
    print(f"Found {len(roads_to_add)} pedestrian-accessible roads to merge")
    
    if not roads_to_add:
        print("  No new roads to add")
        return 0
    
    # Insert into walk_edges using WKT
    added = 0
    skipped = 0
    for row in roads_to_add:
        edge_id, wkt_str, road_type, road_class, name, level, width, surface, length = row
        
        # Skip if already exists
        if edge_id in existing_ids:
            skipped += 1
            continue
        
        try:
            con.execute("""
                INSERT INTO walk_edges (id, geometry, road_type, road_class, name, level, is_bridge, is_tunnel, width, surface, length)
                VALUES (?, ST_GeomFromText(?), ?, ?, ?, ?, FALSE, FALSE, ?, ?, ?)
            """, [edge_id, wkt_str, road_type, road_class, name, level, width, surface, length])
            added += 1
            existing_ids.add(edge_id)
        except Exception as e:
            print(f"  Warning: Failed to add edge {edge_id}: {e}")
    
    new_count = con.execute("SELECT COUNT(*) FROM walk_edges").fetchone()[0]
    print(f"  [OK] Added {added} road edges to walk_edges (skipped {skipped} duplicates)")
    print(f"  Total walk_edges now: {new_count}")
    
    return added


def build_graph_from_edges(con):
    """
    Build a NetworkX graph from walk_edges.
    
    Returns:
        G: NetworkX graph
        node_map: dict mapping (lon, lat) tuples to node IDs
        id_to_coord: dict mapping node IDs to (lon, lat) tuples
        edge_geoms: dict mapping edge IDs to geometries
    """
    print("\n" + "=" * 60)
    print("PHASE 2: Building network graph")
    print("=" * 60)
    
    edges_df = con.execute("""
        SELECT id, ST_AsText(geometry) as wkt, road_class
        FROM walk_edges
    """).fetchdf()
    
    print(f"  Loaded {len(edges_df)} edges")
    
    G = nx.Graph()
    node_map = {}
    id_to_coord = {}
    edge_geoms = {}
    
    for _, row in edges_df.iterrows():
        try:
            geom = wkt.loads(row['wkt'])
            if geom.geom_type != 'LineString' or len(geom.coords) < 2:
                continue
            
            start = (round(geom.coords[0][0], 6), round(geom.coords[0][1], 6))
            end = (round(geom.coords[-1][0], 6), round(geom.coords[-1][1], 6))
            
            if start not in node_map:
                node_map[start] = len(node_map)
                id_to_coord[node_map[start]] = start
            if end not in node_map:
                node_map[end] = len(node_map)
                id_to_coord[node_map[end]] = end
            
            start_id = node_map[start]
            end_id = node_map[end]
            
            G.add_edge(start_id, end_id, 
                       geometry=geom, 
                       edge_id=row['id'],
                       road_class=row['road_class'])
            
            edge_geoms[row['id']] = geom
        except Exception as e:
            continue
    
    print(f"  Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    
    return G, node_map, id_to_coord, edge_geoms


def analyze_components(G, id_to_coord):
    """
    Analyze connected components in the graph.
    
    Returns list of components (sets of node IDs).
    """
    components = list(nx.connected_components(G))
    
    print(f"\n  Connected components: {len(components)}")
    
    for i, comp in enumerate(sorted(components, key=len, reverse=True)[:10]):
        pct = len(comp) / G.number_of_nodes() * 100
        print(f"    Component {i+1}: {len(comp)} nodes ({pct:.1f}%)")
    
    if len(components) > 10:
        print(f"    ... and {len(components) - 10} more smaller components")
    
    return components


def bridge_gaps(G, components, id_to_coord, gap_threshold_meters=GAP_THRESHOLD_METERS):
    """
    Find and bridge gaps between disconnected components.
    
    Returns list of (coord_a, coord_b, distance_m) for bridges added.
    """
    print(f"\n" + "=" * 60)
    print(f"PHASE 3: Bridging gaps (threshold: {gap_threshold_meters}m)")
    print("=" * 60)
    
    if len(components) <= 1:
        print("  Network is already fully connected!")
        return []
    
    bridges = []
    
    # Sort components by size (largest first)
    sorted_components = sorted(components, key=len, reverse=True)
    main_component = sorted_components[0]
    other_components = sorted_components[1:]
    
    print(f"  Main component: {len(main_component)} nodes")
    print(f"  Other components: {len(other_components)}")
    
    # Strategy 1: Connect each component to the main component
    print(f"\n  Strategy 1: Connecting components to main component...")
    connected = set()
    
    for i, comp in enumerate(other_components):
        if len(comp) == 0:
            continue
            
        # Find closest pair between this component and main component
        min_dist = float('inf')
        best_pair = None
        
        # Sample if components are large (for performance)
        nodes_main = list(main_component) if len(main_component) < 200 else list(main_component)[:200]
        nodes_comp = list(comp) if len(comp) < 100 else list(comp)[:100]
        
        for node_c in nodes_comp:
            coord_c = id_to_coord[node_c]
            for node_m in nodes_main:
                coord_m = id_to_coord[node_m]
                dx = coord_c[0] - coord_m[0]
                dy = coord_c[1] - coord_m[1]
                dist = distance_degrees_to_meters(dx, dy)
                
                if dist < min_dist:
                    min_dist = dist
                    best_pair = (coord_c, coord_m)
        
        # Add bridge if within threshold
        if min_dist <= gap_threshold_meters and best_pair:
            bridges.append((best_pair[0], best_pair[1], min_dist))
            connected.add(i)
    
    print(f"  Connected {len(connected)} components to main component")
    
    # Strategy 2: Bridge remaining components to any already-connected component
    remaining = [i for i in range(len(other_components)) if i not in connected]
    print(f"\n  Strategy 2: Bridging {len(remaining)} remaining components...")
    
    # Build set of all nodes in connected components
    connected_nodes = set(main_component)
    for i in connected:
        connected_nodes.update(other_components[i])
    
    bridged_remaining = 0
    for i in remaining:
        comp = other_components[i]
        if len(comp) == 0:
            continue
            
        min_dist = float('inf')
        best_pair = None
        
        nodes_comp = list(comp) if len(comp) < 100 else list(comp)[:100]
        nodes_connected = list(connected_nodes) if len(connected_nodes) < 500 else list(connected_nodes)[:500]
        
        for node_c in nodes_comp:
            coord_c = id_to_coord[node_c]
            for node_conn in nodes_connected:
                coord_conn = id_to_coord[node_conn]
                dx = coord_c[0] - coord_conn[0]
                dy = coord_c[1] - coord_conn[1]
                dist = distance_degrees_to_meters(dx, dy)
                
                if dist < min_dist:
                    min_dist = dist
                    best_pair = (coord_c, coord_conn)
        
        if min_dist <= gap_threshold_meters and best_pair:
            bridges.append((best_pair[0], best_pair[1], min_dist))
            connected_nodes.update(comp)
            bridged_remaining += 1
    
    print(f"  Bridged {bridged_remaining} additional components")
    
    print(f"\n  Total bridges to add: {len(bridges)}")
    
    return bridges


def insert_bridges(con, bridges):
    """
    Insert bridge edges into walk_edges table.
    
    Returns number of bridges added.
    """
    if not bridges:
        print("  No bridges to add")
        return 0
    
    print(f"\n  Inserting {len(bridges)} bridge edges...")
    
    added = 0
    for coord_a, coord_b, dist_m in bridges:
        bridge_id = f"bridge_{uuid.uuid4().hex[:8]}"
        bridge_geom = LineString([coord_a, coord_b])
        
        try:
            con.execute("""
                INSERT INTO walk_edges (id, geometry, road_type, road_class, name, level, is_bridge, is_tunnel, width, surface, length)
                VALUES (?, ST_GeomFromText(?), 'bridge', 'connector', 'synthetic_bridge', 0, TRUE, FALSE, NULL, NULL, ?)
            """, [bridge_id, bridge_geom.wkt, bridge_geom.length])
            added += 1
        except Exception as e:
            print(f"  Warning: Failed to add bridge {bridge_id}: {e}")
    
    print(f"  [OK] Added {added} bridge edges")
    return added


def validate_final_network(con):
    """
    Validate the final walk network connectivity.
    """
    print("\n" + "=" * 60)
    print("PHASE 4: Validating final network")
    print("=" * 60)
    
    edges_df = con.execute("""
        SELECT id, ST_AsText(geometry) as wkt, is_bridge
        FROM walk_edges
    """).fetchdf()
    
    G = nx.Graph()
    node_map = {}
    
    for _, row in edges_df.iterrows():
        try:
            geom = wkt.loads(row['wkt'])
            if geom.geom_type != 'LineString' or len(geom.coords) < 2:
                continue
            
            start = (round(geom.coords[0][0], 6), round(geom.coords[0][1], 6))
            end = (round(geom.coords[-1][0], 6), round(geom.coords[-1][1], 6))
            
            if start not in node_map:
                node_map[start] = len(node_map)
            if end not in node_map:
                node_map[end] = len(node_map)
            
            G.add_edge(node_map[start], node_map[end])
        except:
            continue
    
    components = list(nx.connected_components(G))
    
    print(f"\n  Total edges: {len(edges_df)}")
    print(f"  Total nodes: {G.number_of_nodes()}")
    print(f"  Connected components: {len(components)}")
    
    bridge_count = edges_df['is_bridge'].sum() if 'is_bridge' in edges_df.columns else 0
    print(f"  Bridge edges: {int(bridge_count)}")
    
    for i, comp in enumerate(sorted(components, key=len, reverse=True)[:5]):
        pct = len(comp) / G.number_of_nodes() * 100
        print(f"    Component {i+1}: {len(comp)} nodes ({pct:.1f}%)")
    
    if len(components) == 1:
        print("\n  [OK][OK][OK] Network is FULLY CONNECTED [OK][OK][OK]")
        return True
    elif len(components) <= 5:
        largest = max(components, key=len)
        largest_pct = len(largest) / G.number_of_nodes() * 100
        print(f"\n  [WARN] Network has {len(components)} components")
        print(f"    Largest component: {largest_pct:.1f}% of nodes")
        print(f"    This should be sufficient for most pathfinding")
        return True
    else:
        print(f"\n  [FAIL] Network still has {len(components)} components")
        print(f"    Consider increasing gap threshold or investigating data quality")
        return False


def main():
    """Main pipeline to fix walk network connectivity."""
    print("=" * 60)
    print("NETWORK CONNECTOR - Fixing Walk Network Fragmentation")
    print("=" * 60)
    print(f"Database: {DB_PATH}")
    print(f"Gap threshold: {GAP_THRESHOLD_METERS}m")
    print("=" * 60)
    
    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}")
        return
    
    # Step 0: Backup (before opening connection)
    print("\nStep 0: Creating backup...")
    backup_database()
    
    # Connect to database
    con = duckdb.connect(str(DB_PATH))
    con.install_extension("spatial")
    con.load_extension("spatial")
    print("[OK] Database connected")
    
    # Step 1: Merge pedestrian roads
    roads_added = merge_pedestrian_roads(con)
    
    # Step 2: Build graph
    G, node_map, id_to_coord, edge_geoms = build_graph_from_edges(con)
    
    # Step 3: Analyze components
    components = analyze_components(G, id_to_coord)
    
    # Step 4: Bridge gaps (iterative)
    max_iterations = 3
    total_bridges = 0
    
    for iteration in range(max_iterations):
        print(f"\n{'=' * 60}")
        print(f"BRIDGE ITERATION {iteration + 1}/{max_iterations}")
        print(f"{'=' * 60}")
        
        # Rebuild graph to reflect current state
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
    
    # Step 5: Validate
    success = validate_final_network(con)
    
    # Summary
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


if __name__ == "__main__":
    main()
