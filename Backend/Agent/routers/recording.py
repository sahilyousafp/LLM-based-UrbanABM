import logging
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from geoparquet_recorder import create_recorder, get_recorder, clear_recorder, recover_unmerged_sessions
from paths import PROJECT_ROOT
from state import sim

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/recording/start")
async def start_recording(
    session_name: str = None,
    include_thoughts: bool = True,
    include_perception: bool = True,
):
    logger.info("Checking for unmerged recording sessions...")
    recovered = recover_unmerged_sessions(PROJECT_ROOT / "Documentation")
    if recovered:
        logger.info(f"Recovered {len(recovered)} session(s): {[p.name for p in recovered]}")

    clear_recorder()
    current_perception_mode = getattr(sim.city_model, 'perception_mode', 'both')

    recorder = create_recorder(
        output_dir=PROJECT_ROOT / "Documentation",
        max_buffer_size=5000,
        include_thoughts=include_thoughts,
        include_perception=include_perception,
        perception_mode=current_perception_mode,
    )
    session_id = recorder.start_recording(session_name)
    sim.city_model.set_recorder(recorder)

    return {
        "status": "recording_started",
        "session_id": session_id,
        "session_name": session_name or "auto",
        "include_thoughts": include_thoughts,
        "include_perception": include_perception,
        "perception_mode": current_perception_mode,
        "output_dir": str(PROJECT_ROOT / "Documentation"),
        "recovered_sessions": [str(p) for p in recovered],
    }


@router.post("/api/recording/stop")
async def stop_recording():
    recorder = get_recorder()
    if not recorder or not recorder.is_recording:
        return {"status": "no_recording", "message": "No active recording session"}

    sim.city_model.clear_recorder()
    file_path = recorder.stop_recording()

    if file_path:
        docs_dir = PROJECT_ROOT / "Documentation"
        try:
            rel = str(file_path.relative_to(docs_dir)).replace("\\", "/")
        except ValueError:
            rel = file_path.name
        status = recorder.get_status()
        return {
            "status": "recording_stopped",
            "file_path": str(file_path),
            "file_name": rel,
            "total_records": status['total_records'],
            "agents_tracked": status['agents_tracked'],
            "steps_recorded": status['steps_recorded'],
            "records_written": status['records_written'],
        }
    else:
        return {"status": "error", "message": "Failed to export GeoParquet - check server logs"}


@router.get("/api/recording/status")
async def get_recording_status():
    recorder = get_recorder()
    if not recorder:
        return {"is_recording": False, "message": "No recorder initialized"}

    status = recorder.get_status()
    output_path = recorder.get_output_path()
    return {
        "is_recording": status['is_recording'],
        "session_id": status['session_id'],
        "session_name": status['session_name'],
        "start_time": status['start_time'],
        "start_step": status['start_step'],
        "total_records": status['total_records'],
        "agents_tracked": status['agents_tracked'],
        "steps_recorded": status['steps_recorded'],
        "buffer_size": status['buffer_size'],
        "output_path": str(output_path) if output_path else None,
    }


@router.get("/api/recording/download/{filename:path}")
async def download_recording(filename: str):
    safe = Path(filename).name
    file_path = PROJECT_ROOT / "Documentation" / safe
    if not file_path.exists():
        return {"error": "File not found", "filename": safe}
    return FileResponse(
        str(file_path),
        media_type="application/octet-stream",
        filename=file_path.name,
    )


@router.get("/api/recording/list")
async def list_recordings():
    docs_dir = PROJECT_ROOT / "Documentation"
    files = []
    if docs_dir.exists():
        for f in docs_dir.rglob("*.parquet"):
            try:
                stat = f.stat()
                files.append({
                    "filename": f.name,
                    "rel_path": str(f.relative_to(docs_dir)).replace("\\", "/"),
                    "size_kb": round(stat.st_size / 1024),
                    "modified": stat.st_mtime,
                })
            except Exception:
                continue
    files.sort(key=lambda x: x["modified"], reverse=True)
    return {"files": files}


@router.get("/api/recording/load")
async def load_recording_data(filename: str):
    safe = Path(filename).name
    file_path = PROJECT_ROOT / "Documentation" / safe
    if not file_path.exists():
        return {"error": "File not found", "filename": safe}
    try:
        import pandas as pd
        df = pd.read_parquet(str(file_path))
        agents = []
        for agent_id, group in df.groupby("agent_id"):
            group = group.sort_values("step")
            row0 = group.iloc[0]
            positions = [[float(r["longitude"]), float(r["latitude"])] for _, r in group.iterrows()]
            start = None
            if "start_lon" in df.columns and row0.get("start_lon") is not None:
                try:
                    start = [float(row0["start_lon"]), float(row0["start_lat"])]
                except Exception:
                    pass
            target = None
            if "target_lon" in df.columns and row0.get("target_lon") is not None:
                try:
                    target = [float(row0["target_lon"]), float(row0["target_lat"])]
                except Exception:
                    pass
            agents.append({
                "id": int(agent_id),
                "archetype": str(row0.get("archetype", "unknown")),
                "positions": positions,
                "start": start,
                "target": target,
            })
        total_steps = int(df["step"].max()) if len(df) else 0
        return {"session": file_path.stem, "total_steps": total_steps, "agents": agents}
    except Exception as e:
        return {"error": str(e), "filename": filename}
