import duckdb
import json as _json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Body
from fastapi.responses import FileResponse

from paths import (
    FRONTEND_DIR,
    PERCEPTION_DB_PATH,
    SV_OUTPUT_DIR, SV_IMAGES_DIR, SV_RESULTS_DIR,
)
from state import sim

logger = logging.getLogger(__name__)
router = APIRouter()


def _open_perception_ro():
    """Open a transient read-only perception DB connection (caller MUST close it).

    Nothing holds perception.duckdb persistently — the model loads it into memory at
    startup and releases the file — so display queries open a fresh short-lived
    read-only handle and close it promptly, keeping the file writable for reimport.
    """
    try:
        con = duckdb.connect(str(PERCEPTION_DB_PATH), read_only=True)
        con.load_extension("spatial")
        return con
    except Exception as exc:
        logger.error("Failed to open perception DB (read-only): %s", exc)
        return None


def _write_perception_rows_rw(write_fn, rows):
    """Write perception rows via a short-lived read-write connection.

    Nothing holds perception.duckdb persistently (the model loads to memory and
    releases the file), so this opens read-write directly, writes, checkpoints and
    closes. It then refreshes the model's in-memory perception points so the new data
    is live without a restart. Returns the row count, or None on failure.
    """
    try:
        rw = duckdb.connect(str(PERCEPTION_DB_PATH))
        rw.install_extension("spatial")
        rw.load_extension("spatial")
        count = write_fn(rw, rows)
        try:
            rw.execute("CHECKPOINT")
        except Exception:
            pass
        rw.close()
    except Exception as exc:
        logger.error("Perception write (read-write) failed: %s", exc)
        return None
    # Refresh in-memory perception so the reimport takes effect live (no restart).
    try:
        if sim.city_model is not None:
            sim.city_model._load_perception_points()
    except Exception as exc:
        logger.error("Failed to reload perception points after reimport: %s", exc)
    return count


@router.post("/api/streetview/download")
async def download_streetview(payload: dict = Body(...)):
    import requests as _requests
    import pyproj as _pyproj

    bbox = payload.get("bbox") or []
    spacing = max(40, min(int(payload.get("spacing", 200)), 600))
    frontend_candidates: list[dict] | None = payload.get("candidates")
    if len(bbox) != 4:
        return {"error": "bbox must be [west, south, east, north]"}
    sim.set_zone_bbox(bbox)

    api_key = os.environ.get("GOOGLE_STREETVIEW_API_KEY", "")
    if not api_key:
        return {"error": "GOOGLE_STREETVIEW_API_KEY not configured in .env"}

    west, south, east, north = [float(x) for x in bbox]
    if east < west or north < south:
        return {"error": "bbox bounds inverted"}

    if frontend_candidates and isinstance(frontend_candidates, list) and len(frontend_candidates) > 0:
        candidate_list = [
            {
                "lat": float(c.get("lat", 0)),
                "lon": float(c.get("lon", 0)),
                "heading": float(c.get("heading", 0)),
                "street_name": c.get("street_name", ""),
                "highway_type": c.get("highway_type", "unknown"),
                "edge_id": c.get("edge_id", ""),
            }
            for c in frontend_candidates
            if c.get("lat") is not None and c.get("lon") is not None
        ]
    else:
        candidate_list = None

    job_id = str(uuid.uuid4())[:8]
    progress = {"status": "running", "pct": 0.0, "downloaded": 0, "skipped": 0,
                "total": 0, "existing": 0, "log": [], "cancelled": False}
    sim.sv_jobs[job_id] = progress

    def _run():
        _UTM31N = "EPSG:32631"
        try:
            _to_utm = _pyproj.Transformer.from_crs("EPSG:4326", _UTM31N, always_xy=True)
        except Exception as _proj_exc:
            progress["status"] = "error"
            progress["log"].append(f"Projection setup failed: {_proj_exc}")
            return

        if candidate_list is not None:
            candidates = candidate_list
            progress["log"].append(f"Using {len(candidates)} candidates from frontend")
        else:
            try:
                from routers.spatial import generate_walk_candidates
                candidates = generate_walk_candidates((west, south, east, north), spacing)
                progress["log"].append(f"Generated {len(candidates)} candidates ({spacing}m spacing)")
            except Exception as _exc:
                progress["status"] = "error"
                progress["log"].append(f"Candidate generation failed: {_exc}")
                return

        existing_utm: list[tuple[float, float]] = []
        if SV_IMAGES_DIR.is_dir():
            for jp in SV_IMAGES_DIR.glob("sv_*.jpg"):
                m = re.match(r"^sv_(-?\d+\.\d+)_(-?\d+\.\d+)_h\d+\.jpg$", jp.name)
                if m:
                    elat, elon = float(m.group(1)), float(m.group(2))
                    ux, uy = _to_utm.transform(elon, elat)
                    existing_utm.append((ux, uy))
        _pano_map_path = SV_IMAGES_DIR / "sv_pano_map.json"
        _pano_map: dict = {}
        if _pano_map_path.exists():
            try:
                _pano_map = _json.loads(_pano_map_path.read_text(encoding="utf-8"))
                for _k, _v in _pano_map.items():
                    ux, uy = _to_utm.transform(_v[1], _v[0])
                    existing_utm.append((ux, uy))
            except Exception:
                pass
        SV_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

        progress["total"] = len(candidates)
        progress["existing"] = len(existing_utm)

        half_spacing_sq = (spacing * 0.5) ** 2

        def _near(lat, lon):
            ux, uy = _to_utm.transform(lon, lat)
            return any((ux - ex) ** 2 + (uy - ey) ** 2 <= half_spacing_sq
                       for (ex, ey) in existing_utm)

        for i, cand in enumerate(candidates):
            if progress.get("cancelled"):
                progress["status"] = "cancelled"
                progress["log"].append("Download cancelled by user.")
                return
            lat, lon, heading = cand["lat"], cand["lon"], cand["heading"]
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
                    ux, uy = _to_utm.transform(lon, lat)
                    existing_utm.append((ux, uy))
                    ux, uy = _to_utm.transform(pano_lon, pano_lat)
                    existing_utm.append((ux, uy))
                    _key = f"{lat:.6f},{lon:.6f}"
                    _pano_map[_key] = [pano_lat, pano_lon]
                    progress["downloaded"] += 1
                    progress["existing"] += 1
                    progress["log"].append(f"[OK] {lat:.5f},{lon:.5f}")
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
            for c in candidates
        ]
        SV_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        _json_path = SV_OUTPUT_DIR / "sample_points.json"
        try:
            _json_path.write_text(_json.dumps(_sample_points, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            progress["log"].append(f"Wrote {len(_sample_points)} sample points to {_json_path}")
        except Exception as _jex:
            progress["log"].append(f"ERROR writing sample_points.json: {_jex}")

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id, "total_candidates": len(candidate_list) if candidate_list else 0}


def _rebuild_sample_points_json(
    new_candidates: list[dict] | None = None,
    progress: dict | None = None,
) -> None:
    """Rebuild sample_points.json from existing file + new candidates + images on disk."""
    _json_path = SV_OUTPUT_DIR / "sample_points.json"
    seen: dict[str, dict] = {}

    if _json_path.exists():
        try:
            for pt in _json.loads(_json_path.read_text(encoding="utf-8")):
                key = f"{pt['lat']}_{pt['lon']}"
                seen[key] = pt
        except Exception:
            pass

    if new_candidates:
        for c in new_candidates:
            key = f"{c['lat']}_{c['lon']}"
            if key not in seen:
                seen[key] = {
                    "id": key,
                    "lat": c["lat"],
                    "lon": c["lon"],
                    "heading": c["heading"],
                    "street_name": c.get("street_name", ""),
                    "highway_type": c.get("highway_type", "unknown"),
                    "edge_id": c.get("edge_id", ""),
                }

    if SV_IMAGES_DIR.is_dir():
        for jpg in SV_IMAGES_DIR.glob("sv_*.jpg"):
            m = re.match(r"^sv_(-?\d+\.\d+)_(-?\d+\.\d+)_h(\d+)\.jpg$", jpg.name)
            if not m:
                continue
            lat, lon, heading = float(m.group(1)), float(m.group(2)), int(m.group(3))
            key = f"{lat}_{lon}"
            if key not in seen:
                seen[key] = {
                    "id": key,
                    "lat": lat,
                    "lon": lon,
                    "heading": heading,
                    "street_name": "",
                    "highway_type": "unknown",
                    "edge_id": "",
                }

    points = sorted(seen.values(), key=lambda p: (p["lat"], p["lon"]))
    SV_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _json_path.write_text(
            _json.dumps(points, indent=2, ensure_ascii=False, allow_nan=False),
            encoding="utf-8",
        )
        msg = f"Wrote {len(points)} sample points to {_json_path}"
        if progress:
            progress["log"].append(msg)
        logger.info(msg)
    except Exception as exc:
        msg = f"ERROR writing sample_points.json: {exc}"
        if progress:
            progress["log"].append(msg)
        logger.error(msg)


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


@router.post("/api/streetview/rebuild-sample-points")
async def rebuild_sample_points():
    _rebuild_sample_points_json()
    json_path = SV_OUTPUT_DIR / "sample_points.json"
    if json_path.exists():
        points = _json.loads(json_path.read_text(encoding="utf-8"))
        return {"ok": True, "count": len(points)}
    return {"ok": False, "error": "Failed to write sample_points.json"}


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

        # Step 2: write via a short-lived read-write connection, then refresh the
        # model's in-memory perception. Nothing holds perception.duckdb persistently
        # (the model loads it into RAM at startup and releases the file), so this can
        # take the write lock even while the simulation and the agent-lab are running.
        count = await loop.run_in_executor(None, _write_perception_rows_rw, write_perception_rows, rows)
        if count is None:
            return {"ok": False, "error": "Perception write failed — check logs (is another process writing the DB?)"}

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
                progress["log"].append(f"[OK] {lat_s},{lon_s}")
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
    # Transient read-only handle — opened and closed per request so it never holds a
    # lock that would block a reimport. perception.duckdb is a separate file from the
    # main spatial DB, so this also never conflicts with city_model.con on Windows.
    pcon = _open_perception_ro()
    if pcon is None:
        return {"error": "Perception DB not available — check logs for details"}
    try:
        row = pcon.execute("""
            SELECT latitude, longitude, walkability, has_vegetation,
                   pedestrian_activity, architectural_style,
                   building_condition, source_image,
                   scene_narrative, heading, timestamp_str, model_name,
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
    finally:
        try:
            pcon.close()
        except Exception:
            pass

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
        "heading": row[9], "timestamp": row[10], "model": row[11],
        "scene_overview": row[12], "street_name": row[13], "highway_type": row[14],
        "edge_id": row[15], "device": row[16], "latency_ms": row[17],
        "scene_text": row[18],
        "lighting": _parse_json_col(row[19]),
        "spatial_character": _parse_json_col(row[20]),
        "crowdedness": _parse_json_col(row[21]),
        "greenery": _parse_json_col(row[22]),
        "street_amenities": _parse_json_col(row[23]),
        "visible_text": _parse_json_col(row[24]),
        "nearby_landmarks": _parse_json_col(row[25]),
    }


@router.get("/api/assets/agents/{filename}")
async def serve_agent_glb(filename: str):
    from fastapi import HTTPException
    safe = Path(filename).name
    file_path = FRONTEND_DIR / "assets" / "agents" / safe
    if not file_path.exists() or file_path.suffix.lower() != ".glb":
        raise HTTPException(status_code=404, detail=f"Asset not found: {safe}")
    return FileResponse(str(file_path), media_type="model/gltf-binary")
