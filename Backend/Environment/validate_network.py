"""
Validate Walk Network Connectivity

Standalone script to analyze and report on the walk network connectivity
in eixample_overture.duckdb.

Usage:
    python validate_network.py
"""

import duckdb
import networkx as nx
from shapely import wkt
from pathlib import Path

DB_DIR = Path(__file__).parent
DB_PATH = DB_DIR / "eixample_overture.duckdb"


def validate_network():
    """Validate walk network connectivity and print report."""
    print("=" * 70)
    print("WALK NETWORK VALIDATION REPORT")
    print("=" * 70)
    print(f"Database: {DB_PATH}")
    
    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}")
        return
    
    con = duckdb.connect(str(DB_PATH), read_only=True)
    con.install_extension("spatial")
    con.load_extension("spatial")
    
    # Basic stats
    print("\n--- Basic Statistics ---")
    walk_count = con.execute("SELECT COUNT(*) FROM walk_edges").fetchone()[0]
    print(f"  Walk edges: {walk_count}")
    
    # Check for bridge edges
    try:
        bridge_count = con.execute("SELECT COUNT(*) FROM walk_edges WHERE is_bridge = TRUE").fetchone()[0]
        print(f"  Bridge edges: {bridge_count}")
    except:
        print(f"  Bridge edges: N/A (column not found)")
    
    # Road class distribution
    print("\n--- Road Class Distribution ---")
    rows = con.execute("""
        SELECT road_class, COUNT(*) as cnt 
        FROM walk_edges 
        GROUP BY road_class 
        ORDER BY cnt DESC
    """).fetchall()
    for road_class, cnt in rows:
        print(f"  {road_class or 'unknown'}: {cnt}")
    
    # Build graph
    print("\n--- Building Network Graph ---")
    edges_df = con.execute("""
        SELECT id, ST_AsText(geometry) as wkt, is_bridge
        FROM walk_edges
    """).fetchdf()
    
    G = nx.Graph()
    node_map = {}
    edge_types = {'bridge': 0, 'normal': 0}
    
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
            
            is_bridge = row.get('is_bridge', False)
            edge_type = 'bridge' if is_bridge else 'normal'
            edge_types[edge_type] += 1
            
            G.add_edge(node_map[start], node_map[end], 
                       geometry=geom, 
                       edge_id=row['id'],
                       is_bridge=is_bridge)
        except:
            continue
    
    print(f"  Nodes: {G.number_of_nodes()}")
    print(f"  Edges: {G.number_of_edges()}")
    print(f"    Normal edges: {edge_types['normal']}")
    print(f"    Bridge edges: {edge_types['bridge']}")
    
    # Component analysis
    print("\n--- Connected Components ---")
    components = list(nx.connected_components(G))
    print(f"  Total components: {len(components)}")
    
    for i, comp in enumerate(sorted(components, key=len, reverse=True)):
        pct = len(comp) / G.number_of_nodes() * 100
        if pct < 0.1:
            print(f"    ... and {len(components) - i} more tiny components")
            break
        print(f"    Component {i+1}: {len(comp)} nodes ({pct:.1f}%)")
        
        # Calculate component diameter (longest shortest path)
        if len(comp) < 500:
            subgraph = G.subgraph(comp)
            try:
                diameter = nx.diameter(subgraph)
                print(f"      Diameter: {diameter} edges")
            except:
                print(f"      Diameter: N/A (disconnected)")
    
    # Connectivity assessment
    print("\n--- Connectivity Assessment ---")
    largest = max(components, key=len)
    largest_pct = len(largest) / G.number_of_nodes() * 100
    
    if len(components) == 1:
        print("  [OK][OK][OK] EXCELLENT: Network is FULLY CONNECTED")
        print("      All nodes can reach all other nodes")
    elif largest_pct >= 95:
        print(f"  [OK] GOOD: Largest component covers {largest_pct:.1f}% of nodes")
        print(f"      {len(components) - 1} tiny isolated components remain")
    elif largest_pct >= 80:
        print(f"  [WARN] ACCEPTABLE: Largest component covers {largest_pct:.1f}% of nodes")
        print(f"      Some areas may be unreachable")
    else:
        print(f"  [FAIL] POOR: Largest component covers only {largest_pct:.1f}% of nodes")
        print(f"      Network is severely fragmented")
        print(f"      Run network_connector.py to fix connectivity")
    
    # Sample reachability test
    print("\n--- Sample Reachability Test ---")
    if len(components) > 1:
        # Pick two nodes from different components
        comp_0 = list(components[0])
        comp_1 = list(components[1]) if len(components) > 1 else comp_0
        
        id_to_coord = {v: k for k, v in node_map.items()}
        node_0 = id_to_coord[comp_0[0]]
        node_1 = id_to_coord[comp_1[0]]
        
        print(f"  Node A: {node_0} (component 1)")
        print(f"  Node B: {node_1} (component 2)")
        
        if components[0] != components[1]:
            print(f"  Path exists: NO (different components)")
        else:
            try:
                path = nx.shortest_path(G, comp_0[0], comp_1[0])
                print(f"  Path exists: YES ({len(path)} nodes)")
            except:
                print(f"  Path exists: NO")
    
    # Final recommendation
    print("\n--- Recommendation ---")
    if len(components) == 1 or largest_pct >= 95:
        print("  Network is ready for simulation")
    else:
        print("  Run: python network_connector.py")
        print("  This will merge pedestrian roads and bridge gaps")
    
    print("\n" + "=" * 70)
    
    con.close()


if __name__ == "__main__":
    validate_network()
