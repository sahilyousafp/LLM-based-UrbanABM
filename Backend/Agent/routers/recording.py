import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, UploadFile
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


@router.get("/api/recording/recover")
async def recover_recordings():
    docs_dir = PROJECT_ROOT / "Documentation"
    temp_files = list(docs_dir.rglob("agent_recording_*_flush_*.tmp.parquet")) if docs_dir.exists() else []
    if not temp_files:
        return {"recovered": False, "files": [], "message": "No orphaned recordings found"}

    recovered = recover_unmerged_sessions(docs_dir)
    return {
        "recovered": bool(recovered),
        "files": [str(p.relative_to(docs_dir)).replace("\\", "/") for p in recovered],
        "temp_files_found": len(temp_files),
        "message": f"Recovered {len(recovered)} session(s) from {len(temp_files)} temp files",
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
        return _parse_parquet_to_agents(file_path)
    except Exception as e:
        return {"error": str(e), "filename": filename}


def _safe_json(val) -> dict | list | None:
    if val is None or (isinstance(val, float) and val != val):
        return None
    if isinstance(val, (dict, list)):
        return val
    try:
        import json
        return json.loads(str(val))
    except Exception:
        return None


def _parse_parquet_to_agents(file_path: Path):
    import pandas as pd
    df = pd.read_parquet(str(file_path))
    agents = []
    for agent_id, group in df.groupby("agent_id"):
        group = group.sort_values("step")
        row0 = group.iloc[0]
        last_row = group.iloc[-1]
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

        mood_history = []
        cognition_history = []
        needs_history = []
        if "cognition_state_json" in df.columns:
            for _, r in group.iterrows():
                cog = _safe_json(r.get("cognition_state_json"))
                if isinstance(cog, dict):
                    mood_history.append(cog.get("mood", "neutral"))
                    cognition_history.append(cog)
                else:
                    mood_history.append("neutral")
                    cognition_history.append({"mood": "neutral", "curiosity": 0.7, "fatigue": 0.0})
        if "needs_json" in df.columns:
            for _, r in group.iterrows():
                n = _safe_json(r.get("needs_json"))
                needs_history.append(n if isinstance(n, dict) else {})

        stream_events = []
        if "thought_stream_json" in df.columns:
            raw = _safe_json(last_row.get("thought_stream_json"))
            if isinstance(raw, list):
                stream_events = raw

        satisfaction_history = []
        if "satisfaction_reasoning" in df.columns:
            for _, r in group.iterrows():
                val = r.get("satisfaction_reasoning")
                satisfaction_history.append(str(val) if val and str(val) != "nan" else "")

        agents.append({
            "id": int(agent_id),
            "archetype": str(row0.get("archetype", "unknown")),
            "positions": positions,
            "start": start,
            "target": target,
            "moodHistory": mood_history,
            "cognitionHistory": cognition_history,
            "needsHistory": needs_history,
            "streamEvents": stream_events,
            "satisfactionHistory": satisfaction_history,
        })
    total_steps = int(df["step"].max()) if len(df) else 0
    return {"session": file_path.stem, "total_steps": total_steps, "agents": agents}


def _analyze_parquet(file_path: Path, session_name: str = "unknown"):
    """Full analytics computation from a parquet recording."""
    import pandas as pd
    import json
    from collections import Counter

    needed_cols = [
        "agent_id", "step", "longitude", "latitude", "archetype",
        "cognition_state_json", "needs_json", "is_fallback",
        "perception_available", "satisfaction_reasoning",
        "satisfaction_source", "decision_reason",
    ]
    try:
        import pyarrow.parquet as pq
        schema_cols = pq.ParquetFile(str(file_path)).schema.names
        use_cols = [c for c in needed_cols if c in schema_cols]
        df = pd.read_parquet(str(file_path), columns=use_cols)
    except Exception:
        df = pd.read_parquet(str(file_path))
    n_agents = int(df["agent_id"].nunique())
    n_steps = int(df["step"].max()) if len(df) else 0
    n_rows = len(df)

    if "cognition_state_json" in df.columns:
        cog = df["cognition_state_json"].apply(_safe_json)
        df["_mood"] = cog.apply(lambda x: x.get("mood", "neutral") if isinstance(x, dict) else "neutral")
        df["_curiosity"] = cog.apply(lambda x: float(x.get("curiosity", 0.5)) if isinstance(x, dict) else 0.5)
        df["_fatigue"] = cog.apply(lambda x: float(x.get("fatigue", 0.0)) if isinstance(x, dict) else 0.0)
    else:
        df["_mood"] = "neutral"
        df["_curiosity"] = 0.5
        df["_fatigue"] = 0.0

    if "needs_json" in df.columns:
        df["_needs"] = df["needs_json"].apply(_safe_json).apply(lambda x: x if isinstance(x, dict) else {})
    else:
        df["_needs"] = [{}] * len(df)

    fb_rate = 0.0
    if "is_fallback" in df.columns:
        fb = df["is_fallback"].apply(lambda x: bool(x) if isinstance(x, bool) else str(x).lower() == "true")
        fb_rate = round(float(fb.mean()) * 100, 1)

    perc_rate = 0.0
    if "perception_available" in df.columns:
        pa = df["perception_available"].apply(lambda x: bool(x) if isinstance(x, bool) else str(x).lower() == "true")
        perc_rate = round(float(pa.mean()) * 100, 1)

    bbox = {
        "minLon": float(df["longitude"].min()), "maxLon": float(df["longitude"].max()),
        "minLat": float(df["latitude"].min()), "maxLat": float(df["latitude"].max()),
    }

    sample = df.iloc[::5]
    positions = []
    for idx in sample.index:
        r = df.loc[idx]
        needs = r["_needs"] if isinstance(r["_needs"], dict) else {}
        positions.append([
            round(float(r["latitude"]), 6), round(float(r["longitude"]), 6),
            str(r["_mood"]), int(r["step"]), int(r["agent_id"]),
            round(float(r["_curiosity"]), 3), round(float(r["_fatigue"]), 3),
            {k: round(float(v), 3) for k, v in needs.items() if isinstance(v, (int, float))}
        ])

    bin_size = max(1, n_steps // 20)
    bins = list(range(0, n_steps + bin_size + 1, bin_size))
    df["_bin"] = pd.cut(df["step"], bins=bins, labels=False, include_lowest=True)
    ts = []
    for bin_idx in sorted(df["_bin"].dropna().unique()):
        chunk = df[df["_bin"] == bin_idx]
        if len(chunk) == 0:
            continue
        mc = chunk["_mood"].value_counts()
        total = len(chunk)
        moods = {str(k): round(v / total, 4) for k, v in mc.items()}
        needs_agg = {}
        for n in chunk["_needs"]:
            if isinstance(n, dict):
                for k, v in n.items():
                    try:
                        val = float(v)
                        needs_agg.setdefault(k, []).append(val)
                    except (ValueError, TypeError):
                        pass
        needs_avg = {k: round(sum(v) / len(v), 4) for k, v in needs_agg.items() if v}
        ts.append({
            "step": int(bins[int(bin_idx)]),
            "moods": moods,
            "curiosity": round(float(chunk["_curiosity"].mean()), 4),
            "fatigue": round(float(chunk["_fatigue"].mean()), 4),
            "needs": needs_avg,
        })

    mood_counts = {str(k): int(v) for k, v in df["_mood"].value_counts().items()}

    theme_kw = {
        "Food & Dining": ["restaurant", "food", "eat", "cafe", "coffee", "dining", "meal", "bakery", "bar"],
        "Architecture": ["building", "architecture", "facade", "historic", "design", "modernist", "structure"],
        "Nature & Green": ["tree", "green", "park", "garden", "nature", "plant", "vegetation"],
        "Shopping": ["shop", "store", "market", "buy", "retail", "commercial"],
        "Culture & Art": ["museum", "art", "culture", "gallery", "exhibition", "monument"],
        "Rest & Comfort": ["rest", "bench", "sit", "shade", "comfortable", "relax"],
        "Social": ["people", "crowd", "social", "lively", "busy", "atmosphere"],
        "Walking & Streets": ["street", "walk", "path", "sidewalk", "pedestrian", "boulevard"],
    }
    sat_themes, sat_samples, total_sat = {}, {}, 0
    if "satisfaction_reasoning" in df.columns:
        sat_col = df["satisfaction_reasoning"].dropna().astype(str)
        sat_col = sat_col[sat_col != "nan"]
        total_sat = len(sat_col)
        sat_lower = sat_col.str.lower()
        matched = pd.Series(False, index=sat_col.index)
        for theme, kws in theme_kw.items():
            pattern = "|".join(kws)
            mask = sat_lower.str.contains(pattern, na=False) & ~matched
            sat_themes[theme] = int(mask.sum())
            sat_samples[theme] = [s[:200] for s in sat_col[mask].head(2).tolist()]
            matched = matched | mask
        remaining = int((~matched).sum())
        if remaining > 0:
            sat_themes["Other"] = remaining

    sat_source = {}
    if "satisfaction_source" in df.columns:
        src = df["satisfaction_source"].dropna().astype(str)
        src = src[src != "nan"]
        sat_source = {str(k): int(v) for k, v in src.value_counts().items()}

    target_types = {}
    if "decision_reason" in df.columns:
        dec = df["decision_reason"].dropna().astype(str).str.lower()
        for t in ["park", "museum", "cafe", "restaurant", "pharmacy", "shop", "hotel", "church", "plaza", "market", "library", "bank"]:
            count = int(dec.str.contains(t, na=False).sum())
            if count > 0:
                target_types[t] = count

    archetype_counts = {}
    if "archetype" in df.columns:
        archetype_counts = {str(k): int(v) for k, v in df["archetype"].value_counts().items()}

    trails = {}
    for aid, group in df.groupby("agent_id"):
        coords = group.iloc[::10][["latitude", "longitude"]].values.tolist()
        trails[str(int(aid))] = [[round(c[0], 6), round(c[1], 6)] for c in coords]

    return {
        "positions": positions,
        "emotionTimeSeries": ts,
        "satisfactionThemes": sat_themes,
        "totalSatisfaction": max(total_sat, 1),
        "moodCounts": mood_counts,
        "targetTypes": target_types,
        "satisfactionSource": sat_source,
        "archetypeCounts": archetype_counts,
        "stats": {
            "totalAgents": n_agents, "totalSteps": n_steps, "totalRows": n_rows,
            "fallbackRate": fb_rate, "perceptionRate": perc_rate, "bbox": bbox,
        },
        "satisfactionSamples": sat_samples,
        "trails": trails,
        "session": session_name,
    }


@router.post("/api/recording/analyze")
async def analyze_recording_upload(file: UploadFile = File(...)):
    if not file.filename or not file.filename.endswith(".parquet"):
        return {"error": "Only .parquet files are supported"}
    try:
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = Path(tmp.name)
        result = _analyze_parquet(tmp_path, Path(file.filename).stem)
        tmp_path.unlink(missing_ok=True)
        return result
    except Exception as e:
        logger.exception("analyze_recording_upload failed")
        return {"error": str(e)}


@router.get("/api/recording/analyze")
async def analyze_recording_existing(filename: str):
    docs_dir = PROJECT_ROOT / "Documentation"
    file_path = None
    for f in docs_dir.rglob("*.parquet"):
        if f.name == Path(filename).name:
            file_path = f
            break
    if not file_path or not file_path.exists():
        return {"error": "File not found", "filename": filename}
    try:
        return _analyze_parquet(file_path, file_path.stem)
    except Exception as e:
        logger.exception("analyze_recording_existing failed")
        return {"error": str(e)}


@router.post("/api/recording/upload")
async def upload_recording(file: UploadFile = File(...)):
    if not file.filename or not file.filename.endswith(".parquet"):
        return {"error": "Only .parquet files are supported"}
    try:
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = Path(tmp.name)
        result = _parse_parquet_to_agents(tmp_path)
        result["session"] = Path(file.filename).stem
        tmp_path.unlink(missing_ok=True)
        return result
    except Exception as e:
        return {"error": str(e)}
