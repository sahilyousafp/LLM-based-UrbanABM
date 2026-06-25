"""
street_plm_job.py
=================
StreetPLM inference using Qwen3-VL-8B-Instruct via transformers.
Loads in bfloat16 with device_map="auto". Supports parallel workers.

Usage:
  python street_plm_job.py                               # fetch + analyse
  python street_plm_job.py --fetch-only                   # images only
  python street_plm_job.py --analyze-only --workers 4     # parallel analyse
  python street_plm_job.py --trial --trial-lat 41.391694 --trial-lon 2.164944
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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Literal, Optional

try:
    import osmnx as _osmnx_check
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "osmnx"])

# Bootstrap qwen-vl-utils
try:
    import qwen_vl_utils  # noqa: F401
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "qwen-vl-utils"])

try:
    import bitsandbytes  # noqa: F401
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "bitsandbytes"])

import numpy as np
import pyproj
import requests
import torch
from huggingface_hub import login
from PIL import Image as _PILImage
from pydantic import BaseModel, Field, field_validator
from shapely import wkt as shapely_wkt
from shapely.geometry import LineString
import geopandas as gpd
from tqdm.auto import tqdm
from dotenv import load_dotenv
from transformers import AutoProcessor, Qwen3VLForConditionalGeneration

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger(__name__)

load_dotenv(Path(__file__).parent / ".env")

STREETVIEW_API_KEY = os.environ.get("GOOGLE_STREETVIEW_API_KEY", "")
HF_TOKEN           = os.environ.get("HF_TOKEN", "")

DEFAULT_BBOX = {"min_lon": 2.1500, "min_lat": 41.3862, "max_lon": 2.1740, "max_lat": 41.4042}
SAMPLE_DISTANCE_M = 200
GRID_SPACING_M    = 50
SV_SIZE, SV_FOV, SV_PITCH, SV_RADIUS = "640x640", 90, 0, 50
MODEL_ID = "Qwen/Qwen3-VL-8B-Instruct"
MAX_NEW_TOKENS, TEMPERATURE, TOP_P, TOP_K, REP_PENALTY = 1800, 0.7, 0.8, 20, 1.15
MIN_FIELDS_OK, MAX_RETRIES = 3, 4
UTM31N = "EPSG:32631"

_SV_BASE = "https://maps.googleapis.com/maps/api/streetview"
_META_BASE = "https://maps.googleapis.com/maps/api/streetview/metadata"

# Flat-array schema: each category is a sparse list; only observed zones appear.
_ZONE = Literal["far_left", "left", "center", "right", "far_right"]
_ZONES = ("far_left", "left", "center", "right", "far_right")

class LightingEntry(BaseModel):
    zone: _ZONE = "center"
    element: str = ""
    condition: Literal["dark", "dim", "adequate", "bright", "unknown"] = "adequate"

class SpatialCharacterEntry(BaseModel):
    zone: _ZONE = "center"
    width: Literal["narrow", "moderate", "wide", "unknown"] = "unknown"
    enclosure: Literal["open", "semi", "enclosed", "unknown"] = "unknown"
    passability: Literal["clear", "caution", "obstructed", "blocked"] = "clear"
    lane_type: Literal["sidewalk", "main_roadway", "shared_bus_lane", "bicycle_lane", "median", "shared", "road", "unknown"] = "unknown"
    crossing: Literal["none", "zebra", "signalised", "unknown"] = "none"
    architectural_style: Literal["neo_gothic", "modernist", "contemporary", "neoclassical", "vernacular", "eclectic", "art_deco", "art_nouveau", "other", "unknown"] = "unknown"
    building_condition: Literal["excellent", "good", "fair", "poor", "under_construction", "unknown"] = "unknown"
    storefront_type: Literal["retail", "restaurant", "cafe", "office", "residential", "hotel", "vacant", "cultural", "industrial", "other", "unknown"] = "unknown"
    architectural_details: Optional[str] = None

class CrowdednessEntry(BaseModel):
    zone: _ZONE = "center"
    density_level: Literal["empty", "sparse", "moderate", "dense", "unknown"] = "empty"

class GreeneryEntry(BaseModel):
    zone: _ZONE = "center"
    element: str = ""
    coverage: Literal["none", "sparse", "moderate", "dense", "unknown"] = "none"

class StreetAmenityEntry(BaseModel):
    zone: _ZONE = "center"
    element: str = ""
    material_and_colour: str = ""
    presence: Literal["none", "few", "several", "many", "unknown"] = "unknown"

class VisibleTextEntry(BaseModel):
    text: str = ""
    zone: _ZONE = "center"
    type: Literal["sign", "signage", "board", "banner", "label", "graffiti", "information"] = "sign"

class RestructuredStreetSceneAnalysis(BaseModel):
    scene: str = "unknown"
    lighting:          List[LightingEntry]         = Field(default_factory=list)
    spatial_character: List[SpatialCharacterEntry] = Field(default_factory=list)
    crowdedness:       List[CrowdednessEntry]       = Field(default_factory=list)
    greenery:          List[GreeneryEntry]          = Field(default_factory=list)
    street_amenities:  List[StreetAmenityEntry]     = Field(default_factory=list)
    visible_text:      List[VisibleTextEntry]        = Field(default_factory=list)

    @field_validator("scene", mode="before")
    @classmethod
    def _coerce_scene(cls, v): return str(v).strip() if v else "unknown"

    @field_validator(
        "lighting", "spatial_character", "crowdedness", "greenery",
        "street_amenities", "visible_text", mode="before",
    )
    @classmethod
    def _coerce_to_list(cls, v):
        if isinstance(v, list): return v
        if isinstance(v, dict): return [v]
        return []

SYSTEM_PROMPT = (
    "You are an expert urban perception analyst. Study this street-view image carefully.\n"
    "Zones left-to-right: far_left | left | center | right | far_right"
)

USER_PROMPT = (
    "Output a JSON object with these 7 fields:\n"
    "  lighting          - [{zone, element: describe the specific light source, condition: dark|dim|adequate|bright}]\n"
    "  spatial_character - [{zone, width: narrow|moderate|wide, enclosure: open|semi|enclosed,\n"
    "                         passability: clear|caution|obstructed|blocked,\n"
    "                         lane_type: sidewalk|main_roadway|shared_bus_lane|bicycle_lane|median|shared|road|unknown,\n"
    "                         crossing: none|zebra|signalised,\n"
    "                         architectural_style: neo_gothic|modernist|contemporary|neoclassical|vernacular|eclectic|art_deco|art_nouveau|other|unknown,\n"
    "                         building_condition: excellent|good|fair|poor|unknown,\n"
    "                         storefront_type: retail|restaurant|cafe|office|residential|hotel|vacant|cultural|other|unknown,\n"
    "                         architectural_details: optional descriptive phrase or null}]\n"
    "  crowdedness       - [{zone, density_level: empty|sparse|moderate|dense}]\n"
    "  greenery          - [{zone, element: specific plant/tree type and description, coverage: none|sparse|moderate|dense}]\n"
    "  street_amenities  - [{zone, element: specific object type, material_and_colour: material and colour, presence: none|few|several|many}]\n"
    "  visible_text      - [{text: exact readable string, zone, type: sign|label|graffiti|board|banner|information}]\n"
    "  scene             - one-sentence overview of this specific street\n\n"
    "Rules:\n"
    "  * spatial_character: one entry per zone for ALL 5 zones (far_left, left, center, right, far_right). Report each zone's lane/building even if visibility is limited.\n"
    "  * lighting: if uniform across the whole scene, report it ONCE (zone=\"center\"). Only add extra entries where a zone differs meaningfully. NEVER repeat the same element in multiple zones.\n"
    "  * crowdedness: only include zones that differ in density from each other. If the scene is uniform, report once (zone=\"center\").\n"
    "  * greenery / street_amenities: list every DISTINCT element type you see, one entry per (zone, element-type). If the same element spans multiple zones, place it in its dominant zone only — do NOT repeat it in other zones.\n"
    "  * Multiple DIFFERENT elements in ONE zone → multiple list entries for that zone. Same element → only ONE entry regardless of how many zones it spans.\n"
    "  * element fields: name material, colour, style, scale (e.g. 'grey cast-iron double-arm lamp post')\n"
    "  * visible_text: upper 90% of frame only; skip watermarks, licence plates, blurred text\n"
    "  * Output only the JSON object — no prose, no markdown\n\n"
    "Example (DIFFERENT image — for schema format only, do not copy values):\n"
    '{"lighting":[{"zone":"left","element":"dappled shade from dense tree canopy","condition":"dim"},'
    '{"zone":"center","element":"direct overhead midday sun","condition":"bright"}],'
    '"spatial_character":[{"zone":"left","width":"narrow","enclosure":"enclosed","passability":"clear",'
    '"lane_type":"sidewalk","crossing":"none","architectural_style":"modernist","building_condition":"good",'
    '"storefront_type":"retail","architectural_details":"ornate balconies with wrought-iron railings"},'
    '{"zone":"center","width":"moderate","enclosure":"semi","passability":"clear","lane_type":"road",'
    '"crossing":"zebra","architectural_style":"contemporary","building_condition":"fair",'
    '"storefront_type":"restaurant","architectural_details":null}],'
    '"crowdedness":[{"zone":"left","density_level":"sparse"},{"zone":"center","density_level":"moderate"}],'
    '"greenery":[{"zone":"left","element":"mature London plane trees with mottled grey-green bark","coverage":"dense"},'
    '{"zone":"right","element":"terracotta-potted rosemary shrubs on window ledges","coverage":"sparse"}],'
    '"street_amenities":[{"zone":"left","element":"ornate cast-iron double-arm street lamp with frosted globe",'
    '"material_and_colour":"black painted cast iron","presence":"few"},'
    '{"zone":"center","element":"yellow painted concrete bollard","material_and_colour":"yellow concrete","presence":"several"}],'
    '"visible_text":[{"text":"MERCAT","zone":"far_left","type":"sign"}],'
    '"scene":"Tree-lined residential street with moderate pedestrian activity and parked vehicles."}\n\n'
    "Now analyse THIS image. Output only JSON:"
)

FULL_PROMPT = SYSTEM_PROMPT + "\n\n" + USER_PROMPT

_GOOGLE_WATERMARK_RE = re.compile(r"^(©\s*)?(20\d{2}\s+)?google(\s+maps)?[\s.,]*$", re.IGNORECASE)
_LICENSE_PLATE_RE = re.compile(r"^[A-Z]{1,4}[\s·\-]?\d{2,4}[\s·\-]?[A-Z]{0,3}$|^\d{4}[\s·\-][A-Z]{2,3}$", re.IGNORECASE)

def _parse_json(raw: str) -> dict:
    raw = raw.strip().replace('```json', '').replace('```', '')
    for _ in range(8):
        raw = raw.replace(',}', '}').replace(',]', ']').replace(', }', '}').replace(', ]', ']')
    try: return json.loads(raw)
    except json.JSONDecodeError: pass
    start = raw.find("{")
    if start != -1:
        depth = 0
        for i, c in enumerate(raw[start:], start):
            if c == "{": depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try: return json.loads(raw[start:i+1])
                    except json.JSONDecodeError: pass
                    break
    for a in range(10):
        try: return json.loads(raw + "]}" * (a + 1))
        except json.JSONDecodeError: pass
    result = {}
    for key in ("scene", "lighting", "spatial_character", "crowdedness", "greenery", "street_amenities", "visible_text"):
        m = re.search(rf'"{key}"\s*:\s*(".*?"|[\[{{].*?[\]}}])', raw, re.DOTALL)
        if m:
            try: result[key] = json.loads(m.group(1))
            except Exception: result[key] = m.group(1)
    return result

def _filter_visible_text(entries):
    seen, deduped = set(), []
    for e in entries:
        if not isinstance(e, dict) or not e.get("text"): continue
        txt = e["text"]
        if txt in seen or _GOOGLE_WATERMARK_RE.match(txt) or _LICENSE_PLATE_RE.match(txt): continue
        seen.add(txt); deduped.append(e)
    return deduped

# ── Key aliases: remap model-hallucinated field names to canonical ones ────────
_KEY_ALIASES: dict = {
    "scene": "scene", "scene_description": "scene", "overview": "scene", "summary": "scene",
    "lighting": "lighting", "light": "lighting", "light_quality": "lighting",
    "spatial_character": "spatial_character", "spatial": "spatial_character",
    "space": "spatial_character", "street_space": "spatial_character",
    "crowdedness": "crowdedness", "crowding": "crowdedness", "crowd": "crowdedness",
    "pedestrians": "crowdedness", "people": "crowdedness",
    "greenery": "greenery", "vegetation": "greenery", "plants": "greenery", "trees": "greenery",
    "street_amenities": "street_amenities", "amenities": "street_amenities",
    "furniture": "street_amenities", "street_furniture": "street_amenities",
    "visible_text": "visible_text", "text": "visible_text", "signs": "visible_text",
    "ocr": "visible_text", "signage": "visible_text",
}

# ── Echo detection: model copied the template back as values ──────────────────
_ECHO_SENTINELS = frozenset({
    "dark|dim|adequate|bright", "empty|sparse|moderate|dense", "narrow|moderate|wide",
    "clear|caution|obstructed|blocked", "none|zebra|signalised",
    "sidewalk|main_roadway|shared_bus_lane|bicycle_lane|median|shared|road|unknown",
    "neo_gothic|modernist|contemporary|neoclassical|vernacular|eclectic|art_deco|art_nouveau|other|unknown",
    "excellent|good|fair|poor|unknown",
    "retail|restaurant|cafe|office|residential|hotel|vacant|cultural|other|unknown",
    "far_left|left|center|right|far_right",
    "sky|buildings|lamps|trees|...", "trees|shrubs|grass|planters|...",
    "bench|lamp|sign|bin|bollard|...", "sign|label|graffiti|board|banner|information",
    "exact readable string", "exact text you can read",
    "<one-sentence overall description>", "<optional phrase or null>",
    "optional descriptive phrase or null", "metal grey|...",
})

def _model_echoed_template(parsed: dict) -> bool:
    def _has(obj) -> bool:
        if isinstance(obj, str): return obj.strip().lower() in _ECHO_SENTINELS
        if isinstance(obj, dict): return any(_has(v) for v in obj.values())
        if isinstance(obj, list): return any(_has(i) for i in obj)
        return False
    return any(_has(v) for v in parsed.values())

def _dedup_entries(entries: list, key_fn) -> list:
    seen, out = set(), []
    for e in entries:
        if not isinstance(e, dict):
            continue
        k = key_fn(e)
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out

def _normalise_result(raw: dict) -> dict:
    normalised: dict = {}
    for k, v in raw.items():
        canonical = _KEY_ALIASES.get(k.strip().lower())
        if canonical:
            normalised[canonical] = v
    if "visible_text" in normalised and isinstance(normalised["visible_text"], list):
        normalised["visible_text"] = _filter_visible_text(normalised["visible_text"])
    # lighting: deduplicate by (element text, condition) — same light source in every zone is noise
    if "lighting" in normalised and isinstance(normalised["lighting"], list):
        normalised["lighting"] = _dedup_entries(
            normalised["lighting"],
            lambda e: (e.get("element", "").lower().strip()[:60], e.get("condition", ""))
        )
    # spatial_character: one entry per zone maximum
    if "spatial_character" in normalised and isinstance(normalised["spatial_character"], list):
        normalised["spatial_character"] = _dedup_entries(
            normalised["spatial_character"],
            lambda e: e.get("zone", "")
        )
    # crowdedness: one entry per zone
    if "crowdedness" in normalised and isinstance(normalised["crowdedness"], list):
        normalised["crowdedness"] = _dedup_entries(
            normalised["crowdedness"],
            lambda e: e.get("zone", "")
        )
    # greenery: deduplicate same element in same zone
    if "greenery" in normalised and isinstance(normalised["greenery"], list):
        normalised["greenery"] = _dedup_entries(
            normalised["greenery"],
            lambda e: (e.get("zone", ""), e.get("element", "").lower().strip()[:60])
        )
    # street_amenities: deduplicate same element in same zone
    if "street_amenities" in normalised and isinstance(normalised["street_amenities"], list):
        normalised["street_amenities"] = _dedup_entries(
            normalised["street_amenities"],
            lambda e: (e.get("zone", ""), e.get("element", "").lower().strip()[:60])
        )
    try:
        return RestructuredStreetSceneAnalysis(**normalised).model_dump()
    except Exception:
        return RestructuredStreetSceneAnalysis().model_dump()

def _count_populated(result):
    score = 0
    if result.get("scene", "unknown") not in ("unknown", ""):
        score += 1
    for entry in result.get("spatial_character", []):
        if isinstance(entry, dict) and entry.get("lane_type", "unknown") != "unknown":
            score += 1
    return score

def _parse_scene_json(text):
    d = _parse_json(text)
    if not d: return RestructuredStreetSceneAnalysis().model_dump()
    if _model_echoed_template(d): return RestructuredStreetSceneAnalysis().model_dump()
    return _normalise_result(d)

_model = None
_processor = None
_device = "cpu"

def load_model():
    global _model, _processor, _device
    if HF_TOKEN:
        login(token=HF_TOKEN, add_to_git_credential=False)
    _device = "cuda" if torch.cuda.is_available() else "cpu"
    if _device == "cuda":
        props = torch.cuda.get_device_properties(0)
        log.info("GPU: %s  (%.1f GB VRAM)", props.name, props.total_memory / 1e9)

    kwargs = dict(
        token=HF_TOKEN,
        torch_dtype=torch.bfloat16 if _device == "cuda" else torch.float32,
        device_map="auto",
        low_cpu_mem_usage=True,
        ignore_mismatched_sizes=True,
    )

    log.info("Loading %s (bfloat16, device_map=auto) …", MODEL_ID)
    _processor = AutoProcessor.from_pretrained(MODEL_ID, token=HF_TOKEN)
    _model = Qwen3VLForConditionalGeneration.from_pretrained(MODEL_ID, **kwargs)
    _model.eval()
    if _device == "cuda":
        torch.cuda.empty_cache()
        gc.collect()
    log.info("Model loaded.")

_gpu_lock = Lock()

def _infer(image, greedy=False):
    messages = [{
        "role": "user",
        "content": [
            {"type": "image", "image": image},
            {"type": "text", "text": FULL_PROMPT},
        ],
    }]
    inputs = _processor.apply_chat_template(
        messages, tokenize=True, add_generation_prompt=True,
        return_dict=True, return_tensors="pt",
    ).to(_model.device)
    input_len = inputs["input_ids"].shape[1]
    gen_kw = dict(max_new_tokens=MAX_NEW_TOKENS)
    if not greedy:
        gen_kw.update(do_sample=True, temperature=TEMPERATURE, top_p=TOP_P, top_k=TOP_K)
    t0 = time.time()
    with _gpu_lock, torch.no_grad():
        out_ids = _model.generate(**inputs, **gen_kw)
    latency = (time.time() - t0) * 1000
    trimmed = out_ids[:, input_len:]
    raw = _processor.batch_decode(
        trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False
    )[0]
    if _device == "cuda":
        del out_ids, trimmed, inputs
        torch.cuda.empty_cache()
        gc.collect()
    return _parse_scene_json(raw), raw, latency

def _is_blank(img, thresh=18.0):
    return float(np.array(img.convert("L"), dtype=np.float32).std()) < thresh

def analyze_image(image_path):
    img = _PILImage.open(image_path).convert("RGB")
    if _is_blank(img):
        log.warning("  Blank image skipped: %s", image_path)
        r = RestructuredStreetSceneAnalysis().model_dump()
        r["_latency_ms"] = 0.0; r["_blank"] = True; return r
    result, raw = None, ""
    total_ms = 0.0
    for attempt in range(1, MAX_RETRIES + 1):
        result, raw, ms = _infer(img, greedy=(attempt == MAX_RETRIES))
        total_ms += ms
        if _count_populated(result) >= MIN_FIELDS_OK or attempt == MAX_RETRIES:
            break
        log.warning("  Attempt %d/%d: only %d fields populated", attempt, MAX_RETRIES, _count_populated(result))
    populated = _count_populated(result)
    total = sum(1 for k in result if not k.startswith("_"))
    log.info("  Scene: %d ms  %d/%d fields", int(total_ms), populated, total)
    if populated == 0: log.warning("  0 fields — raw: %r", raw[:500])
    result["_latency_ms"] = round(total_ms, 1)
    return result

def sv_available(lat, lon):
    r = requests.get(_META_BASE, params={"location": f"{lat},{lon}", "radius": SV_RADIUS, "source": "outdoor", "key": STREETVIEW_API_KEY}, timeout=10)
    return r.status_code == 200 and r.json().get("status") == "OK"

def fetch_sv(lat, lon, heading, images_dir):
    name = f"sv_{lat:.6f}_{lon:.6f}_h{int(heading)}.jpg"
    path = images_dir / name
    if path.exists(): return path
    if not sv_available(lat, lon): return None
    try:
        r = requests.get(_SV_BASE, params={"size": SV_SIZE, "location": f"{lat},{lon}", "heading": heading,
                                            "pitch": SV_PITCH, "fov": SV_FOV, "source": "outdoor", "key": STREETVIEW_API_KEY}, timeout=30)
        r.raise_for_status()
        if len(r.content) < 5120: return None
        path.write_bytes(r.content); return path
    except Exception as exc:
        log.warning("SV error (%s,%s): %s", lat, lon, exc); return None

def fetch_nearby_landmarks(lat, lon):
    return []

def fetch_zone_from_server(server_url: str) -> dict:
    url = server_url.rstrip("/") + "/api/zone/current"
    try:
        r = requests.get(url, timeout=5); r.raise_for_status()
        bbox_list = r.json()["bbox"]  # [west, south, east, north]
        log.info("Zone fetched from %s: %s", url, bbox_list)
        return {"min_lon": bbox_list[0], "min_lat": bbox_list[1],
                "max_lon": bbox_list[2], "max_lat": bbox_list[3]}
    except Exception as exc:
        log.warning("Could not fetch zone from %s: %s — using DEFAULT_BBOX", url, exc)
        return DEFAULT_BBOX

def sample_points_from_osmnx(bbox: dict, spacing_m: int, output_root: Path) -> list:
    import osmnx as ox
    from shapely.geometry import box as shapely_box
    log.info("Downloading walk network via OSMnx …")
    bbox_poly = shapely_box(bbox["min_lon"], bbox["min_lat"], bbox["max_lon"], bbox["max_lat"])
    G = ox.graph_from_polygon(bbox_poly, network_type="walk", retain_all=False)
    gdf = ox.graph_to_gdfs(G, nodes=False, edges=True).to_crs(UTM31N)
    log.info("OSMnx: %d walk edges", len(gdf))
    tr = pyproj.Transformer.from_crs(UTM31N, "EPSG:4326", always_xy=True)
    pts, seen = [], set()
    for (u, v, _k), row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty or geom.length < 10: continue
        hw = row.get("highway", "unknown")
        if isinstance(hw, list): hw = hw[0]
        name = row.get("name", "")
        if isinstance(name, list): name = name[0]
        length  = geom.length
        n_steps = max(1, int(length / spacing_m))
        for i in range(n_steps + 1):
            dist   = min(i * spacing_m, length)
            p_proj = geom.interpolate(dist)
            offset = 1.0 if dist + 1.0 < length else -1.0
            p2     = geom.interpolate(dist + offset)
            heading = (np.degrees(np.arctan2(p2.x - p_proj.x, p2.y - p_proj.y)) + 360) % 360
            lon, lat = tr.transform(p_proj.x, p_proj.y)
            cell = (round(lat, 4), round(lon, 4))
            if cell in seen: continue
            seen.add(cell)
            pts.append({"id": f"{lat:.6f}_{lon:.6f}", "lat": round(float(lat), 6),
                        "lon": round(float(lon), 6), "heading": round(float(heading), 1),
                        "street_name": str(name) if name else "", "highway_type": str(hw),
                        "edge_id": f"{u}_{v}", "dist_along_edge_m": round(float(dist), 1)})
    # Clip to bbox — OSMnx may return edges that cross the polygon boundary
    before = len(pts)
    pts = [p for p in pts
           if bbox["min_lat"] <= p["lat"] <= bbox["max_lat"]
           and bbox["min_lon"] <= p["lon"] <= bbox["max_lon"]]
    if len(pts) < before:
        log.info("Clipped %d out-of-bounds points", before - len(pts))
    _save_sample_points(pts, output_root)
    log.info("OSMnx sampling: %d candidate points", len(pts))
    return pts

def sample_points_from_grid(bbox: dict, spacing_m: int, output_root: Path) -> list:
    log.info("Using BBOX grid fallback (%d m spacing) …", spacing_m)
    mid_lat  = (bbox["min_lat"] + bbox["max_lat"]) / 2
    lat_step = spacing_m / 111_320.0
    lon_step = spacing_m / (111_320.0 * np.cos(np.radians(mid_lat)))
    pts, seen = [], set()
    lat = bbox["min_lat"]
    while lat <= bbox["max_lat"] + 1e-9:
        lon = bbox["min_lon"]
        while lon <= bbox["max_lon"] + 1e-9:
            for heading in (0.0, 90.0):
                key = (round(lat, 5), round(lon, 5), int(heading))
                if key not in seen:
                    seen.add(key)
                    rl, rn = round(lat, 6), round(lon, 6)
                    pts.append({"id": f"{rl:.6f}_{rn:.6f}_h{int(heading)}", "lat": rl, "lon": rn,
                                "heading": float(heading), "street_name": "", "highway_type": "unknown",
                                "edge_id": "grid", "dist_along_edge_m": None})
            lon += lon_step
        lat += lat_step
    _save_sample_points(pts, output_root)
    log.info("Grid fallback: %d candidate points", len(pts))
    return pts

def _save_sample_points(pts: list, output_root: Path) -> None:
    f = output_root / "sample_points.json"
    f.write_text(json.dumps(pts, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Saved %d sample points → %s", len(pts), f)

def query_and_sample_points(output_root: Path, bbox: dict, spacing_m: int = SAMPLE_DISTANCE_M, resume: bool = True) -> list:
    fp = output_root / "sample_points.json"
    if resume and fp.exists():
        pts = json.loads(fp.read_text(encoding="utf-8"))
        log.info("Resuming — loaded %d points from %s", len(pts), fp)
        return pts
    try:
        return sample_points_from_osmnx(bbox, spacing_m, output_root)
    except Exception as exc:
        log.warning("OSMnx failed (%s) — falling back to BBOX grid", exc)
        return sample_points_from_grid(bbox, GRID_SPACING_M, output_root)

def run_trial(args, images_dir):
    if args.trial_lat is not None and args.trial_lon is not None:
        pt = {"id": f"{args.trial_lat:.6f}_{args.trial_lon:.6f}", "lat": args.trial_lat, "lon": args.trial_lon,
              "heading": args.trial_heading or 0.0, "street_name": "manual", "highway_type": "unknown", "edge_id": "manual"}
    else:
        pts = query_and_sample_points(images_dir.parent)
        if not pts: log.error("No sample points."); return
        pt = dict(pts[(args.trial_index or 0) % len(pts)])
        if args.trial_heading is not None: pt["heading"] = args.trial_heading
    log.info("Trial: %s (%.6f, %.6f) heading %.1f", pt["street_name"], pt["lat"], pt["lon"], pt["heading"])
    ip = fetch_sv(pt["lat"], pt["lon"], pt["heading"], images_dir)
    if not ip: log.warning("No Street View."); return
    t0 = time.time(); res = analyze_image(ip)
    log.info("Latency: %d ms", round((time.time() - t0) * 1000))
    print(f"\n--- Scene ---\n  {res.get('scene', 'unknown')}")

    def _bz(lst, zone):
        return next((e for e in lst if isinstance(e, dict) and e.get("zone") == zone), {})

    print("\n--- Zone Detail ---")
    for zone_name in _ZONES:
        sp = _bz(res.get("spatial_character", []), zone_name)
        li = _bz(res.get("lighting",          []), zone_name)
        cr = _bz(res.get("crowdedness",        []), zone_name)
        gr = _bz(res.get("greenery",           []), zone_name)
        ams = [a for a in res.get("street_amenities", []) if isinstance(a, dict) and a.get("zone") == zone_name]
        vts = [t for t in res.get("visible_text",     []) if isinstance(t, dict) and t.get("zone") == zone_name]
        if not any([sp, li, cr, gr, ams, vts]): continue
        print(f"\n  [{zone_name}]")
        print(f"    lane={sp.get('lane_type','—')}  width={sp.get('width','—')}  passability={sp.get('passability','—')}  crossing={sp.get('crossing','—')}")
        print(f"    arch={sp.get('architectural_style','—')}  condition={sp.get('building_condition','—')}  storefront={sp.get('storefront_type','—')}")
        print(f"    light={li.get('condition','—')} ({li.get('element','—')})  crowd={cr.get('density_level','—')}  green={gr.get('coverage','—')} ({gr.get('element','—')})")
        if ams:
            items = ", ".join(f"{a.get('element','?')} ({a.get('presence','?')})" for a in ams)
            print(f"    amenities: {items}")
        if vts:
            items = ", ".join(f"\"{t.get('text','?')}\" [{t.get('type','?')}]" for t in vts)
            print(f"    text: {items}")
    print()

def _rp(results_dir, pid): return results_dir / f"{pid}_analysis.json"

def fetch_images(sample_points, images_dir):
    pending = [p for p in sample_points if not (images_dir / f"sv_{p['lat']:.6f}_{p['lon']:.6f}_h{int(p['heading'])}.jpg").exists()]
    log.info("Total: %d  To fetch: %d", len(sample_points), len(pending))
    if not pending: log.info("All images already cached."); return
    ok = 0
    for pt in tqdm(pending, desc="Fetching", unit="img"):
        ip = fetch_sv(pt["lat"], pt["lon"], pt["heading"], images_dir)
        if ip: ok += 1
        time.sleep(0.2)
    log.info("Fetched %d / %d images", ok, len(pending))

def _analyze_one(pt, images_dir, results_dir):
    ip = fetch_sv(pt["lat"], pt["lon"], pt["heading"], images_dir)
    if not ip:
        _rp(results_dir, pt["id"]).write_text(json.dumps({"metadata": {"timestamp": datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
            "latitude": pt["lat"], "longitude": pt["lon"], "heading": pt["heading"],
            "street_name": pt["street_name"], "highway_type": pt["highway_type"],
            "edge_id": pt["edge_id"], "source_image": None, "model": MODEL_ID,
            "device": _device, "status": "no_streetview"},
            "scene_analysis": None, "nearby_landmarks": []}, indent=2, ensure_ascii=False), encoding="utf-8")
        return "no_sv"
    sr = analyze_image(ip)
    _rp(results_dir, pt["id"]).write_text(json.dumps({"metadata": {"timestamp": datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
        "latitude": pt["lat"], "longitude": pt["lon"], "heading": pt["heading"],
        "street_name": pt["street_name"], "highway_type": pt["highway_type"],
        "edge_id": pt["edge_id"], "source_image": ip.name, "model": MODEL_ID,
        "device": _device, "latency_ms": sr.pop("_latency_ms", None), "status": "ok"},
        "scene_analysis": sr, "nearby_landmarks": fetch_nearby_landmarks(pt["lat"], pt["lon"])}, indent=2, ensure_ascii=False), encoding="utf-8")
    return "ok"

def analyze_pipeline(sample_points, images_dir, results_dir, workers=1):
    pending = [p for p in sample_points if not _rp(results_dir, p["id"]).exists()]
    log.info("Total: %d  Pending: %d  Workers: %d", len(sample_points), len(pending), workers)
    if not pending: return
    s = {"ok": 0, "no_sv": 0, "err": 0}
    if workers <= 1:
        for pt in tqdm(pending, desc="Analysing", unit="loc"):
            try:
                status = _analyze_one(pt, images_dir, results_dir)
                s[status] = s.get(status, 0) + 1
            except Exception as exc:
                log.error("Error %s: %s", pt["id"], exc); s["err"] += 1
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_analyze_one, pt, images_dir, results_dir): pt for pt in pending}
            for f in tqdm(as_completed(futs), total=len(futs), desc="Analysing", unit="loc"):
                try:
                    status = f.result()
                    s[status] = s.get(status, 0) + 1
                except Exception as exc:
                    pt = futs[f]
                    log.error("Error %s: %s", pt["id"], exc); s["err"] += 1
    log.info("Done: %s", s)

def print_summary(results_dir):
    files = sorted(results_dir.glob("*_analysis.json"))
    ok = ns = err = 0
    for f in files:
        try:
            st = json.loads(f.read_text(encoding="utf-8"))["metadata"].get("status", "ok")
            ok += 1 if st == "ok" else 0; ns += 1 if st == "no_streetview" else 0; err += 1 if st != "ok" and st != "no_streetview" else 0
        except Exception: err += 1
    log.info("Results: %d  OK: %d  NoSV: %d  Errors: %d", len(files), ok, ns, err)

def _parse_args():
    p = argparse.ArgumentParser(description="StreetPLM — Qwen3-VL-8B-Instruct")
    p.add_argument("--output-dir", default=os.environ.get("OUTPUT_DIR", "/teamspace/studios/this_studio/StreetPLM"))
    p.add_argument("--bbox", nargs=4, type=float, metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"))
    p.add_argument("--spacing", type=int, default=SAMPLE_DISTANCE_M,
                   help=f"Sample spacing along walk edges in metres (default: {SAMPLE_DISTANCE_M})")
    p.add_argument("--from-zone", nargs="?", const="http://127.0.0.1:8000", metavar="SERVER_URL",
                   help="Fetch bbox from running backend GET /api/zone/current")
    p.add_argument("--no-resume", action="store_true", help="Regenerate sample points even if file exists")
    p.add_argument("--trial", action="store_true"); p.add_argument("--trial-index", type=int, default=0)
    p.add_argument("--trial-lat", type=float); p.add_argument("--trial-lon", type=float)
    p.add_argument("--trial-heading", type=float)
    p.add_argument("--workers", type=int, default=6, help="Parallel download workers (default: 6)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--fetch-only", action="store_true", help="Download images only, skip model + analysis")
    g.add_argument("--analyze-only", action="store_true", help="Skip download, analyse existing images only")
    return p.parse_args()

def main():
    args = _parse_args()
    assert STREETVIEW_API_KEY, "Set GOOGLE_STREETVIEW_API_KEY"
    output_root = Path(args.output_dir)
    images_dir, results_dir = output_root / "images", output_root / "results"
    for d in (images_dir, results_dir): d.mkdir(parents=True, exist_ok=True)
    log.info("Output: %s  Model: %s", output_root, MODEL_ID)

    if args.from_zone is not None:
        bbox = fetch_zone_from_server(args.from_zone)
    elif args.bbox:
        bbox = {"min_lon": args.bbox[0], "min_lat": args.bbox[1],
                "max_lon": args.bbox[2], "max_lat": args.bbox[3]}
    else:
        bbox = DEFAULT_BBOX
    log.info("Study area: lon [%.4f, %.4f]  lat [%.4f, %.4f]",
             bbox["min_lon"], bbox["max_lon"], bbox["min_lat"], bbox["max_lat"])

    sample_points = query_and_sample_points(
        output_root, bbox, spacing_m=args.spacing, resume=not args.no_resume
    )

    if args.fetch_only:
        fetch_images(sample_points, images_dir)
        return

    load_model()
    if args.trial:
        run_trial(args, images_dir)
        return
    workers = max(1, args.workers)
    if args.analyze_only:
        analyze_pipeline(sample_points, images_dir, results_dir, workers=workers)
    else:
        fetch_images(sample_points, images_dir)
        analyze_pipeline(sample_points, images_dir, results_dir, workers=workers)
    print_summary(results_dir)

if __name__ == "__main__":
    main()
