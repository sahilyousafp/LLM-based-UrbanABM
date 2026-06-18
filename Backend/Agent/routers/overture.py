import logging
import os
import shutil
import tempfile
import threading
import uuid
from pathlib import Path

import duckdb
from fastapi import APIRouter, Body, File, UploadFile

from paths import DB_PATH, PROJECT_ROOT
from state import sim
from db import get_db_connection

logger = logging.getLogger(__name__)
router = APIRouter()


def _query_data_bounds() -> list | None:
    try:
        con = get_db_connection()
        row = con.execute(
            "SELECT MIN(lon), MIN(lat), MAX(lon), MAX(lat) FROM amenities WHERE lat IS NOT NULL"
        ).fetchone()
        con.close()
        if row and all(v is not None for v in row):
            west, south, east, north = row
            buf = 0.001
            return [round(west - buf, 6), round(south - buf, 6),
                    round(east + buf, 6), round(north + buf, 6)]
    except Exception:
        pass
    return None


@router.get("/api/zone/current")
async def get_current_zone():
    bbox = sim.get_zone_bbox()
    if bbox and len(bbox) == 4:
        return {"bbox": bbox, "source": "user_drawn"}
    data_bounds = _query_data_bounds()
    if data_bounds:
        return {"bbox": data_bounds, "source": "amenity_extent"}
    return {"bbox": [2.1500, 41.3862, 2.1740, 41.4042], "source": "default"}


@router.post("/api/overture/download")
async def start_overture_download(payload: dict = Body(...)):
    from overture_to_duckdb import OverturePipeline

    bbox_list = payload.get("bbox")
    layers = payload.get("layers", ["buildings", "amenities", "transport"])
    location_name = payload.get("location_name", "custom_zone")
    gcp_project = payload.get("gcp_project") or None

    if not bbox_list or len(bbox_list) != 4:
        return {"error": "bbox must be [west, south, east, north]"}
    sim.set_zone_bbox(bbox_list)

    bbox = {
        "min_lon": bbox_list[0], "min_lat": bbox_list[1],
        "max_lon": bbox_list[2], "max_lat": bbox_list[3],
    }
    job_id = str(uuid.uuid4())[:8]

    pipeline = OverturePipeline(
        bbox=bbox, location_name=location_name,
        layers=layers, gcp_project=gcp_project,
        db_path=DB_PATH, job_id=job_id,
    )
    sim.overture_jobs[job_id] = pipeline.progress

    threading.Thread(target=pipeline.run, daemon=True).start()
    return {"job_id": job_id, "status": "started"}


@router.get("/api/overture/status/{job_id}")
async def get_overture_status(job_id: str):
    if job_id not in sim.overture_jobs:
        return {"error": "Unknown job"}
    return sim.overture_jobs[job_id]


@router.post("/api/overture/save/{job_id}")
async def save_overture_download(job_id: str, payload: dict = Body(...)):
    if job_id not in sim.overture_jobs:
        return {"error": "Unknown job"}

    mode = payload.get("mode", "append")
    db_name = payload.get("db_name")

    progress = sim.overture_jobs[job_id]
    pending_path = progress.get("pending_path")
    if not pending_path:
        return {"error": "No pending database found for this job"}

    pending_path = Path(pending_path)
    if not pending_path.exists():
        return {"error": "Pending database file not found"}

    try:
        if mode == "append":
            pending_con = duckdb.connect(str(pending_path), read_only=True)
            pending_con.execute("INSTALL spatial; LOAD spatial;")
            main_con = get_db_connection()

            for tbl in ["buildings", "amenities", "walk_edges"]:
                try:
                    pending_con.execute(f"SELECT 1 FROM {tbl} LIMIT 1")
                    with tempfile.NamedTemporaryFile(suffix='.parquet', delete=False) as tmp:
                        tmp_path = tmp.name
                    pending_con.execute(f"COPY {tbl} TO '{tmp_path}' (FORMAT PARQUET)")
                    main_con.execute(f"""
                        INSERT INTO {tbl}
                        SELECT * FROM read_parquet('{tmp_path}')
                        WHERE id NOT IN (SELECT id FROM {tbl})
                    """)
                    os.unlink(tmp_path)
                except Exception as e:
                    logger.warning(f"Failed to append {tbl}: {e}")

            pending_con.close()
            main_con.close()

            sim.reset_model(
                num_agents=sim.initial_num_agents,
                spawn_seed=sim.initial_spawn_seed,
            )
            pending_path.unlink(missing_ok=True)
            return {"status": "ok", "mode": "append", "message": "Data appended to map database"}

        elif mode == "new":
            if not db_name or not db_name.strip():
                return {"error": "db_name required for new mode"}
            db_name = "".join(c for c in db_name if c.isalnum() or c == "_")
            if not db_name:
                return {"error": "Invalid database name"}
            new_db_path = DB_PATH.parent / f"{db_name}.duckdb"
            shutil.copy2(str(pending_path), str(new_db_path))
            pending_path.unlink(missing_ok=True)
            return {"status": "ok", "mode": "new", "filename": f"{db_name}.duckdb",
                    "message": f"Database saved as {db_name}.duckdb"}

        else:
            return {"error": f"Unknown mode: {mode}"}

    except Exception as exc:
        logger.error(f"Save failed: {exc}")
        pending_path.unlink(missing_ok=True)
        return {"error": f"Save failed: {exc}"}


@router.post("/api/database/upload")
async def upload_database(file: UploadFile = File(...)):
    if not file.filename or not (file.filename.endswith('.duckdb') or file.filename.endswith('.db')):
        return {"error": "Please upload a .duckdb or .db file"}
    try:
        content = await file.read()
        if len(content) == 0:
            return {"error": "Uploaded file is empty"}
        backup_path = DB_PATH.with_name(DB_PATH.stem + ".bak" + DB_PATH.suffix)
        if DB_PATH.exists():
            shutil.copy2(str(DB_PATH), str(backup_path))
        DB_PATH.write_bytes(content)
        sim.reset_model(
            num_agents=sim.initial_num_agents,
            spawn_seed=sim.initial_spawn_seed,
        )
        return {
            "status": "ok",
            "filename": file.filename,
            "agents": len(sim.city_model.city_agents),
            "message": f"Database loaded: {file.filename} ({len(content)//1024} KB)"
        }
    except Exception as exc:
        return {"error": f"Upload failed: {exc}"}


@router.get("/api/external/sources")
async def list_external_sources():
    from plugins.registry import list_with_status
    try:
        con = get_db_connection()
        result = list_with_status(con)
        con.close()
        return {"sources": result}
    except Exception as e:
        return {"sources": [], "error": str(e)}


@router.post("/api/external/{source_name}/download")
async def start_external_download(source_name: str, payload: dict = Body(default={})):
    from plugins.registry import get_plugin

    plugin = get_plugin(source_name)
    if not plugin:
        return {"error": f"Unknown source: {source_name}"}

    bbox_list = payload.get("bbox")
    if not bbox_list or len(bbox_list) != 4:
        return {"error": "bbox must be [west, south, east, north]"}

    bbox = {
        "min_lon": bbox_list[0], "min_lat": bbox_list[1],
        "max_lon": bbox_list[2], "max_lat": bbox_list[3],
    }
    job_id = f"{source_name}_{str(uuid.uuid4())[:8]}"
    progress = {"pct": 0, "status": "running", "log": [], "error": None}
    sim.ext_jobs[job_id] = progress

    def _progress_cb(pct: float, msg: str) -> None:
        progress["pct"] = pct
        progress["log"].append(msg)
        if len(progress["log"]) > 50:
            progress["log"] = progress["log"][-50:]

    def run():
        try:
            con = get_db_connection()
            row_count = plugin.fetch(bbox=bbox, con=con, progress_cb=_progress_cb)
            con.close()
            progress["pct"] = 100
            progress["status"] = "done"
            progress["row_count"] = row_count
            try:
                sim.reset_model(
                    num_agents=sim.initial_num_agents,
                    spawn_seed=sim.initial_spawn_seed,
                )
            except Exception as e:
                logger.warning(f"Model reload after {source_name} download failed: {e}")
        except Exception as e:
            logger.error(f"External download {source_name} failed: {e}")
            progress["status"] = "error"
            progress["error"] = str(e)

    threading.Thread(target=run, daemon=True).start()
    return {"job_id": job_id, "status": "started"}


@router.get("/api/external/{source_name}/status/{job_id}")
async def get_external_status(source_name: str, job_id: str):
    if job_id not in sim.ext_jobs:
        return {"error": "Unknown job"}
    return sim.ext_jobs[job_id]


@router.delete("/api/external/{source_name}")
async def remove_external_source(source_name: str):
    from plugins.registry import get_plugin
    plugin = get_plugin(source_name)
    if not plugin:
        return {"error": f"Unknown source: {source_name}"}
    try:
        con = get_db_connection()
        plugin.drop_table(con)
        con.close()
        sim.reset_model(
            num_agents=sim.initial_num_agents,
            spawn_seed=sim.initial_spawn_seed,
        )
        return {"status": "ok", "message": f"{source_name} data removed"}
    except Exception as e:
        return {"error": str(e)}
