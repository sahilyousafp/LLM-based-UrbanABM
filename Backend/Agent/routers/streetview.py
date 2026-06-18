import json as _json
import logging
import math
import os
import re
import threading
import time
import uuid
from pathlib import Path

import duckdb
from fastapi import APIRouter, Body
from fastapi.responses import FileResponse

from paths import (
    DB_PATH, FRONTEND_DIR,
    SV_OUTPUT_DIR, SV_IMAGES_DIR, SV_RESULTS_DIR,
)
from state import sim

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/streetview/download")
async def download_streetview(payload: dict = Body(...)):
    import requests as _requests

    bbox = payload.get("bbox") or []
    spacing = max(40, min(int(payload.get("spacing", 200)), 600))
    if len(bbox) != 4:
        return {"error": "bbox must be [west, south, east, north]"}
    sim.set_zone_bbox(bbox)

    api_key = os.environ.get("GOOGLE_STREETVIEW_API_KEY", "")
    if not api_key:
        return {"error": "GOOGLE_STREETVIEW_API_KEY not configured in .env"}

    west, south, east, north = [float(x) for x in bbox]
    if east < west or north < south:
        return {"error": "bbox bounds inverted"}

    job_id = str(uuid.uuid4())[:8]
    progress = {"status": "running", "pct": 0.0, "downloaded": 0, "skipped": 0,
                "total": 0, "existing": 0, "log": [], "cancelled": False}
    sim.sv_jobs[job_id] = progress

    def _run():
        import numpy as _np
        deg_per_m_lat = 1.0 / 110540.0
        ref_cos = math.cos(math.radians((north + south) / 2.0)) or 1e-9
        deg_per_m_lon = 1.0 / (111320.0 * ref_cos)
        near_threshold_sq = (5.0 * deg_per_m_lat) ** 2

        def _dist_sq(lat1, lon1, lat2, lon2):
            dlat = lat1 - lat2
            dlon = (lon1 - lon2) * ref_cos
            return dlat * dlat + dlon * dlon

        candidates: list[tuple[float, float, float]] = []
        all_candidates: list[dict] = []
        _db_ok = False
        try:
            from db import get_db_connection
            from shapely import wkt as _wkt
            from collections import defaultdict

            _con = get_db_connection()

            _visited_edges: set[str] = set()
            if sim.city_model and sim.city_model.city_agents:
                for agent in sim.city_model.city_agents:
                    try:
                        ve = agent.memory.status.get("visited_edges", {})
                        if isinstance(ve, dict):
                            _visited_edges.update(str(k) for k in ve.keys())
                    except Exception:
                        pass
                if _visited_edges:
                    progress["log"].append(f"Collecting agent-visited edges ({len(_visited_edges)} visited edges)")

            if _visited_edges:
                _placeholders = ",".join(["?"] * len(_visited_edges))
                _rows = _con.execute(f"""
                    SELECT id, ST_AsText(geometry), name, road_type
                    FROM walk_edges
                    WHERE id IN ({_placeholders})
                """, list(_visited_edges)).fetchall()
            else:
                _rows = _con.execute("""
                    SELECT id, ST_AsText(geometry), name, road_type
                    FROM walk_edges
                    WHERE ST_Intersects(geometry, ST_MakeEnvelope(?,?,?,?))
                """, [west, south, east, north]).fetchall()
            _con.close()

            _node_edges: dict[tuple[float, float], list[tuple[str, float, str, str]]] = defaultdict(list)
            for (edge_id, wkt_str, street_name, road_type) in _rows:
                if not wkt_str:
                    continue
                try:
                    line = _wkt.loads(wkt_str)
                    if line.geom_type != 'LineString' or len(line.coords) < 2:
                        continue
                    coords = list(line.coords)
                    start = (round(coords[0][0], 5), round(coords[0][1], 5))
                    end   = (round(coords[-1][0], 5), round(coords[-1][1], 5))
                    dx = coords[1][0] - coords[0][0]
                    dy = coords[1][1] - coords[0][1]
                    heading = float((_np.degrees(_np.arctan2(dx, dy)) + 360) % 360)
                    _node_edges[start].append((edge_id, heading, street_name or "", road_type or "unknown"))
                    if start != end:
                        dx = coords[-1][0] - coords[-2][0]
                        dy = coords[-1][1] - coords[-2][1]
                        heading = float((_np.degrees(_np.arctan2(dx, dy)) + 360) % 360)
                        _node_edges[end].append((edge_id, heading, street_name or "", road_type or "unknown"))
                except Exception:
                    continue

            for (lon, lat), edges in _node_edges.items():
                degree = len(set(e[0] for e in edges))
                if degree < 2:
                    continue
                if not (west <= lon <= east and south <= lat <= north):
                    continue
                heading = edges[0][1]
                street_name = edges[0][2]
                road_type = edges[0][3]
                all_candidates.append({
                    "lat": lat, "lon": lon,
                    "heading": round(heading, 1),
                    "street_name": street_name,
                    "highway_type": road_type,
                    "edge_id": edges[0][0],
                })
                candidates.append((lat, lon, heading))

            progress["log"].append(f"DuckDB walk intersections: {len(candidates)} candidates (from {len(_rows)} edges)")
            _db_ok = True
        except Exception as _exc:
            progress["log"].append(f"DuckDB unavailable ({_exc}) — falling back to OSMnx nodes")

        if not _db_ok:
            try:
                import osmnx as ox
                import pyproj
                from shapely.geometry import box as _shapely_box
                bbox_poly = _shapely_box(west, south, east, north)
                G = ox.graph_from_polygon(bbox_poly, network_type="walk", retain_all=False)
                gdf_nodes, _ = ox.graph_to_gdfs(G, nodes=True, edges=True)
                gdf_nodes = gdf_nodes.to_crs("EPSG:4326")
                seen_cells: set[tuple] = set()
                for node_id, node_row in gdf_nodes.iterrows():
                    lat_n = float(node_row.geometry.y)
                    lon_n = float(node_row.geometry.x)
                    cell = (round(lat_n, 4), round(lon_n, 4))
                    if cell in seen_cells:
                        continue
                    seen_cells.add(cell)
                    nbrs = list(G.successors(node_id)) + list(G.predecessors(node_id))
                    if nbrs:
                        nb = G.nodes[nbrs[0]]
                        dx, dy = nb["x"] - lon_n, nb["y"] - lat_n
                        heading = (_np.degrees(_np.arctan2(dx, dy)) + 360) % 360
                    else:
                        heading = 0.0
                    candidates.append((round(lat_n, 6), round(lon_n, 6), round(heading, 1)))
                progress["log"].append(f"OSMnx nodes: {len(candidates)} raw candidates")
            except Exception as exc2:
                progress["log"].append(f"OSMnx also unavailable ({exc2}) — falling back to grid")
                step_lat = spacing * deg_per_m_lat
                step_lon = spacing * deg_per_m_lon
                _lat = south
                while _lat <= north:
                    _lon = west
                    while _lon <= east:
                        candidates.append((round(_lat, 6), round(_lon, 6), 0.0))
                        _lon += step_lon
                    _lat += step_lat

        if candidates:
            _accepted: list[tuple[float, float, float]] = []
            for _c in candidates:
                if not any(_dist_sq(_c[0], _c[1], _a[0], _a[1]) < near_threshold_sq
                           for _a in _accepted):
                    _accepted.append(_c)
            progress["log"].append(f"After {spacing}m thinning: {len(_accepted)} candidates (from {len(candidates)})")
            candidates = _accepted

        existing: set[tuple[float, float]] = set()
        if SV_IMAGES_DIR.is_dir():
            for jp in SV_IMAGES_DIR.glob("sv_*.jpg"):
                m = re.match(r"^sv_(-?\d+\.\d+)_(-?\d+\.\d+)_h\d+\.jpg$", jp.name)
                if m:
                    existing.add((float(m.group(1)), float(m.group(2))))
        _pano_map_path = SV_IMAGES_DIR / "sv_pano_map.json"
        _pano_map: dict = {}
        if _pano_map_path.exists():
            try:
                _pano_map = _json.loads(_pano_map_path.read_text(encoding="utf-8"))
                for _k, _v in _pano_map.items():
                    existing.add((_v[0], _v[1]))
            except Exception:
                pass
        SV_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

        progress["total"] = len(candidates)
        progress["existing"] = len(existing)
        existing_list = list(existing)

        def _near(lat, lon):
            return any(_dist_sq(lat, lon, elat, elon) <= near_threshold_sq
                       for (elat, elon) in existing_list)

        for i, candidate in enumerate(candidates):
            if progress.get("cancelled"):
                progress["status"] = "cancelled"
                progress["log"].append("Download cancelled by user.")
                return
            lat, lon, heading = candidate
            progress["pct"] = round(i / max(len(candidates), 1) * 100, 1)
            if _near(lat, lon):
                progress["skipped"] += 1
                progress["log"].append(f"~ {lat:.5f},{lon:.5f}  (near existing)")
                continue
            try:
                meta = _requests.get(
                    "https://maps.googleapis.com/maps/api/streetview/metadata",
                    params={"location": f"{lat:.6f},{lon:.6f}", "radius": 50,
                            "source": "outdoor", "key": api_key},
                    timeout=10,
                ).json()
                if meta.get("status") != "OK":
                    progress["skipped"] += 1
                    progress["log"].append(f"~ {lat:.5f},{lon:.5f}  (no panorama: {meta.get('status', 'unknown')})")
                    continue
                pano_lat = float(meta.get("location", {}).get("lat", lat))
                pano_lon = float(meta.get("location", {}).get("lng", lon))
            except Exception as _exc:
                progress["skipped"] += 1
                progress["log"].append(f"~ {lat:.5f},{lon:.5f}  (metadata error: {_exc})")
                continue
            if _near(pano_lat, pano_lon):
                progress["skipped"] += 1
                progress["log"].append(f"~ {lat:.5f},{lon:.5f}  (pano near existing)")
                continue
            hdg_int = int(round(heading))
            fname = f"sv_{lat:.6f}_{lon:.6f}_h{hdg_int}.jpg"
            fpath = SV_IMAGES_DIR / fname
            if fpath.exists():
                progress["skipped"] += 1
                continue
            try:
                img = _requests.get(
                    "https://maps.googleapis.com/maps/api/streetview",
                    params={"size": "640x640", "location": f"{pano_lat:.6f},{pano_lon:.6f}",
                            "heading": hdg_int, "pitch": 0, "fov": 90,
                            "source": "outdoor", "key": api_key},
                    timeout=20,
                )
                if img.status_code == 200 and img.content[:3] == b"\xff\xd8\xff":
                    fpath.write_bytes(img.content)
                    existing_list.append((lat, lon))
                    existing_list.append((pano_lat, pano_lon))
                    _key = f"{lat:.6f},{lon:.6f}"
                    _pano_map[_key] = [pano_lat, pano_lon]
                    progress["downloaded"] += 1
                    progress["existing"] += 1
                    progress["log"].append(f"✓ {lat:.5f},{lon:.5f}")
                else:
                    progress["skipped"] += 1
                    progress["log"].append(f"~ {lat:.5f},{lon:.5f}  (image HTTP {img.status_code})")
            except Exception as _exc:
                progress["skipped"] += 1
                progress["log"].append(f"~ {lat:.5f},{lon:.5f}  (image error: {_exc})")
            time.sleep(0.15)

        progress["pct"] = 100.0
        progress["status"] = "done"
        progress["log"].append(f"Done — {progress['downloaded']} downloaded, {progress['skipped']} skipped.")

        try:
            _pano_map_path.write_text(_json.dumps(_pano_map, indent=2), encoding="utf-8")
        except Exception:
            pass

        if all_candidates:
            _sample_points = [
                {
                    "id": f"{c['lat']}_{c['lon']}",
                    "lat": c["lat"],
                    "lon": c["lon"],
                    "heading": c["heading"],
                    "street_name": c.get("street_name", ""),
                    "highway_type": c.get("highway_type", "unknown"),
                    "edge_id": c.get("edge_id", ""),
                }
                for c in all_candidates
            ]
        else:
            _sample_points = [
                {
                    "id": f"{lat}_{lon}",
                    "lat": lat,
                    "lon": lon,
                    "heading": hdg,
                    "street_name": "",
                    "highway_type": "unknown",
                    "edge_id": "",
                }
                for lat, lon, hdg in candidates
            ]
        SV_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        _json_path = SV_OUTPUT_DIR / "sample_points.json"
        try:
            _json_path.write_text(_json.dumps(_sample_points, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            progress["log"].append(f"Wrote {len(_sample_points)} sample points to {_json_path}")
        except Exception as _jex:
            progress["log"].append(f"ERROR writing sample_points.json: {_jex}")

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id, "total_candidates": 0}


@router.get("/api/streetview/download/status/{job_id}")
async def get_sv_download_status(job_id: str):
    if job_id not in sim.sv_jobs:
        return {"error": "Unknown job"}
    return sim.sv_jobs[job_id]


@router.delete("/api/streetview/download/{job_id}")
async def cancel_sv_download(job_id: str):
    if job_id not in sim.sv_jobs:
        return {"error": "Unknown job"}
    sim.sv_jobs[job_id]["cancelled"] = True
    return {"ok": True}


@router.get("/api/streetview/stats")
async def get_streetview_stats():
    image_count  = len(list(SV_IMAGES_DIR.glob("sv_*.jpg")))  if SV_IMAGES_DIR.is_dir()  else 0
    result_count = len(list(SV_RESULTS_DIR.glob("*_analysis.json"))) if SV_RESULTS_DIR.is_dir() else 0
    return {"images": image_count, "results": result_count}


@router.delete("/api/streetview/images/unanalyzed")
async def delete_unanalyzed_images():
    if not SV_IMAGES_DIR.is_dir():
        return {"deleted": 0}
    analyzed: set[str] = set()
    if SV_RESULTS_DIR.is_dir():
        for jf in SV_RESULTS_DIR.glob("*_analysis.json"):
            m = re.match(r"^(-?\d+\.\d+)_(-?\d+\.\d+)_analysis\.json$", jf.name)
            if m:
                analyzed.add(f"{m.group(1)}_{m.group(2)}")
    deleted, freed = 0, 0
    for jpg in list(SV_IMAGES_DIR.glob("sv_*.jpg")):
        m = re.match(r"^sv_(-?\d+\.\d+)_(-?\d+\.\d+)_h\d+\.jpg$", jpg.name)
        if not m:
            continue
        key = f"{m.group(1)}_{m.group(2)}"
        if key not in analyzed:
            freed += jpg.stat().st_size
            jpg.unlink()
            deleted += 1
    return {"deleted": deleted, "freed_mb": round(freed / 1_048_576, 1)}


@router.post("/api/streetview/reimport-perception")
async def reimport_perception():
    """Re-import all *_analysis.json files from output/results/ into the streetview_perception DuckDB table."""
    import asyncio
    import sys
    import traceback

    env_path = str(Path(__file__).parent.parent.parent / "Environment")
    if env_path not in sys.path:
        sys.path.insert(0, env_path)

    from ingestion.perception import collect_perception_rows, write_perception_rows  # type: ignore

    try:
        # Step 1: parse JSON files in a thread (pure file I/O, no DuckDB)
        loop = asyncio.get_running_loop()
        rows = await loop.run_in_executor(None, collect_perception_rows)

        if not rows:
            return {"ok": False, "error": "No valid *_analysis.json files found in output/results/"}

        # Step 2: write via the dedicated perception DB connection.
        # perception_con targets perception.duckdb — a separate file from the main
        # eixample_overture.duckdb — so there is zero Windows file-lock conflict.
        con = getattr(sim.city_model, "perception_con", None) if sim.city_model else None
        if con is None:
            return {"ok": False, "error": "Perception DB connection not available — restart the backend"}
        count = write_perception_rows(con, rows)

        logger.info(f"Perception reimport: {count} records loaded")
        return {"ok": True, "records_in_table": count}
    except Exception as exc:
        logger.error("Perception reimport failed", exc_info=True)
        return {"ok": False, "error": str(exc), "detail": traceback.format_exc()}


@router.post("/api/streetview/analyze")
async def start_streetview_analyze(payload: dict = Body(...)):
    images_keys: list[str] = payload.get("images", [])
    if not images_keys:
        return {"error": "No images specified"}

    job_id = str(uuid.uuid4())[:8]
    progress = {"status": "running", "pct": 0.0, "done": 0, "total": len(images_keys), "log": []}
    sim.analyze_jobs[job_id] = progress

    def _run():
        from paths import PROJECT_ROOT as _root
        try:
            import sys
            scripts_path = str(_root / "scripts" / "Notebook_for_Street_PLM" / "image + VLM")
            if scripts_path not in sys.path:
                sys.path.insert(0, scripts_path)
            from importlib import import_module
            vlm_mod = import_module("02_run_vlm_analysis")
            if vlm_mod._model is None:
                vlm_mod.load_model(vlm_mod.MODEL_IDS["7b"])
        except Exception as exc:
            progress["status"] = "error"
            progress["log"].append(f"Failed to load VLM model: {exc}")
            return

        for i, key in enumerate(images_keys):
            progress["pct"] = round(i / len(images_keys) * 100, 1)
            matches = list(SV_IMAGES_DIR.glob(f"sv_{key}_h*.jpg")) if SV_IMAGES_DIR.is_dir() else []
            if not matches:
                progress["log"].append(f"✗ No image for {key}")
                continue
            img_path = matches[0]
            try:
                m = re.match(r"^sv_(-?\d+\.\d+)_(-?\d+\.\d+)_h(\d+)\.jpg$", img_path.name)
                if not m:
                    continue
                lat_s, lon_s, hdg = m.group(1), m.group(2), int(m.group(3))
                lat, lon = float(lat_s), float(lon_s)
                scene_analysis, latency_ms = vlm_mod.analyse_image(img_path)
                result_path = SV_RESULTS_DIR / f"{lat_s}_{lon_s}_analysis.json"
                SV_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
                import json as _j
                from datetime import datetime, timezone
                output = {
                    "metadata": {
                        "timestamp": datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
                        "latitude": lat, "longitude": lon, "heading": hdg,
                        "source_image": img_path.name,
                        "model": vlm_mod.MODEL_IDS.get("7b", "Qwen/Qwen2.5-VL-7B-Instruct"),
                        "latency_ms": latency_ms, "status": "ok",
                    },
                    "scene_analysis": scene_analysis,
                    "nearby_landmarks": [],
                }
                result_path.write_text(_j.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
                progress["done"] += 1
                progress["log"].append(f"✓ {lat_s},{lon_s}")
            except Exception as exc:
                progress["log"].append(f"✗ {key}: {exc}")

        progress["pct"] = 100.0
        progress["status"] = "done"
        progress["log"].append(f"Done — {progress['done']} / {progress['total']} analysed.")

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id, "total": len(images_keys)}


@router.get("/api/streetview/analyze/status/{job_id}")
async def get_analyze_status(job_id: str):
    if job_id not in sim.analyze_jobs:
        return {"error": "Unknown job"}
    return sim.analyze_jobs[job_id]


@router.get("/api/streetview_grid")
async def get_streetview_grid():
    features = []
    seen: set = set()

    if SV_RESULTS_DIR.is_dir():
        for json_file in sorted(SV_RESULTS_DIR.glob("*_analysis.json")):
            m = re.match(r"^(-?\d+\.\d+)_(-?\d+\.\d+)_analysis\.json$", json_file.name)
            if not m:
                continue
            lat, lon = float(m.group(1)), float(m.group(2))
            key = (round(lat, 4), round(lon, 4))
            seen.add(key)
            try:
                data = _json.loads(json_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            meta = data.get("metadata", {})
            scene = data.get("scene_analysis") or {}
            src_img = meta.get("source_image", "")
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "lat": lat, "lon": lon,
                    "heading": meta.get("heading", 0.0),
                    "image_url": f"/api/streetview_grid/image/{src_img}" if src_img else "",
                    "model": meta.get("model", ""),
                    "timestamp": meta.get("timestamp", ""),
                    "schema": "new",
                    "scene": scene.get("scene", ""),
                    "lighting": _json.dumps(scene.get("lighting", []), ensure_ascii=False),
                    "spatial_character": _json.dumps(scene.get("spatial_character", []), ensure_ascii=False),
                    "crowdedness": _json.dumps(scene.get("crowdedness", []), ensure_ascii=False),
                    "greenery": _json.dumps(scene.get("greenery", []), ensure_ascii=False),
                    "street_amenities": _json.dumps(scene.get("street_amenities", []), ensure_ascii=False),
                    "visible_text": _json.dumps(scene.get("visible_text", []), ensure_ascii=False),
                },
            })

    if SV_IMAGES_DIR.is_dir():
        for jpg in sorted(SV_IMAGES_DIR.glob("sv_*.jpg")):
            m = re.match(r"^sv_(-?\d+\.\d+)_(-?\d+\.\d+)_h(\d+)\.jpg$", jpg.name)
            if not m:
                continue
            lat, lon, heading = float(m.group(1)), float(m.group(2)), int(m.group(3))
            key = (round(lat, 4), round(lon, 4))
            if key in seen:
                continue
            seen.add(key)
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "lat": lat, "lon": lon, "heading": heading,
                    "image_url": f"/api/streetview_grid/image/{jpg.name}",
                    "schema": "image_only", "model": "", "timestamp": "", "scene": "",
                },
            })

    return {"type": "FeatureCollection", "features": features}


@router.get("/api/streetview_grid/image/{filename}")
async def get_streetview_image(filename: str):
    safe = Path(filename).name
    for images_dir in [SV_IMAGES_DIR, SV_OUTPUT_DIR / "images"]:
        filepath = images_dir / safe
        if filepath.is_file():
            return FileResponse(filepath, media_type="image/jpeg")
    return {"error": "Image not found", "filename": safe}


@router.get("/api/streetview_grid/json/{filename}")
async def get_streetview_json(filename: str):
    safe = Path(filename).name
    filepath = SV_OUTPUT_DIR / "results" / safe
    if not filepath.is_file():
        return {"error": "Analysis JSON not found", "filename": safe}
    try:
        return _json.loads(filepath.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": str(e)}


@router.get("/api/streetview_grid/analysis/{lat}_{lon}")
async def get_streetview_analysis(lat: str, lon: str):
    try:
        # Use the shared perception_con — perception.duckdb is a separate file from the
        # main spatial DB, so this never conflicts with city_model.con on Windows.
        pcon = getattr(sim.city_model, "perception_con", None) if sim.city_model else None
        if pcon is None:
            return {"error": "Perception DB not available — restart the backend"}
        row = pcon.execute("""
            SELECT latitude, longitude, walkability, has_vegetation,
                   pedestrian_activity, architectural_style,
                   building_condition, source_image,
                   scene_narrative, materials, street_furniture,
                   spatial_impression, heading, timestamp_str, model_name,
                   scene_overview, street_name, highway_type, edge_id,
                   device, latency_ms, scene_text,
                   lighting_json, spatial_character_json,
                   crowdedness_json, greenery_json,
                   street_amenities_json, visible_text_json,
                   nearby_landmarks_json
            FROM streetview_perception
            WHERE ROUND(latitude, 4) = ROUND(CAST(? AS DOUBLE), 4)
              AND ROUND(longitude, 4) = ROUND(CAST(? AS DOUBLE), 4)
            LIMIT 1
        """, [lat, lon]).fetchone()
    except Exception as e:
        return {"error": str(e)}

    if not row:
        return {"error": "Analysis not found"}

    def _parse_json_col(val):
        if not val or val == "[]":
            return []
        try:
            return _json.loads(val)
        except Exception:
            return []

    return {
        "latitude": row[0], "longitude": row[1], "walkability": row[2],
        "has_vegetation": row[3], "pedestrian_activity": row[4],
        "architectural_style": row[5], "building_condition": row[6],
        "source_image": row[7], "scene_narrative": row[8],
        "materials": row[9], "street_furniture": row[10],
        "spatial_impression": row[11], "heading": row[12],
        "timestamp": row[13], "model": row[14], "scene_overview": row[15],
        "street_name": row[16], "highway_type": row[17], "edge_id": row[18],
        "device": row[19], "latency_ms": row[20], "scene_text": row[21],
        "lighting": _parse_json_col(row[22]),
        "spatial_character": _parse_json_col(row[23]),
        "crowdedness": _parse_json_col(row[24]),
        "greenery": _parse_json_col(row[25]),
        "street_amenities": _parse_json_col(row[26]),
        "visible_text": _parse_json_col(row[27]),
        "nearby_landmarks": _parse_json_col(row[28]),
    }


@router.get("/api/assets/agents/{filename}")
async def serve_agent_glb(filename: str):
    from fastapi import HTTPException
    safe = Path(filename).name
    file_path = FRONTEND_DIR / "assets" / "agents" / safe
    if not file_path.exists() or file_path.suffix.lower() != ".glb":
        raise HTTPException(status_code=404, detail=f"Asset not found: {safe}")
    return FileResponse(str(file_path), media_type="model/gltf-binary")
