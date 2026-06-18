"""
Walk network graph building and validation.

Provides functions to construct a NetworkX graph from DuckDB walk_edges,
analyse its connected components, and validate final connectivity.
"""

import duckdb
import networkx as nx
from shapely import wkt


def build_graph_from_edges(con: duckdb.DuckDBPyConnection):
    """
    Build a NetworkX graph from the walk_edges table.

    Returns
    -------
    G : nx.Graph
    node_map : dict[(lon, lat) -> node_id]
    id_to_coord : dict[node_id -> (lon, lat)]
    edge_geoms : dict[edge_id -> Shapely LineString]
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
    node_map: dict = {}
    id_to_coord: dict = {}
    edge_geoms: dict = {}

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

            G.add_edge(node_map[start], node_map[end],
                       geometry=geom,
                       edge_id=row['id'],
                       road_class=row['road_class'])
            edge_geoms[row['id']] = geom
        except Exception:
            continue

    print(f"  Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    return G, node_map, id_to_coord, edge_geoms


def analyze_components(G: nx.Graph, id_to_coord: dict) -> list:
    """
    Report connected components and return them sorted by size (largest first).
    """
    components = list(nx.connected_components(G))
    print(f"\n  Connected components: {len(components)}")

    for i, comp in enumerate(sorted(components, key=len, reverse=True)[:10]):
        pct = len(comp) / G.number_of_nodes() * 100
        print(f"    Component {i+1}: {len(comp)} nodes ({pct:.1f}%)")

    if len(components) > 10:
        print(f"    ... and {len(components) - 10} more smaller components")

    return components


def validate_final_network(con: duckdb.DuckDBPyConnection) -> bool:
    """
    Build a fresh graph from walk_edges and report connectivity stats.

    Returns True when the network is acceptably connected (≤5 components).
    """
    print("\n" + "=" * 60)
    print("PHASE 4: Validating final network")
    print("=" * 60)

    edges_df = con.execute("""
        SELECT id, ST_AsText(geometry) as wkt, is_bridge
        FROM walk_edges
    """).fetchdf()

    G = nx.Graph()
    node_map: dict = {}

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
        except Exception:
            continue

    components = list(nx.connected_components(G))
    bridge_count = edges_df['is_bridge'].sum() if 'is_bridge' in edges_df.columns else 0

    print(f"\n  Total edges: {len(edges_df)}")
    print(f"  Total nodes: {G.number_of_nodes()}")
    print(f"  Connected components: {len(components)}")
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
