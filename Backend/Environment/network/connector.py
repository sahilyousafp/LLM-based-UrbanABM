"""
Walk network road merger and gap bridger.

Fixes pedestrian network fragmentation by:
1. Merging pedestrian-accessible road types into walk_edges
2. Finding disconnected components and adding synthetic bridge edges for small gaps
"""

import uuid
import shutil
from pathlib import Path

import duckdb
from shapely.geometry import LineString

DB_PATH = Path(__file__).parent.parent / "eixample_overture.duckdb"

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

# Maximum gap distance to bridge (meters)
GAP_THRESHOLD_METERS = 50.0

# Coordinate conversion factors at Barcelona latitude (~41.4°)
# 1 degree latitude  ≈ 111,000 m
# 1 degree longitude ≈ 111,000 × cos(41.4°) ≈ 83,500 m
DEG_TO_M_LAT = 111000.0
DEG_TO_M_LON = 83500.0


def meters_to_degrees(meters: float) -> float:
    """Convert meters to approximate degree distance at Barcelona latitude."""
    return meters / DEG_TO_M_LAT


def distance_degrees_to_meters(dx: float, dy: float) -> float:
    """Convert degree differences to approximate meters."""
    return ((dx * DEG_TO_M_LON) ** 2 + (dy * DEG_TO_M_LAT) ** 2) ** 0.5


def backup_database(db_path: Path = DB_PATH) -> None:
    """Create a .backup.duckdb copy before any modifications."""
    backup_path = db_path.with_suffix('.backup.duckdb')
    if backup_path.exists():
        print(f"  Backup already exists: {backup_path}")
        return
    print(f"  Creating backup: {backup_path}")
    shutil.copy2(db_path, backup_path)
    print(f"  [OK] Backup created")


def merge_pedestrian_roads(con: duckdb.DuckDBPyConnection) -> int:
    """
    Merge pedestrian-accessible roads from the `roads` table into `walk_edges`.

    Returns the number of new edges added.
    """
    print("\n" + "=" * 60)
    print("PHASE 1: Merging pedestrian-accessible roads into walk_edges")
    print("=" * 60)

    walk_count = con.execute("SELECT COUNT(*) FROM walk_edges").fetchone()[0]
    print(f"Current walk_edges: {walk_count}")

    existing_ids = set(row[0] for row in con.execute("SELECT id FROM walk_edges").fetchall())

    road_types_str = ", ".join(f"'{t}'" for t in PEDESTRIAN_ACCESSIBLE_TYPES)
    road_count = con.execute("SELECT COUNT(*) FROM roads").fetchone()[0]
    print(f"Total roads in database: {road_count}")

    roads_to_add = con.execute(f"""
        SELECT id, ST_AsText(geometry) as wkt, road_type, road_class, name,
               level, width, surface, ST_Length(geometry) as length
        FROM roads
        WHERE road_class IN ({road_types_str})
    """).fetchall()
    print(f"Found {len(roads_to_add)} pedestrian-accessible roads to merge")

    if not roads_to_add:
        print("  No new roads to add")
        return 0

    added = 0
    skipped = 0
    for row in roads_to_add:
        edge_id, wkt_str, road_type, road_class, name, level, width, surface, length = row
        if edge_id in existing_ids:
            skipped += 1
            continue
        try:
            con.execute("""
                INSERT INTO walk_edges
                    (id, geometry, road_type, road_class, name,
                     level, is_bridge, is_tunnel, width, surface, length)
                VALUES (?, ST_GeomFromText(?), ?, ?, ?, ?, FALSE, FALSE, ?, ?, ?)
            """, [edge_id, wkt_str, road_type, road_class, name,
                  level, width, surface, length])
            added += 1
            existing_ids.add(edge_id)
        except Exception as e:
            print(f"  Warning: Failed to add edge {edge_id}: {e}")

    new_count = con.execute("SELECT COUNT(*) FROM walk_edges").fetchone()[0]
    print(f"  [OK] Added {added} road edges (skipped {skipped} duplicates)")
    print(f"  Total walk_edges now: {new_count}")
    return added


def bridge_gaps(G, components: list, id_to_coord: dict,
                gap_threshold_meters: float = GAP_THRESHOLD_METERS) -> list:
    """
    Find pairs of nodes across disconnected components that are within
    gap_threshold_meters of each other.

    Returns list of (coord_a, coord_b, distance_m) tuples for bridges to add.
    """
    print(f"\n" + "=" * 60)
    print(f"PHASE 3: Bridging gaps (threshold: {gap_threshold_meters}m)")
    print("=" * 60)

    if len(components) <= 1:
        print("  Network is already fully connected!")
        return []

    bridges = []
    sorted_components = sorted(components, key=len, reverse=True)
    main_component = sorted_components[0]
    other_components = sorted_components[1:]

    print(f"  Main component: {len(main_component)} nodes")
    print(f"  Other components: {len(other_components)}")

    # Strategy 1: Connect each component directly to the main component
    print(f"\n  Strategy 1: Connecting components to main component...")
    connected = set()

    for i, comp in enumerate(other_components):
        if not comp:
            continue
        min_dist = float('inf')
        best_pair = None

        nodes_main = list(main_component)[:200]
        nodes_comp = list(comp)[:100]

        for node_c in nodes_comp:
            coord_c = id_to_coord[node_c]
            for node_m in nodes_main:
                coord_m = id_to_coord[node_m]
                dist = distance_degrees_to_meters(
                    coord_c[0] - coord_m[0], coord_c[1] - coord_m[1]
                )
                if dist < min_dist:
                    min_dist = dist
                    best_pair = (coord_c, coord_m)

        if min_dist <= gap_threshold_meters and best_pair:
            bridges.append((best_pair[0], best_pair[1], min_dist))
            connected.add(i)

    print(f"  Connected {len(connected)} components to main component")

    # Strategy 2: Bridge any remaining components to the now-connected set
    remaining = [i for i in range(len(other_components)) if i not in connected]
    print(f"\n  Strategy 2: Bridging {len(remaining)} remaining components...")

    connected_nodes = set(main_component)
    for i in connected:
        connected_nodes.update(other_components[i])

    bridged_remaining = 0
    for i in remaining:
        comp = other_components[i]
        if not comp:
            continue
        min_dist = float('inf')
        best_pair = None

        nodes_comp = list(comp)[:100]
        nodes_connected = list(connected_nodes)[:500]

        for node_c in nodes_comp:
            coord_c = id_to_coord[node_c]
            for node_conn in nodes_connected:
                coord_conn = id_to_coord[node_conn]
                dist = distance_degrees_to_meters(
                    coord_c[0] - coord_conn[0], coord_c[1] - coord_conn[1]
                )
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


def insert_bridges(con: duckdb.DuckDBPyConnection, bridges: list) -> int:
    """
    Insert synthetic bridge edges into the walk_edges table.

    Returns number of bridges successfully inserted.
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
                INSERT INTO walk_edges
                    (id, geometry, road_type, road_class, name,
                     level, is_bridge, is_tunnel, width, surface, length)
                VALUES (?, ST_GeomFromText(?), 'bridge', 'connector',
                        'synthetic_bridge', 0, TRUE, FALSE, NULL, NULL, ?)
            """, [bridge_id, bridge_geom.wkt, bridge_geom.length])
            added += 1
        except Exception as e:
            print(f"  Warning: Failed to add bridge {bridge_id}: {e}")

    print(f"  [OK] Added {added} bridge edges")
    return added
