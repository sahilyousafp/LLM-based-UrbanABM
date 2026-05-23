"""
street_plm_job.py
=================
LightningAI Job version of StreetPLM_Eixample.ipynb.

What this script does:
  1. Queries Overture Maps pedestrian walk edges from BigQuery for the
     Eixample study area
  2. Samples points every 250 m along each walk edge, heading aligned with street direction
  3. Checks Street View availability and fetches one 640x640 image per sample point
  4. Runs Qwen2.5-VL-3B-Instruct on each image for structured observation analysis
  5. Saves one JSON per location to OUTPUT_DIR
     (default /teamspace/studios/this_studio/StreetPLM)

Required environment variables (set as Lightning AI Secrets):
  GOOGLE_STREETVIEW_API_KEY  -- Google Maps Platform Street View Static API key
  HF_TOKEN                   -- Hugging Face token with access to Qwen/Qwen2.5-VL-3B-Instruct
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
  python street_plm_job.py --trial --trial-lat 41.3917 --trial-lon 2.1654
"""

import argparse
import gc
import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
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

# Bootstrap qwen-vl-utils
try:
    import qwen_vl_utils  # noqa: F401
except ImportError:
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-q", "qwen-vl-utils"]
    )

# Bootstrap bitsandbytes for 4-bit quantization
try:
    import bitsandbytes  # noqa: F401
except ImportError:
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-q", "bitsandbytes"]
    )

import numpy as np
import pyproj
import requests
import torch
from google.cloud import bigquery
from huggingface_hub import login
from PIL import Image as _PILImage
from pydantic import BaseModel, field_validator
from shapely import wkt as shapely_wkt
from shapely.geometry import LineString  # noqa: F401
import geopandas as gpd
from tqdm.auto import tqdm
from dotenv import load_dotenv
from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
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

# Bounding box -- focused area near Casa Battló
# Mirrors Backend/Environment/overture_to_duckdb.py
# OLD BBOX (2 km x 2 km centred on Passeig de Gracia):
# BBOX = {
#     "min_lon": 2.1500,
#     "min_lat": 41.3862,
#     "max_lon": 2.1740,
#     "max_lat": 41.4042,
# }
BBOX = {
    "min_lon": 2.166667,
    "min_lat": 41.396468,
    "max_lon": 2.172096,
    "max_lat": 41.399895,
}

BIGQUERY_PROJECT  = "bigquery-public-data"
OVERTURE_DATASET  = "overture_maps"

SAMPLE_DISTANCE_M = 250
SV_SIZE           = "640x640"
SV_FOV            = 90
SV_PITCH          = 0
SV_RADIUS         = 50
MODEL_ID          = "Qwen/Qwen2.5-VL-3B-Instruct"
MAX_NEW_TOKENS    = 500  # Increased to prevent truncation of structured JSON output
USE_4BIT          = False  # 4-bit quantization disabled due to bitsandbytes initialization issues

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")
os.environ.setdefault("HF_HOME", "/teamspace/studios/this_studio/.hf")

UTM31N = "EPSG:32631"

_SV_BASE   = "https://maps.googleapis.com/maps/api/streetview"
_META_BASE = "https://maps.googleapis.com/maps/api/streetview/metadata"

# ---------------------------------------------------------------------------
# Signal handling for graceful shutdown
# ---------------------------------------------------------------------------
_shutdown_requested = False

def _handle_signal(signum, frame):
    global _shutdown_requested
    log.warning("Signal %s received — finishing current image then stopping.", signum)
    _shutdown_requested = True

signal.signal(signal.SIGINT,  _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)

# ---------------------------------------------------------------------------
# Structured observation schema
# ---------------------------------------------------------------------------

STORAGE_KEYWORDS = {
    "path_obstruction", "vehicle_traffic", "ground_surface",
    "enclosure", "greenery", "lighting", "maintenance",
    "seating", "pedestrian_activity", "commercial_activity",
    "facade_complexity",
}

VALID_PANELS = {"L", "CL", "CR", "R", "CL-CR", "L-CL", "CR-R", "all"}

AGENT_MERGE = {
    "path_condition":  {"path_obstruction", "vehicle_traffic", "ground_surface"},
    "enclosure":       {"enclosure"},
    "greenery":        {"greenery"},
    "lighting":        {"lighting"},
    "built_character": {"maintenance", "facade_complexity"},
    "people_activity": {"seating", "pedestrian_activity", "commercial_activity"},
}

ARCHETYPE_FEATURES = {
    "tourist":  {"enclosure", "greenery", "lighting", "built_character", "people_activity"},
    "commuter": {"path_condition", "enclosure", "lighting", "people_activity"},
    "resident": {"path_condition", "enclosure", "greenery", "lighting",
                 "built_character", "people_activity"},
    "student":  {"enclosure", "greenery", "lighting", "built_character", "people_activity"},
}


class Observation(BaseModel):
    feature:    str
    element:    str
    panel:      str
    descriptor: str

    @field_validator("feature", mode="before")
    @classmethod
    def _f(cls, v):
        s = str(v).strip().lower().replace(" ", "_")
        return s if s in STORAGE_KEYWORDS else "enclosure"

    @field_validator("panel", mode="before")
    @classmethod
    def _p(cls, v):
        s = str(v).strip().upper().replace(" ", "")
        return s if s in VALID_PANELS else "CL"

    @field_validator("element", "descriptor", mode="before")
    @classmethod
    def _s(cls, v):
        return str(v).strip() if v else "unknown"

# ---------------------------------------------------------------------------
# PLM prompt
# ---------------------------------------------------------------------------

_KW = ", ".join(sorted(STORAGE_KEYWORDS))

_SCENE_PROMPT = f"""\
Analyse this 640px street-view image from Barcelona's Eixample district.
Zones: L (left 25%), CL (centre-left 25%), CR (centre-right 25%), R (right 25%).
At junctions: L/R show the cross street; CL/CR show the path ahead.

RULES — follow exactly:
1. Only report what you can directly see. Do not invent.
2. Assign the SPECIFIC zone where the element appears. Use "all" only if
   the element genuinely spans the full width (e.g. overcast sky).
3. element: name the specific object. If a shop sign is legible, write the
   name. Write "yellow cherry-picker" not "equipment". Write "Massimo Dutti
   shopfront" not "store". Be as specific as the image allows.
4. descriptor: describe character, not just presence. Write "ornate limestone,
   balconies, columns" not "well-maintained". Write "blocks left corridor
   width" not "obstructing". A reader must be able to picture the specific
   thing from descriptor alone.
5. You MUST check every panel including R. If R contains a building facade,
   report it under facade_complexity. Do not skip edge panels.
6. Do not use "all" for panel unless the element truly spans all four zones.

Keywords (use ONLY these): {_KW}

Keyword meanings:
  path_obstruction   — static objects narrowing or blocking CL or CR
  vehicle_traffic    — vehicles in road or lane panels
  ground_surface     — pavement material, pattern, condition
  enclosure          — sky ratio, building canyon vs open boulevard feeling
  greenery           — trees, plants, shade — note species if recognisable
  lighting           — sun angle, shadow direction, artificial lights
  maintenance        — graffiti, damage, peeling paint, construction activity
  seating            — benches, outdoor chairs, steps used as seats
  pedestrian_activity — what people are doing, count, pace
  commercial_activity — named shops, cafes, vendors — use the visible name
  facade_complexity  — architectural richness: balconies, ornament, columns,
                       window rhythm, stonework detail

For each visible feature:
  feature    — one keyword from the list above
  element    — specific named object (use visible text/name if present, <=5 words)
  panel      — exact zone: L / CL / CR / R / CL-CR / L-CL / CR-R
               (NOT "all" unless genuinely full-width)
  descriptor — specific character (<=8 words, must be imageable not generic)

Return ONLY this JSON — scalars INSIDE the root object alongside observations:
{{
  "observations": [
    {{"feature":"...","element":"...","panel":"...","descriptor":"..."}},
    ...
  ],
  "openness":    <1=tunnel/no sky, 3=partial sky, 5=wide open boulevard>,
  "crowdedness": <1=empty, 3=few people, 5=packed>,
  "passable":    <true if CL and CR are walkable, false if physically blocked>
}}"""

# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

def _try_load(text: str):
    """
    Robust JSON loader with fixes for:
    1. Markdown code fences (```json ... ```)
    2. Truncated JSON
    3. Scalars written outside observations closing brace (VLM structural error)

    VLM outputs:
        {"observations":[...]},
        "openness": 1,
        ...

    This reconstructs valid JSON from that structure.
    """
    # Fix 1: Strip markdown code fences (may be incomplete if output is truncated)
    text = re.sub(r'^```[a-z]*\s*\n?', '', text)  # Remove opening fence
    text = re.sub(r'\n?```\s*$', '', text)  # Remove closing fence if present
    text = text.strip()

    # Fix 2: Try direct parse first (for well-formed output)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fix 3: Extract observations array and scalars separately, then recombine
    # Pattern: {"observations":[...]} followed by separate "key": value lines
    obs_match = re.search(r'"observations"\s*:\s*\[[\s\S]*?\]', text)
    if obs_match:
        obs_str = obs_match.group()

        # Extract scalar values from the remaining text
        remaining = text[obs_match.end():]
        openness = 3
        crowdedness = 3
        passable = True

        # Parse scalar values (allowing for various whitespace)
        open_m = re.search(r'"openness"\s*:\s*(\d+)', remaining)
        if open_m:
            openness = int(open_m.group(1))

        crowd_m = re.search(r'"crowdedness"\s*:\s*(\d+)', remaining)
        if crowd_m:
            crowdedness = int(crowd_m.group(1))

        pass_m = re.search(r'"passable"\s*:\s*(true|false)', remaining, re.IGNORECASE)
        if pass_m:
            passable = pass_m.group(1).lower() == 'true'

        # Reconstruct valid JSON
        reconstructed = (
            '{' + obs_str +
            f', "openness": {openness}' +
            f', "crowdedness": {crowdedness}' +
            f', "passable": {str(passable).lower()}' +
            '}'
        )
        try:
            return json.loads(reconstructed)
        except json.JSONDecodeError:
            pass

    # Fix 4: Try adding various closing brackets (for truncated output)
    for suffix in ["", "]", "}", "]}",  "}]"]:
        try:
            return json.loads(text + suffix)
        except json.JSONDecodeError:
            pass

    # Fix 5: Extract outermost { ... }
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass

    # Fix 6: Brace-balance repair as last resort
    s = text.strip().rstrip(",")
    s += "]" * max(0, s.count("[") - s.count("]"))
    s += "}" * max(0, s.count("{") - s.count("}"))
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


def _parse_scene_json(raw: str) -> tuple[list[dict], dict]:
    """Returns (observations_list, scalars_dict)."""
    data = _try_load(raw)
    if not data:
        log.warning("Failed to parse VLM output. Raw text (first 500 chars):\n%s", raw[:500])
        return [], {"openness": 3, "crowdedness": 3, "passable": True}
    obs = []
    for o in data.get("observations", []):
        if isinstance(o, dict):
            try:
                obs.append(Observation(**o).model_dump())
            except Exception:
                pass
    scalars = {
        "openness":    max(1, min(5, int(data.get("openness",    3)))),
        "crowdedness": max(1, min(5, int(data.get("crowdedness", 3)))),
        "passable":    bool(data.get("passable", True)),
    }
    return obs, scalars

# ---------------------------------------------------------------------------
# Model globals (populated by load_model())
# ---------------------------------------------------------------------------

_model     = None
_processor = None
_device    = "cpu"


def load_model():
    global _model, _processor, _device

    login(token=HF_TOKEN, add_to_git_credential=False)

    _device = "cuda" if torch.cuda.is_available() else "cpu"
    if _device == "cuda":
        vram_gb = torch.cuda.get_device_properties(0).total_memory / 1e9
        log.info("GPU: %s  (%.1f GB VRAM)", torch.cuda.get_device_name(0), vram_gb)
    else:
        log.warning("No GPU — inference will be very slow on CPU")

    kwargs = dict(
        token=HF_TOKEN,
        torch_dtype=torch.bfloat16 if _device == "cuda" else torch.float32,
        device_map="auto",
        low_cpu_mem_usage=True,
        ignore_mismatched_sizes=True,
    )

    if USE_4BIT and _device == "cuda":
        try:
            from transformers import BitsAndBytesConfig
            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_use_double_quant=True,
            )
            log.info("4-bit quantization enabled")
        except ImportError:
            log.warning("bitsandbytes not installed — using full precision")

    log.info("Loading %s ...", MODEL_ID)
    _processor = AutoProcessor.from_pretrained(MODEL_ID, token=HF_TOKEN)
    _model = Qwen2_5_VLForConditionalGeneration.from_pretrained(MODEL_ID, **kwargs)
    _model.eval()

    if _device == "cuda":
        torch.cuda.empty_cache()
        gc.collect()
        used = torch.cuda.memory_allocated() / 1e9
        log.info("Model loaded — VRAM in use: %.2f GB", used)


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

def _infer_scene(image: _PILImage.Image) -> str:
    """Single VLM forward pass. Returns raw text."""
    messages = [{
        "role": "user",
        "content": [
            {"type": "image", "image": image},
            {"type": "text",  "text": _SCENE_PROMPT},
        ],
    }]

    # Let processor handle the full message with image
    text = _processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )

    inputs = _processor(
        text=[text],
        images=[image],
        padding=True,
        return_tensors="pt",
    )

    # Move inputs to device with proper dtype
    for k in list(inputs.keys()):
        v = inputs[k]
        if hasattr(v, "to"):
            if v.is_floating_point():
                inputs[k] = v.to(_device, dtype=torch.bfloat16 if _device == "cuda" else torch.float32)
            else:
                inputs[k] = v.to(_device)

    with torch.no_grad():
        gen = _model.generate(
            **inputs,
            max_new_tokens=MAX_NEW_TOKENS,
            do_sample=False,
        )

    trimmed  = gen[:, inputs.input_ids.shape[1]:]
    raw_text = _processor.batch_decode(
        trimmed, skip_special_tokens=True
    )[0]

    if _device == "cuda":
        del gen, trimmed, inputs
        torch.cuda.empty_cache()
        gc.collect()

    return raw_text


def analyze_image(image_path: Path) -> dict:
    """
    Run Qwen VLM on one street-view image.
    Returns dict with keys: observations, openness, crowdedness,
    passable, raw_vlm_output, _latency_ms.
    """
    img = _PILImage.open(image_path).convert("RGB")
    t0  = time.perf_counter()
    raw = _infer_scene(img)
    ms  = round((time.perf_counter() - t0) * 1000, 1)

    obs, scalars = _parse_scene_json(raw)
    kws = {o["feature"] for o in obs}

    log.info(
        "  %d ms | %d observations | keywords: %s | open=%s crowd=%s pass=%s",
        int(ms), len(obs), sorted(kws),
        scalars["openness"], scalars["crowdedness"], scalars["passable"],
    )

    return {
        "observations":   obs,
        "raw_vlm_output": raw,
        "_latency_ms":    ms,
        **scalars,
    }

# ---------------------------------------------------------------------------
# OSM landmark lookup
# ---------------------------------------------------------------------------

_OVERPASS_URL = "https://overpass-api.de/api/interpreter"

def landmark_lookup(lat: float, lon: float, radius: int = 100) -> str:
    """
    Query OpenStreetMap for named landmarks near GPS point.
    Radius 100 m (increased from 60 m) to reach building-plot nodes
    set back from street centrelines (e.g. Passeig de Gracia).
    Prioritises tourism/historic over plain street names.
    Never uses the VLM.
    """
    query = f"""
    [out:json][timeout:15];
    (
      node["tourism"~"attraction|artwork|viewpoint"](around:{radius},{lat},{lon});
      way["tourism"~"attraction|artwork|viewpoint"](around:{radius},{lat},{lon});
      node["historic"](around:{radius},{lat},{lon});
      way["historic"](around:{radius},{lat},{lon});
      way["highway"="pedestrian"]["name"](around:{radius},{lat},{lon});
      way["highway"="primary"]["name"](around:{radius},{lat},{lon});
    );
    out body;
    """
    try:
        r = requests.post(
            _OVERPASS_URL,
            data={"data": query}, timeout=15,
        )
        elements = r.json().get("elements", [])
        priority, fallback = [], []
        for e in elements:
            tags = e.get("tags", {})
            name = tags.get("name") or tags.get("name:en", "")
            if not name:
                continue
            if tags.get("tourism") or tags.get("historic"):
                priority.append(name)
            else:
                fallback.append(name)
        result = priority[0] if priority else (fallback[0] if fallback else "")
        if result:
            log.info("  OSM landmark: %s (radius %dm)", result, radius)
        return result
    except Exception as exc:
        log.debug("OSM lookup failed (%s, %s): %s", lat, lon, exc)
        return ""

# ---------------------------------------------------------------------------
# Agent context builder
# ---------------------------------------------------------------------------

_AGENT_TEMPLATES = {
    "path_condition":  lambda e, p, d: f"{e} in {p} — {d}",
    "enclosure":       lambda e, p, d: f"space in {p}: {d} ({e})",
    "greenery":        lambda e, p, d: f"{e} in {p} — {d}",
    "lighting":        lambda e, p, d: f"light in {p}: {d}",
    "built_character": lambda e, p, d: f"{e} in {p} — {d}",
    "people_activity": lambda e, p, d: f"people in {p}: {e} — {d}",
}

_ARCHETYPE_PREAMBLE = {
    "tourist":  "You are a tourist choosing a scenic walking route.",
    "commuter": "You are a commuter choosing the fastest clear path.",
    "resident": "You are a local resident evaluating street quality.",
    "student":  "You are a student looking for a comfortable, social route.",
}


def build_agent_context(scene_analysis: dict, archetype: str,
                        landmark: str = "") -> str:
    """
    Build natural language context for an LLM ABM agent.

    Design intent: give the agent the full set of relevant observations
    in the order the VLM produced them (i.e. observation order reflects
    visual salience, not our assumptions). The agent's own archetype
    knowledge determines what it weighs and acts on.

    Structure:
    - Archetype relevance filter: removes keywords irrelevant to this agent.
    - All matching observations included — not truncated to one per keyword.
    - GPS landmark added to built_character when available.
    - passable=false stated explicitly as a fact, not an instruction.
    - No ordering imposed beyond the VLM's original observation sequence.
    """
    arch     = archetype.lower()
    relevant = ARCHETYPE_FEATURES.get(arch, set())
    preamble = _ARCHETYPE_PREAMBLE.get(arch, "You are a pedestrian.")

    # Group storage observations into agent keywords
    grouped: dict[str, list] = {k: [] for k in AGENT_MERGE}
    for o in scene_analysis.get("observations", []):
        for ak, skws in AGENT_MERGE.items():
            if o.get("feature") in skws:
                grouped[ak].append(o)

    # GPS landmark as a factual addition to built_character
    if landmark:
        grouped["built_character"].append({
            "feature":    "built_character",
            "element":    landmark,
            "panel":      "L",
            "descriptor": "named landmark on this street (GPS verified)",
        })

    # Render all observations for relevant keywords, VLM order preserved
    lines = []
    for ak, items in grouped.items():
        if ak not in relevant or not items:
            continue
        for o in items:
            tmpl = _AGENT_TEMPLATES.get(ak)
            if tmpl:
                lines.append(tmpl(
                    o.get("element", ""),
                    o.get("panel", ""),
                    o.get("descriptor", ""),
                ))

    obs_text   = " ".join(lines) if lines else "No specific features observed."
    passable   = scene_analysis.get("passable", True)
    block_note = " Path is physically blocked in CL or CR." if not passable else ""

    return (
        f"{preamble} "
        f"Street: {obs_text}"
        f" Openness {scene_analysis.get('openness', 3)}/5."
        f" Crowd {scene_analysis.get('crowdedness', 3)}/5."
        f"{block_note}"
    )

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
# Helper for checking completion
# ---------------------------------------------------------------------------

def _is_done(results_dir: Path, point_id: str) -> bool:
    """True if this point already has a valid completed result."""
    p = results_dir / f"{point_id}_analysis.json"
    if not p.exists():
        return False
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
        return rec.get("metadata", {}).get("status") in ("ok", "no_streetview")
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def _result_path(results_dir: Path, point_id: str) -> Path:
    return results_dir / f"{point_id}_analysis.json"


def run_pipeline(sample_points: list, images_dir: Path, results_dir: Path):
    global _shutdown_requested

    pending = [pt for pt in sample_points if not _is_done(results_dir, pt["id"])]
    done_n  = len(sample_points) - len(pending)

    log.info("Total sample points : %d", len(sample_points))
    log.info("Already completed   : %d", done_n)
    log.info("Remaining           : %d", len(pending))

    if not pending:
        log.info("All points already analysed — nothing to do.")
        return

    stats = {"ok": 0, "no_sv": 0, "errors": 0}
    start = time.time()

    pbar = tqdm(pending, desc="Analysing", unit="loc", dynamic_ncols=True)

    for pt in pbar:
        if _shutdown_requested:
            log.warning("Shutdown requested — stopping after %d images.", stats["ok"])
            break

        # Update ETA in progress bar
        done_so_far = sum(stats.values())
        if done_so_far > 0:
            rate = done_so_far / (time.time() - start)
            eta  = (len(pending) - done_so_far) / rate
            pbar.set_postfix(
                ok=stats["ok"], no_sv=stats["no_sv"],
                eta=str(timedelta(seconds=int(eta))),
            )

        try:
            # 1. Fetch Street View image (with retry)
            img_path = None
            img_dest = images_dir / f"sv_{pt['id']}_h{int(pt['heading'])}.jpg"
            for attempt in range(3):
                img_path = fetch_sv(pt["lat"], pt["lon"], pt["heading"], images_dir)
                if img_path is not None:
                    break
                time.sleep(2 ** attempt)

            if img_path is None:
                stats["no_sv"] += 1
                stub = {
                    "metadata": {
                        "timestamp":         datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
                        "latitude":          pt["lat"],
                        "longitude":         pt["lon"],
                        "heading":           pt["heading"],
                        "street_name":       pt["street_name"],
                        "highway_type":      pt["highway_type"],
                        "edge_id":           pt["edge_id"],
                        "dist_along_edge_m": pt["dist_along_edge_m"],
                        "source_image":      None,
                        "model":             MODEL_ID,
                        "device":            _device,
                        "schema_version":    "v6",
                        "status":            "no_streetview",
                    },
                    "scene_analysis": None,
                }
                _result_path(results_dir, pt["id"]).write_text(
                    json.dumps(stub, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                continue

            time.sleep(0.25)

            # 2. VLM inference
            scene = analyze_image(img_path)
            latency_ms = scene.pop("_latency_ms", None)

            # 3. OSM landmark lookup — GPS-based, not VLM
            landmark = landmark_lookup(pt["lat"], pt["lon"])
            if landmark:
                log.info("  Landmark (OSM): %s", landmark)

            # 4. Save storage record
            record = {
                "metadata": {
                    "timestamp":         datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
                    "latitude":          pt["lat"],
                    "longitude":         pt["lon"],
                    "heading":           pt["heading"],
                    "street_name":       pt["street_name"],
                    "highway_type":      pt["highway_type"],
                    "edge_id":           pt["edge_id"],
                    "dist_along_edge_m": pt["dist_along_edge_m"],
                    "source_image":      img_path.name,
                    "model":             MODEL_ID,
                    "device":            _device,
                    "latency_ms":        latency_ms,
                    "schema_version":    "v6",
                    "status":            "ok",
                },
                "scene_analysis": {
                    "observations":   scene.get("observations", []),
                    "landmark_name":  landmark,
                    "openness":       scene.get("openness", 3),
                    "crowdedness":    scene.get("crowdedness", 3),
                    "passable":       scene.get("passable", True),
                    "raw_vlm_output": scene.get("raw_vlm_output", ""),
                },
            }
            _result_path(results_dir, pt["id"]).write_text(
                json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            stats["ok"] += 1

        except KeyboardInterrupt:
            log.warning("KeyboardInterrupt — stopping cleanly.")
            break

        except Exception as exc:
            log.error("Error processing %s: %s", pt["id"], exc, exc_info=True)
            stats["errors"] += 1
            # Write error stub so this point is not retried indefinitely
            try:
                _result_path(results_dir, pt["id"]).write_text(
                    json.dumps({
                        "metadata": {
                            "timestamp":      datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
                            "latitude":       pt["lat"], "longitude": pt["lon"],
                            "schema_version": "v6", "status": "error",
                            "error":          str(exc),
                        },
                        "scene_analysis": None,
                    }, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except Exception:
                pass

    elapsed = time.time() - start
    log.info("Pipeline complete in %s — ok=%d no_sv=%d errors=%d",
             str(timedelta(seconds=int(elapsed))),
             stats["ok"], stats["no_sv"], stats["errors"])


def print_summary(results_dir: Path):
    files  = list(results_dir.glob("*_analysis.json"))
    counts = {"ok": 0, "no_streetview": 0, "error": 0}
    kw_counts: dict[str, int] = {}

    for f in files:
        try:
            rec    = json.loads(f.read_text(encoding="utf-8"))
            status = rec.get("metadata", {}).get("status", "error")
            counts[status] = counts.get(status, 0) + 1
            if status == "ok":
                for obs in (rec.get("scene_analysis") or {}).get("observations", []):
                    kw = obs.get("feature", "")
                    kw_counts[kw] = kw_counts.get(kw, 0) + 1
        except Exception:
            counts["error"] = counts.get("error", 0) + 1

    log.info("─" * 50)
    log.info("SUMMARY")
    log.info("  Analysed (ok)   : %d", counts["ok"])
    log.info("  No Street View  : %d", counts.get("no_streetview", 0))
    log.info("  Errors          : %d", counts.get("error", 0))

    if kw_counts:
        log.info("Keyword frequency across all ok images:")
        for kw, n in sorted(kw_counts.items(), key=lambda x: -x[1]):
            bar = "█" * (n * 20 // max(kw_counts.values()))
            log.info("  %-22s %4d  %s", kw, n, bar)

    log.info("Results: %s", results_dir)
    log.info("─" * 50)


# ---------------------------------------------------------------------------
# Trial run
# ---------------------------------------------------------------------------

def run_trial(args, images_dir: Path):
    """Single-image diagnostic run. Prints observations and agent contexts."""
    if args.trial_lat is not None and args.trial_lon is not None:
        pt = {
            "id":                f"{args.trial_lat:.6f}_{args.trial_lon:.6f}",
            "lat":               args.trial_lat,
            "lon":               args.trial_lon,
            "heading":           args.trial_heading or 0.0,
            "street_name":       "manual",
            "highway_type":      "unknown",
            "edge_id":           "manual",
            "dist_along_edge_m": None,
        }
    else:
        pts = query_and_sample_points(images_dir.parent)
        if not pts:
            log.error("No sample points available.")
            return
        idx = args.trial_index or 0
        pt  = dict(pts[idx % len(pts)])
        if args.trial_heading is not None:
            pt["heading"] = args.trial_heading

    log.info("Trial: %s  (%.6f, %.6f)  heading %.1f°",
             pt["street_name"], pt["lat"], pt["lon"], pt["heading"])

    img_path = fetch_sv(pt["lat"], pt["lon"], pt["heading"], images_dir)
    if img_path is None:
        log.warning("No Street View coverage at this location.")
        return

    log.info("Image: %s", img_path)
    scene      = analyze_image(img_path)
    landmark   = landmark_lookup(pt["lat"], pt["lon"])
    latency_ms = scene.pop("_latency_ms", None)

    print(f"\n=== OBSERVATIONS ({len(scene['observations'])} found) ===")
    print(f"  {'feature':<22} {'element':<22} {'panel':<8} descriptor")
    print("  " + "-" * 70)
    for o in scene["observations"]:
        print(f"  {o['feature']:<22} {o['element']:<22} {o['panel']:<8} {o['descriptor']}")

    print(f"\n  openness={scene['openness']}  "
          f"crowdedness={scene['crowdedness']}  "
          f"passable={scene['passable']}")
    print(f"  landmark (OSM): '{landmark}'")
    print(f"  latency: {latency_ms} ms")

    print("\n=== AGENT CONTEXTS ===")
    for arch in ["tourist", "commuter", "resident", "student"]:
        ctx = build_agent_context(scene, arch, landmark)
        print(f"\n  [{arch.upper()}]")
        print(f"  {ctx}")


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

    # File logging — persists across spot interruptions
    log_dir = output_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S"
    ))
    logging.getLogger().addHandler(file_handler)
    log.info("Log file: %s", log_file)

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

    # Release GPU before exit
    global _model, _processor
    del _model, _processor
    _model = _processor = None
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    gc.collect()
    log.info("GPU released. Exiting.")
    sys.exit(0)


if __name__ == "__main__":
    main()
