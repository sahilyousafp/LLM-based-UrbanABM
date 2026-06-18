import json
from pathlib import Path

from fastapi import APIRouter, Body
from fastapi.responses import FileResponse

from paths import BENCHMARK_FILE, COMPARE_IMAGES_DIR, VLM_ANALYSIS_DIR, PROJECT_ROOT

router = APIRouter()


@router.get("/api/vlm/compare-images/{filename}")
async def get_vlm_compare_image(filename: str):
    from fastapi import HTTPException
    safe = Path(filename).name
    img_path = COMPARE_IMAGES_DIR / safe
    if not img_path.exists() or img_path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        raise HTTPException(status_code=404, detail=f"{safe} not found")
    return FileResponse(str(img_path), media_type="image/png")


@router.get("/api/vlm/barcelona-image")
async def get_barcelona_benchmark_image():
    from fastapi import HTTPException
    img_path = PROJECT_ROOT / "benchmark" / "vlm_analysis_outputs" / "barcelona_streetview.jpg"
    if not img_path.exists():
        raise HTTPException(status_code=404, detail="Benchmark image not found")
    return FileResponse(str(img_path), media_type="image/jpeg")


@router.get("/api/vlm/benchmark")
async def get_vlm_benchmark():
    if not BENCHMARK_FILE.exists():
        return {"models": {}, "benchmark_location": "No benchmark data yet. Run notebook 04."}
    return json.loads(BENCHMARK_FILE.read_text())


@router.post("/api/vlm/benchmark")
async def post_vlm_benchmark(payload: dict = Body(...)):
    stored = json.loads(BENCHMARK_FILE.read_text()) if BENCHMARK_FILE.exists() else {
        "benchmark_image": "", "benchmark_location": "", "models": {}
    }
    if "models" in payload:
        stored["models"].update(payload["models"])
        stored.update({k: v for k, v in payload.items() if k != "models"})
    else:
        display_name = payload.get("display_name", payload.get("model_id", "Unknown"))
        stored["models"][display_name] = payload
    stored["last_updated"] = payload.get("last_updated", "")
    BENCHMARK_FILE.write_text(json.dumps(stored, indent=2))
    return {"ok": True, "total_models": len(stored["models"])}


@router.get("/api/vlm/analysis-outputs")
async def get_vlm_analysis_outputs():
    from fastapi import HTTPException
    if not VLM_ANALYSIS_DIR.exists():
        raise HTTPException(status_code=404, detail="VLM analysis outputs directory not found")
    results = {}
    for f in sorted(VLM_ANALYSIS_DIR.glob("*_analysis.json")):
        slug = f.name.replace("_analysis.json", "")
        try:
            data = json.loads(f.read_text())
            results[slug] = data
        except Exception as e:
            results[slug] = {"error": str(e)}
    return results
