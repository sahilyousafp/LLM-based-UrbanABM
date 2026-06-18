import logging

import numpy as np
import pyproj
from fastapi import APIRouter, Query
from shapely import wkt
from shapely.geometry import LineString

from db import get_db_connection

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/buildings")
async def get_buildings():
    con = get_db_connection()
    try:
        results = con.execute("SELECT ST_AsText(geometry) as wkt FROM buildings").fetchall()
        print(f"Retrieved {len(results)} buildings from database")
        features = []
        for idx, row in enumerate(results):
            try:
                geom = wkt.loads(row[0])
                if geom.geom_type == "Polygon":
                    coords = [[list(coord) for coord in geom.exterior.coords]]
                    features.append({
                        "type": "Feature",
                        "geometry": {"type": "Polygon", "coordinates": coords},
                        "properties": {"layer": "buildings"}
                    })
                    if idx == 0:
                        print(f"First building coords: {coords[0][0]}")
            except Exception as e:
                if idx < 5:
                    print(f"Error processing building {idx}: {e}")
                continue
        print(f"Returning {len(features)} building features")
        return {"type": "FeatureCollection", "features": features}
    finally:
        con.close()


@router.get("/api/walk_network")
async def get_walk_network():
    con = get_db_connection()
    try:
        results = con.execute("SELECT ST_AsText(geometry) as wkt FROM walk_edges").fetchall()
        print(f"Retrieved {len(results)} walk edges from database")
        features = []
        for idx, row in enumerate(results):
            try:
                geom = wkt.loads(row[0])
                coords = [list(coord) for coord in geom.coords]
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": coords},
                    "properties": {"layer": "walk_network"}
                })
                if idx == 0:
                    print(f"First walk edge coords: {coords[0]}")
            except Exception as e:
                if idx < 5:
                    print(f"Error processing walk edge {idx}: {e}")
                continue
        print(f"Returning {len(features)} walk network features")
        return {"type": "FeatureCollection", "features": features}
    except Exception as e:
        print(f"Error in walk_network endpoint: {e}")
        return {"type": "FeatureCollection", "features": []}
    finally:
        con.close()


@router.get("/api/walk_network/classes")
async def get_walk_network_classes():
    con = get_db_connection()
    try:
        results = con.execute("""
            SELECT rowid AS edge_id, ST_AsText(geometry) AS wkt, road_class
            FROM walk_edges
        """).fetchall()
        features = []
        counts: dict = {}
        for edge_id, wkt_str, road_class in results:
            rc = road_class or "unknown"
            counts[rc] = counts.get(rc, 0) + 1
            try:
                geom = wkt.loads(wkt_str)
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": [list(c) for c in geom.coords]},
                    "properties": {"road_class": rc, "edge_id": int(edge_id)},
                })
            except Exception:
                continue
        return {"type": "FeatureCollection", "features": features, "counts": counts}
    except Exception as e:
        print(f"[ERROR] walk_network/classes: {e}")
        return {"type": "FeatureCollection", "features": [], "counts": {}}
    finally:
        con.close()


@router.get("/api/walk_network/candidates")
async def get_walk_network_candidates(
    bbox: str = Query(..., description="west,south,east,north in WGS84"),
    spacing: int = Query(200, ge=50, le=500, description="Sample spacing in metres"),
):
    try:
        w, s, e, n = [float(x) for x in bbox.split(",")]
    except Exception:
        return {"error": "bbox must be 'west,south,east,north'"}

    UTM31N = "EPSG:32631"
    try:
        to_utm = pyproj.Transformer.from_crs("EPSG:4326", UTM31N, always_xy=True)
        to_wgs = pyproj.Transformer.from_crs(UTM31N, "EPSG:4326", always_xy=True)
    except Exception as exc:
        return {"error": f"Projection setup failed: {exc}"}

    try:
        con = get_db_connection()
        rows = con.execute("""
            SELECT id, ST_AsText(geometry), name, road_type
            FROM walk_edges
            WHERE ST_Intersects(geometry, ST_MakeEnvelope(?, ?, ?, ?))
        """, [w, s, e, n]).fetchall()
        con.close()
    except Exception as exc:
        logger.warning(f"Candidates walk_edges query failed: {exc}")
        return {"type": "FeatureCollection", "features": []}

    raw_candidates: list[tuple[float, float, dict]] = []
    for row in rows:
        edge_id, wkt_str, name, road_type = row
        if not wkt_str:
            continue
        try:
            line_wgs = wkt.loads(wkt_str)
            coords_proj = [to_utm.transform(x, y) for x, y in line_wgs.coords]
        except Exception:
            continue
        line_proj = LineString(coords_proj)
        length = line_proj.length
        if length < 1:
            continue
        n_steps = max(1, int(length / spacing))
        for i in range(n_steps + 1):
            dist = min(i * spacing, length)
            p = line_proj.interpolate(dist)
            offset = 1.0 if dist + 1.0 < length else -1.0
            p2 = line_proj.interpolate(dist + offset)
            dx, dy = p2.x - p.x, p2.y - p.y
            heading = float((np.degrees(np.arctan2(dx, dy)) + 360) % 360)
            lon, lat = to_wgs.transform(p.x, p.y)
            if not (w <= lon <= e and s <= lat <= n):
                continue
            raw_candidates.append((p.x, p.y, {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
                "properties": {
                    "lat": round(lat, 6), "lon": round(lon, 6),
                    "heading": round(heading, 1),
                    "street_name": name or "",
                    "highway_type": road_type or "unknown",
                    "edge_id": edge_id or "",
                    "dist_along_edge_m": round(float(dist), 1),
                },
            }))

    threshold_sq = float(spacing * spacing)
    accepted_utm: list[tuple[float, float]] = []
    features = []
    for ux, uy, feat in raw_candidates:
        if not any((ux - ax) ** 2 + (uy - ay) ** 2 < threshold_sq for ax, ay in accepted_utm):
            accepted_utm.append((ux, uy))
            features.append(feat)

    return {"type": "FeatureCollection", "features": features}


@router.get("/api/roads")
async def get_roads():
    con = get_db_connection()
    try:
        tables = con.execute("SHOW TABLES").fetchall()
        table_names = [t[0] for t in tables]
        print(f"Available tables: {table_names}")
        if 'roads' in table_names:
            query = "SELECT ST_AsText(geometry) as wkt FROM roads"
        elif 'drive_edges' in table_names:
            query = "SELECT ST_AsText(geometry) as wkt FROM drive_edges"
        else:
            print("No roads or drive_edges table found")
            return {"type": "FeatureCollection", "features": []}
        results = con.execute(query).fetchall()
        print(f"Retrieved {len(results)} road edges from database")
        features = []
        for idx, row in enumerate(results):
            try:
                geom = wkt.loads(row[0])
                coords = [list(coord) for coord in geom.coords]
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": coords},
                    "properties": {"layer": "roads"}
                })
            except Exception as e:
                if idx < 5:
                    print(f"Error processing road {idx}: {e}")
                continue
        print(f"Returning {len(features)} road features")
        return {"type": "FeatureCollection", "features": features}
    finally:
        con.close()


@router.get("/api/amenities")
async def get_amenities():
    con = get_db_connection()
    try:
        query = "SELECT name, amenity, ST_AsText(geometry) as wkt, address, website, phone, amenity_tags FROM amenities"
        results = con.execute(query).fetchall()
        features = []
        for row in results:
            try:
                geom = wkt.loads(row[2])
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [geom.x, geom.y]},
                    "properties": {
                        "name": str(row[0]) if row[0] else "Unnamed",
                        "amenity": row[1],
                        "address": str(row[3]) if row[3] else None,
                        "website": str(row[4]) if row[4] else None,
                        "phone": str(row[5]) if row[5] else None,
                        "amenity_tags": str(row[6]) if row[6] else None,
                        "layer": "amenities"
                    }
                })
            except Exception:
                continue
        return {"type": "FeatureCollection", "features": features}
    finally:
        con.close()


@router.get("/api/walk_nodes")
async def get_walk_nodes():
    con = get_db_connection()
    try:
        results = con.execute("SELECT ST_AsText(geometry) as wkt FROM walk_nodes LIMIT 500").fetchall()
        features = []
        for row in results:
            try:
                geom = wkt.loads(row[0])
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [geom.x, geom.y]},
                    "properties": {"layer": "walk_nodes"}
                })
            except Exception:
                continue
        return {"type": "FeatureCollection", "features": features}
    finally:
        con.close()


@router.get("/api/stats")
async def get_stats():
    con = get_db_connection()
    try:
        stats = {}
        for table in ['buildings', 'walk_edges', 'walk_nodes', 'amenities']:
            try:
                count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                stats[table] = count
            except Exception:
                stats[table] = 0
        try:
            bbox = con.execute("""
                SELECT
                    MIN(ST_XMin(geometry)) as minx,
                    MAX(ST_XMax(geometry)) as maxx,
                    MIN(ST_YMin(geometry)) as miny,
                    MAX(ST_YMax(geometry)) as maxy
                FROM buildings
            """).fetchone()
            stats['bbox'] = {
                'minLon': bbox[0], 'maxLon': bbox[1],
                'minLat': bbox[2], 'maxLat': bbox[3]
            }
        except Exception:
            stats['bbox'] = None
        return stats
    finally:
        con.close()


@router.get("/api/tables")
async def list_tables():
    con = get_db_connection()
    try:
        tables = con.execute("SHOW TABLES").fetchall()
        table_list = [t[0] for t in tables]
        table_info = {}
        for table in table_list:
            try:
                count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                table_info[table] = count
            except Exception:
                table_info[table] = 0
        return table_info
    finally:
        con.close()
