"""
street_plm_job.py
=================
LightningAI Job version of StreetPLM_Eixample.ipynb.

What this script does:
  1. Queries Overture Maps pedestrian walk edges from BigQuery for the
     2 km x 2 km Eixample study area (same filter as Backend/Environment/overture_to_duckdb.py)
  2. Samples points every 250 m along each walk edge, heading aligned with street direction
  3. Checks Street View availability and fetches one 640x640 image per sample point
  4. Runs Meta PerceptionLM-1B (full-image urban analysis) on each image
  5. Saves one JSON + JPG per location to OUTPUT_DIR
     (default /teamspace/studios/this_studio/StreetPLM)

Required environment variables (set as Lightning AI Secrets):
  GOOGLE_STREETVIEW_API_KEY  -- Google Maps Platform Street View Static API key
  HF_TOKEN                   -- Hugging Face token with access to facebook/Perception-LM-1B
  GCP_PROJECT_ID             -- GCP project with BigQuery API enabled

Optional environment variable:
  OUTPUT_DIR                 -- Override default output root
                               (default: /teamspace/studios/this_studio/StreetPLM)

GCP / BigQuery auth:
  Uses Application Default Credentials (ADC) -- run `gcloud auth application-default login`
  on the Lightning AI terminal before submitting the job.

Usage:
  python street_plm_job.py
  python street_plm_job.py --output-dir /my/output
  python street_plm_job.py --trial                                  # analyse first sample point
  python street_plm_job.py --trial --trial-lat 41.3952 --trial-lon 2.1620
"""

import argparse
import gc
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: ensure accelerate is installed (required for device_map="auto")
# ---------------------------------------------------------------------------
try:
    import accelerate  # noqa: F401
except ImportError:
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-q", "accelerate>=0.30"]
    )

import numpy as np
import pyproj
import requests
import torch
from google.cloud import bigquery
from huggingface_hub import login
from PIL import Image as _PILImage
from pydantic import BaseModel, field_validator, FieldValidationInfo
from typing import Any
from shapely import wkt as shapely_wkt
from shapely.geometry import LineString  # noqa: F401
import geopandas as gpd
from tqdm.auto import tqdm
from dotenv import load_dotenv
from transformers import AutoModelForImageTextToText, AutoProcessor

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (from environment variables)
# Load .env from the script's own directory first; Lightning AI Secrets
# injected as env vars will already be present and take precedence because
# load_dotenv() does not override existing env vars by default.
# ---------------------------------------------------------------------------
load_dotenv(Path(__file__).parent / ".env")

STREETVIEW_API_KEY = os.environ.get("GOOGLE_STREETVIEW_API_KEY", "")
HF_TOKEN           = os.environ.get("HF_TOKEN", "")
GCP_PROJECT_ID     = os.environ.get("GCP_PROJECT_ID", "")

# Bounding box -- 2 km x 2 km centred on Passeig de Gracia
# Mirrors Backend/Environment/overture_to_duckdb.py
BBOX = {
    "min_lon": 2.1500,
    "min_lat": 41.3862,
    "max_lon": 2.1740,
    "max_lat": 41.4042,
}

BIGQUERY_PROJECT  = "bigquery-public-data"
OVERTURE_DATASET  = "overture_maps"

SAMPLE_DISTANCE_M = 250
SV_SIZE           = "640x640"
SV_FOV            = 90
SV_PITCH          = 0
SV_RADIUS         = 50
MODEL_ID          = "facebook/Perception-LM-1B"
MAX_NUM_TILES     = 4    # 4 tiles; overridden dynamically by GPU VRAM in load_model()
MAX_NEW_TOKENS    = 1024  # 16 fields + quadrant scene_context fits in ~1024 tokens

UTM31N = "EPSG:32631"

_SV_BASE   = "https://maps.googleapis.com/maps/api/streetview"
_META_BASE = "https://maps.googleapis.com/maps/api/streetview/metadata"

# ---------------------------------------------------------------------------
# Pydantic schema
# ---------------------------------------------------------------------------

_QUADRANT_KEYS = ("top_left", "top_right", "bottom_left", "bottom_right")

class StreetSceneAnalysis(BaseModel):
    """Validated schema for individual comfort-focused urban-quality analysis.

    scene_context is a dict with 4 quadrant keys (top_left, top_right,
    bottom_left, bottom_right); all other fields are descriptive strings.
    """

    scene_context       : Any  = "unknown"   # dict{top_left,top_right,bottom_left,bottom_right}
    perceived_safety    : str  = "unknown"
    visibility          : str  = "unknown"
    lighting_quality    : str  = "unknown"
    cleanliness         : str  = "unknown"
    greenery            : str  = "unknown"
    thermal_comfort     : str  = "unknown"
    walkability         : str  = "unknown"
    noise_comfort       : str  = "unknown"
    crowding            : str  = "unknown"
    privacy             : str  = "unknown"
    social_potential    : str  = "unknown"
    visual_interest     : str  = "unknown"
    enclosure_exposure  : str  = "unknown"
    accessibility       : str  = "unknown"
    street_activity     : str  = "unknown"

    @field_validator("*", mode="before")
    @classmethod
    def _coerce(cls, v, info: FieldValidationInfo):
        if info.field_name == "scene_context":
            if isinstance(v, dict):
                return {qk: str(v.get(qk, "unknown")).strip() or "unknown"
                        for qk in _QUADRANT_KEYS}
            s = str(v).strip() if v else "unknown"
            return s or "unknown"
        if isinstance(v, list):
            return ", ".join(str(x).strip() for x in v if str(x).strip()) or "unknown"
        if isinstance(v, dict):
            return str(v)
        s = str(v).strip() if v else "unknown"
        return s if s else "unknown"


_KEY_ALIASES = {
    # scene_context
    "scene_context": "scene_context", "scene context": "scene_context",
    "context": "scene_context", "overview": "scene_context",
    "scene_overview": "scene_context", "scene overview": "scene_context",
    "scene": "scene_context", "description": "scene_context",
    # perceived_safety
    "perceived_safety": "perceived_safety", "perceived safety": "perceived_safety",
    "safety": "perceived_safety", "security": "perceived_safety",
    "crime": "perceived_safety", "danger": "perceived_safety",
    # visibility (sightlines, daytime visual clarity)
    "visibility": "visibility", "visual_clarity": "visibility",
    "visual clarity": "visibility", "sightlines": "visibility",
    # lighting_quality (artificial + natural light comfort)
    "lighting_quality": "lighting_quality", "lighting quality": "lighting_quality",
    "lighting": "lighting_quality", "light": "lighting_quality",
    "illumination": "lighting_quality",
    # cleanliness
    "cleanliness": "cleanliness", "clean": "cleanliness",
    "litter": "cleanliness", "maintenance": "cleanliness",
    "upkeep": "cleanliness", "disorder": "cleanliness",
    # greenery
    "greenery": "greenery", "greenery_comfort": "greenery",
    "greenery comfort": "greenery", "vegetation": "greenery",
    "green": "greenery", "nature": "greenery", "biophilia": "greenery",
    # thermal_comfort
    "thermal_comfort": "thermal_comfort", "thermal comfort": "thermal_comfort",
    "comfort": "thermal_comfort", "shade": "thermal_comfort",
    "microclimate": "thermal_comfort", "weather_comfort": "thermal_comfort",
    # walkability
    "walkability": "walkability", "walking": "walkability",
    "pedestrian_quality": "walkability", "pedestrian quality": "walkability",
    "pavement_quality": "walkability", "pavement quality": "walkability",
    # noise_comfort
    "noise_comfort": "noise_comfort", "noise comfort": "noise_comfort",
    "noise_indicators": "noise_comfort", "noise indicators": "noise_comfort",
    "noise": "noise_comfort", "sound": "noise_comfort",
    "traffic_noise": "noise_comfort", "traffic noise": "noise_comfort",
    "auditory_comfort": "noise_comfort",
    # crowding
    "crowding": "crowding", "crowd": "crowding",
    "density": "crowding", "congestion": "crowding",
    "pedestrian_density": "crowding", "pedestrian density": "crowding",
    # privacy
    "privacy": "privacy", "exposure": "privacy",
    "overlooking": "privacy", "surveillance": "privacy",
    # social_potential
    "social_potential": "social_potential", "social potential": "social_potential",
    "social": "social_potential", "interaction": "social_potential",
    "gathering": "social_potential", "lingering": "social_potential",
    # visual_interest
    "visual_interest": "visual_interest", "visual interest": "visual_interest",
    "interest": "visual_interest", "complexity": "visual_interest",
    "imageability": "visual_interest", "visual_complexity": "visual_interest",
    # enclosure_exposure
    "enclosure_exposure": "enclosure_exposure", "enclosure exposure": "enclosure_exposure",
    "enclosure": "enclosure_exposure", "spatial_enclosure": "enclosure_exposure",
    "openness": "enclosure_exposure",
    # accessibility
    "accessibility": "accessibility", "access": "accessibility",
    "barrier_free": "accessibility", "barrier free": "accessibility",
    "universal_design": "accessibility", "universal design": "accessibility",
    # street_activity
    "street_activity": "street_activity", "street activity": "street_activity",
    "activity": "street_activity", "liveliness": "street_activity",
    "vitality": "street_activity", "uses": "street_activity",
}


def _normalise_result(raw: dict) -> dict:
    flat = {}
    for k, v in raw.items():
        canonical_k = _KEY_ALIASES.get(k.strip().lower(), "")
        if canonical_k == "scene_context":
            flat["scene_context"] = v  # preserve quadrant dict as-is
        elif isinstance(v, dict) and canonical_k != "scene_context":
            flat.update(v)
        else:
            flat[k] = v
    normalised = {}
    for k, v in flat.items():
        canonical = _KEY_ALIASES.get(k.strip().lower())
        if canonical:
            normalised[canonical] = v
        elif k == "scene_context":
            normalised["scene_context"] = v
    try:
        return StreetSceneAnalysis(**normalised).model_dump()
    except Exception:
        return StreetSceneAnalysis().model_dump()


# ---------------------------------------------------------------------------
# PLM prompt
# ---------------------------------------------------------------------------

_SCENE_PROMPT = (
    "You are an urban comfort analyst studying a street-view photograph "
    "from Barcelona's Eixample district.\n"
    "\n"
    "Your task: assess how COMFORTABLE, SAFE, and PLEASANT this street feels "
    "for an individual walking through it. Focus only on perceptual qualities — "
    "things a person on foot would feel or notice. Do NOT describe architecture "
    "or building typology.\n"
    "\n"
    "IMPORTANT — scene_context: Mentally divide the image into 4 quadrants "
    "(top-left, top-right, bottom-left, bottom-right). Write ONE sentence per "
    "quadrant describing what is there and how it affects individual comfort. "
    "Return scene_context as a JSON object with keys: top_left, top_right, "
    "bottom_left, bottom_right.\n"
    "\n"
    "For all other fields write 1-3 full sentences. Reference spatial positions "
    "(left, right, foreground, background) where relevant.\n"
    "\n"
    "Return ONLY a JSON object with exactly these 16 keys:\n"
    "{\n"
    '  "scene_context": {\n'
    '    "top_left":     "One sentence: what is in the top-left quadrant and '
    'how does it affect comfort/mood?",\n'
    '    "top_right":    "One sentence: what is in the top-right quadrant and '
    'how does it affect comfort/mood?",\n'
    '    "bottom_left":  "One sentence: what is in the bottom-left quadrant '
    '(ground level, left side) and how does it affect walking comfort?",\n'
    '    "bottom_right": "One sentence: what is in the bottom-right quadrant '
    '(ground level, right side) and how does it affect walking comfort?"\n'
    '  },\n'
    '  "perceived_safety": "How safe does this street feel? Note sightlines, '
    'eyes on the street (active windows, shops), hiding spots, signs of '
    'vandalism or neglect, and whether the space feels supervised or isolated.",\n'
    '  "visibility": "How far and clearly can a pedestrian see? Note sightline '
    'depth, visual obstructions (parked vehicles, scaffolding, bends), daytime '
    'clarity, and whether the environment feels legible and easy to navigate.",\n'
    '  "lighting_quality": "Assess artificial and natural light comfort: '
    'streetlight presence and spacing, shopfront glow, shadow pools, dark '
    'corners or underpasses, and overall illumination quality for pedestrians.",\n'
    '  "cleanliness": "Rate visual cleanliness: litter, graffiti, stained '
    'surfaces, overflowing bins, construction debris, or conversely -- '
    'well-maintained surfaces and tidy shopfronts.",\n'
    '  "greenery": "How does vegetation contribute to individual comfort? Note '
    'tree canopy maturity and coverage, planter boxes, ground cover, whether '
    'trees shade the sidewalk, and the overall sensory relief green provides.",\n'
    '  "thermal_comfort": "How thermally comfortable is this street? Note shade '
    'from trees or awnings, sun-exposed stretches, building shadows, wind '
    'indicators (flags, awnings), and overall thermal protection for a walker.",\n'
    '  "walkability": "How easy and safe is it to walk here? Note pavement '
    'condition, surface evenness, obstacles (parked vehicles, poles, bins), '
    'curb cuts, sidewalk width relative to use, and any tripping hazards.",\n'
    '  "noise_comfort": "Infer acoustic comfort from visual cues: traffic '
    'volume, construction activity, outdoor dining, narrow vs wide streets, '
    'sound-reflecting surfaces, and any noise barriers, trees, or buffers.",\n'
    '  "crowding": "How crowded or empty does this street feel? Estimate '
    'pedestrian density, available walking space, bottlenecks, and whether '
    'it feels comfortably populated, uncomfortably dense, or deserted.",\n'
    '  "privacy": "How exposed or private does a pedestrian feel? Note '
    'overlooking windows, balconies, CCTV cameras, open vs enclosed space, '
    'and whether one feels watched or anonymous.",\n'
    '  "social_potential": "Are there places to stop, sit, linger, or meet? '
    'Note benches, cafe terraces, ledges, steps, plazas, and whether the '
    'street encourages social interaction or only pass-through movement.",\n'
    '  "visual_interest": "Is there variety and complexity to look at? Note '
    'facade diversity, colour, art, window displays, views, or conversely -- '
    'monotony, blank walls, repetitive surfaces at eye level.",\n'
    '  "enclosure_exposure": "Does the street feel spatially contained and '
    'intimate, or wide-open and exposed? Note building height vs street width, '
    'sky visibility, canopy ceiling effect, and whether one feels sheltered.",\n'
    '  "accessibility": "How accessible is this street for all users? Note '
    'ramps, tactile paving, bollard spacing, step-free paths, wheelchair '
    'passability, and any barriers to mobility-impaired pedestrians.",\n'
    '  "street_activity": "What activities are visible? Distinguish necessary '
    '(commuting), optional (sitting, browsing), and social (talking, eating '
    'outdoors) activities, and note their effect on the street atmosphere."\n'
    "}\n"
    "\n"
    "IMPORTANT: Describe what you SEE and what it implies for individual comfort. "
    "No architecture descriptions -- only perceptual and comfort qualities.\n"
    "No markdown, no explanation -- output ONLY the JSON object."
)


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

def _parse_scene_json(text: str) -> dict:
    candidate = None
    for attempt in (text, text + "}"):
        try:
            candidate = json.loads(attempt)
            break
        except json.JSONDecodeError:
            pass
    if candidate is None:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                candidate = json.loads(m.group())
            except json.JSONDecodeError:
                pass
    if candidate is None and "{" in text:
        truncated = text.strip().rstrip(",")
        opens = truncated.count("{") - truncated.count("}")
        if opens > 0:
            try:
                candidate = json.loads(truncated + "}" * opens)
            except json.JSONDecodeError:
                pass
        if candidate is None:
            stripped = re.sub(r',\s*"[^"]*$', "", truncated)
            opens2 = stripped.count("{") - stripped.count("}")
            if opens2 >= 0:
                try:
                    candidate = json.loads(stripped + "}" * opens2)
                except json.JSONDecodeError:
                    pass
    if candidate is None:
        pairs = {}
        for m in re.finditer(r'"([^"]+)"\s*:\s*"([^"]*)"', text):
            pairs[m.group(1)] = m.group(2)
        if pairs:
            candidate = pairs
    if candidate and isinstance(candidate, dict):
        return _normalise_result(candidate)
    return StreetSceneAnalysis().model_dump()


# ---------------------------------------------------------------------------
# Model globals (populated by load_model())
# ---------------------------------------------------------------------------

_model     = None
_processor = None
_device    = "cpu"
_dtype     = torch.float32


def load_model():
    global _model, _processor, _device, _dtype, MAX_NUM_TILES

    login(token=HF_TOKEN, add_to_git_credential=False)

    _device = "cuda" if torch.cuda.is_available() else "cpu"
    _dtype  = torch.bfloat16 if _device == "cuda" else torch.float32

    if _device == "cuda":
        vram_gb = torch.cuda.get_device_properties(0).total_memory / 1e9
        log.info("GPU: %s  (%.1f GB)", torch.cuda.get_device_name(0), vram_gb)
        # Scale tile budget to VRAM — PLM-1B peak ~1.5 GB/tile + ~3 GB base
        if vram_gb >= 20:
            MAX_NUM_TILES = 4   # L4 / A100 — keep at 4 for speed
        elif vram_gb >= 16:
            MAX_NUM_TILES = 4   # T4 / similar
        elif vram_gb >= 10:
            MAX_NUM_TILES = 4
        else:
            MAX_NUM_TILES = 2
        os.environ.setdefault("PYTORCH_ALLOC_CONF", "expandable_segments:True")
    else:
        log.warning("No GPU detected -- inference will be very slow on CPU")
        MAX_NUM_TILES = 1

    log.info("Tile budget: %d", MAX_NUM_TILES)
    log.info("Loading %s ...", MODEL_ID)

    _processor = AutoProcessor.from_pretrained(MODEL_ID, token=HF_TOKEN, use_fast=True)
    _processor.image_processor.max_num_tiles = MAX_NUM_TILES

    _model = AutoModelForImageTextToText.from_pretrained(
        MODEL_ID,
        token             = HF_TOKEN,
        torch_dtype       = _dtype,
        device_map        = "auto",
        low_cpu_mem_usage = True,
    )
    # PLM omits lm_head.weight -- manually bridge to embed_tokens
    _model.lm_head.weight = _model.model.language_model.embed_tokens.weight
    _model.eval()

    if _device == "cuda":
        torch.cuda.empty_cache()
        gc.collect()

    log.info("PerceptionLM-1B loaded and ready")


# ---------------------------------------------------------------------------
# PLM inference
# ---------------------------------------------------------------------------

def _infer_scene(image):
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image"},
                {"type": "text", "text": _SCENE_PROMPT},
            ],
        }
    ]
    text = _processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    text += "{"
    inputs = _processor(images=image, text=text, return_tensors="pt")

    log.debug("Processor output keys: %s", list(inputs.keys()))
    for k in list(inputs.keys()):
        v = inputs[k]
        if hasattr(v, "to"):
            inputs[k] = v.to(_device, dtype=_dtype) if v.is_floating_point() else v.to(_device)

    with torch.no_grad():
        gen_ids = _model.generate(
            **inputs,
            max_new_tokens     = MAX_NEW_TOKENS,
            eos_token_id       = _processor.tokenizer.eos_token_id,
            pad_token_id       = _processor.tokenizer.pad_token_id,
            repetition_penalty = 1.1,
        )

    new_ids  = gen_ids[:, inputs["input_ids"].shape[1]:]
    raw_text = "{" + _processor.tokenizer.batch_decode(new_ids, skip_special_tokens=True)[0]

    if _device == "cuda":
        del gen_ids, new_ids, inputs
        torch.cuda.empty_cache()
        gc.collect()

    return _parse_scene_json(raw_text), raw_text


def analyze_image(image_path):
    """
    Run PerceptionLM-1B on a full street-view image.
    Returns a dict with scene analysis fields and _latency_ms.
    """
    img = _PILImage.open(image_path).convert("RGB")
    t0  = time.perf_counter()
    result, raw = _infer_scene(img)
    latency_ms  = (time.perf_counter() - t0) * 1000

    populated = sum(
        1 for k, v in result.items()
        if v not in ("unknown", "", None) and not k.startswith("_")
    )
    total = sum(1 for k in result if not k.startswith("_"))
    log.info("  Scene analysis: %d ms  %d/%d fields populated", int(latency_ms), populated, total)
    log.debug("  Raw output (%d chars): %r", len(raw), raw[:150])

    result["_latency_ms"] = round(latency_ms, 1)
    return result


# ---------------------------------------------------------------------------
# Street View helpers
# ---------------------------------------------------------------------------

def sv_available(lat: float, lon: float) -> bool:
    r = requests.get(_META_BASE, params={
        "location": f"{lat},{lon}",
        "radius"  : SV_RADIUS,
        "key"     : STREETVIEW_API_KEY,
    }, timeout=10)
    return r.status_code == 200 and r.json().get("status") == "OK"


def fetch_sv(lat: float, lon: float, heading: float, images_dir: Path):
    """Download one Street View image. Returns local Path or None."""
    fname = f"sv_{lat:.6f}_{lon:.6f}_h{int(heading)}.jpg"
    fpath = images_dir / fname

    if fpath.exists():
        return fpath
    if not sv_available(lat, lon):
        return None

    try:
        r = requests.get(_SV_BASE, params={
            "size"    : SV_SIZE,
            "location": f"{lat},{lon}",
            "heading" : heading,
            "pitch"   : SV_PITCH,
            "fov"     : SV_FOV,
            "key"     : STREETVIEW_API_KEY,
        }, timeout=30)
        r.raise_for_status()
        if len(r.content) < 5_120:  # Google grey placeholder < 5 KB
            return None
        fpath.write_bytes(r.content)
        return fpath
    except Exception as exc:
        log.warning("SV fetch error (%s,%s): %s", lat, lon, exc)
        return None


# ---------------------------------------------------------------------------
# BigQuery / point sampling
# ---------------------------------------------------------------------------

def query_and_sample_points(output_root: Path) -> list:
    """Query BigQuery for walk edges and sample points. Caches to disk."""
    points_file = output_root / "sample_points.json"

    if points_file.exists():
        with open(points_file, encoding="utf-8") as f:
            pts = json.load(f)
        log.info("Re-using %d saved sample points from %s", len(pts), points_file)
        return pts

    log.info("Querying Overture Maps walk edges from BigQuery ...")
    bq_client = bigquery.Client(project=GCP_PROJECT_ID)

    bq_query = f"""
    SELECT
        id,
        ST_AsText(geometry) AS wkt,
        names.primary       AS name,
        subtype             AS road_type,
        class               AS road_class
    FROM `{BIGQUERY_PROJECT}.{OVERTURE_DATASET}.segment`
    WHERE (subtype = 'pedestrian'
           OR class IN ('pedestrian', 'footway', 'path', 'steps'))
      AND bbox.xmin >= {BBOX["min_lon"]}
      AND bbox.ymin >= {BBOX["min_lat"]}
      AND bbox.xmax <= {BBOX["max_lon"]}
      AND bbox.ymax <= {BBOX["max_lat"]}
    """

    df = bq_client.query(bq_query).to_dataframe()
    log.info("%d walk edges returned from BigQuery", len(df))

    df["geometry"] = df["wkt"].apply(shapely_wkt.loads)
    gdf_edges  = gpd.GeoDataFrame(df, geometry="geometry", crs="EPSG:4326")
    gdf_proj   = gdf_edges.to_crs(UTM31N)
    transformer = pyproj.Transformer.from_crs(UTM31N, "EPSG:4326", always_xy=True)

    sample_points = []
    seen_cells    = set()

    for _, row in gdf_proj.iterrows():
        geom    = row.geometry
        length  = geom.length
        n_steps = max(1, int(length / SAMPLE_DISTANCE_M))

        for i in range(n_steps + 1):
            dist   = min(i * SAMPLE_DISTANCE_M, length)
            p_proj = geom.interpolate(dist)

            offset = 1.0 if dist + 1.0 < length else -1.0
            p2     = geom.interpolate(dist + offset)
            dx, dy = p2.x - p_proj.x, p2.y - p_proj.y
            heading = (np.degrees(np.arctan2(dx, dy)) + 360) % 360

            lon, lat = transformer.transform(p_proj.x, p_proj.y)

            cell_key = (round(lat, 4), round(lon, 4))
            if cell_key in seen_cells:
                continue
            seen_cells.add(cell_key)

            name = row["name"] if isinstance(row["name"], str) else ""
            rt   = row["road_type"] if isinstance(row["road_type"], str) else ""

            sample_points.append({
                "id"               : f"{lat:.6f}_{lon:.6f}",
                "lat"              : lat,
                "lon"              : lon,
                "heading"          : heading,
                "street_name"      : name,
                "highway_type"     : rt,
                "edge_id"          : row["id"],
                "dist_along_edge_m": round(dist, 1),
            })

    points_file.write_text(
        json.dumps(sample_points, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    log.info("Sampled %d points -> %s", len(sample_points), points_file)
    return sample_points


# ---------------------------------------------------------------------------
# Trial run
# ---------------------------------------------------------------------------

def run_trial(args, images_dir: Path):
    if args.trial_lat is not None and args.trial_lon is not None:
        pt = {
            "id"               : f"{args.trial_lat:.6f}_{args.trial_lon:.6f}",
            "lat"              : args.trial_lat,
            "lon"              : args.trial_lon,
            "heading"          : args.trial_heading or 0.0,
            "street_name"      : "manual",
            "highway_type"     : "unknown",
            "edge_id"          : "manual",
            "dist_along_edge_m": None,
        }
    else:
        sample_points = query_and_sample_points(images_dir.parent)
        if not sample_points:
            log.error("No sample points available.")
            return
        idx = args.trial_index or 0
        pt  = dict(sample_points[idx % len(sample_points)])
        if args.trial_heading is not None:
            pt["heading"] = args.trial_heading

    log.info("Trial point: %s  (%.6f, %.6f)  heading %.1f°",
             pt["street_name"], pt["lat"], pt["lon"], pt["heading"])

    img_path = fetch_sv(pt["lat"], pt["lon"], pt["heading"], images_dir)
    if img_path is None:
        log.warning("No Street View coverage at this location.")
        return

    log.info("Image saved: %s", img_path)
    t0  = time.time()
    res = analyze_image(img_path)
    log.info("PLM latency: %d ms", round((time.time() - t0) * 1000))

    comfort_fields = [
        "perceived_safety", "visibility", "lighting_quality", "cleanliness",
        "greenery", "thermal_comfort", "walkability", "noise_comfort",
        "crowding", "privacy", "social_potential", "visual_interest",
        "enclosure_exposure", "accessibility", "street_activity",
    ]

    print("\n--- Scene Context (4 Quadrants) ---")
    sc = res.get("scene_context", {})
    if isinstance(sc, dict):
        for qk in _QUADRANT_KEYS:
            print(f"  {qk:15s}: {sc.get(qk, 'unknown')}")
    else:
        print(f"  {sc}")

    print("\n--- Individual Comfort Criteria ---")
    for k in comfort_fields:
        print(f"  {k:22s}: {res.get(k, 'unknown')}")
    print()


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def _result_path(results_dir: Path, point_id: str) -> Path:
    return results_dir / f"{point_id}_analysis.json"


def run_pipeline(sample_points: list, images_dir: Path, results_dir: Path):
    pending = [pt for pt in sample_points if not _result_path(results_dir, pt["id"]).exists()]
    done    = len(sample_points) - len(pending)

    log.info("Total sample points : %d", len(sample_points))
    log.info("Already completed   : %d", done)
    log.info("Remaining           : %d", len(pending))

    if not pending:
        log.info("All points already analysed -- nothing to do.")
        return

    stats = {"fetched": 0, "no_sv": 0, "analysed": 0, "errors": 0}

    for pt in tqdm(pending, desc="Analysing", unit="loc"):
        try:
            img_path = fetch_sv(pt["lat"], pt["lon"], pt["heading"], images_dir)

            if img_path is None:
                stats["no_sv"] += 1
                stub = {
                    "metadata": {
                        "timestamp"         : datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
                        "latitude"          : pt["lat"],
                        "longitude"         : pt["lon"],
                        "heading"           : pt["heading"],
                        "street_name"       : pt["street_name"],
                        "highway_type"      : pt["highway_type"],
                        "edge_id"           : pt["edge_id"],
                        "dist_along_edge_m" : pt["dist_along_edge_m"],
                        "source_image"      : None,
                        "model"             : MODEL_ID,
                        "device"            : _device,
                        "status"            : "no_streetview",
                    },
                    "scene_analysis": None,
                }
                _result_path(results_dir, pt["id"]).write_text(
                    json.dumps(stub, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                continue

            stats["fetched"] += 1
            time.sleep(0.25)

            scene_result = analyze_image(img_path)
            stats["analysed"] += 1

            record = {
                "metadata": {
                    "timestamp"         : datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
                    "latitude"          : pt["lat"],
                    "longitude"         : pt["lon"],
                    "heading"           : pt["heading"],
                    "street_name"       : pt["street_name"],
                    "highway_type"      : pt["highway_type"],
                    "edge_id"           : pt["edge_id"],
                    "dist_along_edge_m" : pt["dist_along_edge_m"],
                    "source_image"      : img_path.name,
                    "model"             : MODEL_ID,
                    "device"            : _device,
                    "latency_ms"        : scene_result.pop("_latency_ms", None),
                    "status"            : "ok",
                },
                "scene_analysis": scene_result,
            }
            _result_path(results_dir, pt["id"]).write_text(
                json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
            )

        except Exception as exc:
            log.error("Error processing %s: %s", pt["id"], exc)
            stats["errors"] += 1

    log.info("Pipeline complete: %s", stats)


def print_summary(results_dir: Path):
    result_files = sorted(results_dir.glob("*_analysis.json"))
    log.info("Total JSON files: %d", len(result_files))
    ok_count = no_sv_count = err_count = 0
    for rf in result_files:
        try:
            rec    = json.loads(rf.read_text(encoding="utf-8"))
            status = rec["metadata"].get("status", "ok")
            if status == "ok":
                ok_count += 1
            elif status == "no_streetview":
                no_sv_count += 1
            else:
                err_count += 1
        except Exception:
            err_count += 1
    log.info("  Analysed with PLM : %d", ok_count)
    log.info("  No Street View    : %d", no_sv_count)
    log.info("  Parse / IO errors : %d", err_count)

    ok_files = [
        f for f in result_files
        if json.loads(f.read_text(encoding="utf-8"))["metadata"].get("status") == "ok"
    ]
    if ok_files:
        rec  = json.loads(ok_files[-1].read_text(encoding="utf-8"))
        meta = rec["metadata"]
        log.info("Most recent successful result:")
        log.info("  File     : %s", ok_files[-1].name)
        log.info("  Location : %.6f, %.6f", meta["latitude"], meta["longitude"])
        log.info("  Street   : %s (%s)", meta["street_name"], meta["highway_type"])
        log.info("  Heading  : %.1f°", meta["heading"])
        log.info("  Latency  : %s ms", meta.get("latency_ms"))
    log.info("Each JSON contains geocoordinates + edge_id for direct join "
             "with Overture Maps walk_edges table in DuckDB.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _parse_args():
    p = argparse.ArgumentParser(
        description="StreetPLM Eixample -- LightningAI job version"
    )
    p.add_argument(
        "--output-dir",
        default=os.environ.get(
            "OUTPUT_DIR",
            "/teamspace/studios/this_studio/StreetPLM",
        ),
        help="Root directory for images/ and results/ output",
    )
    p.add_argument(
        "--trial",
        action="store_true",
        help="Run one location only (diagnostic, no results written)",
    )
    p.add_argument("--trial-index",  type=int,   default=0,    help="Index into sample_points list")
    p.add_argument("--trial-lat",    type=float, default=None, help="Manual latitude override")
    p.add_argument("--trial-lon",    type=float, default=None, help="Manual longitude override")
    p.add_argument("--trial-heading",type=float, default=None, help="Manual heading override (0-360)")
    return p.parse_args()


def main():
    args = _parse_args()

    assert STREETVIEW_API_KEY, "Set GOOGLE_STREETVIEW_API_KEY env var (Lightning AI Secret)"
    assert HF_TOKEN,           "Set HF_TOKEN env var (Lightning AI Secret)"
    assert GCP_PROJECT_ID,     "Set GCP_PROJECT_ID env var (Lightning AI Secret)"

    output_root = Path(args.output_dir)
    images_dir  = output_root / "images"
    results_dir = output_root / "results"
    for d in (images_dir, results_dir):
        d.mkdir(parents=True, exist_ok=True)

    log.info("Output root : %s", output_root)
    log.info("BBOX        : %s", BBOX)
    log.info("Sample step : %d m", SAMPLE_DISTANCE_M)
    log.info("GCP project : %s", GCP_PROJECT_ID)
    log.info("Model       : %s", MODEL_ID)

    load_model()

    if args.trial:
        run_trial(args, images_dir)
        return

    sample_points = query_and_sample_points(output_root)
    run_pipeline(sample_points, images_dir, results_dir)
    print_summary(results_dir)


if __name__ == "__main__":
    main()
