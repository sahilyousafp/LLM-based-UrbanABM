"""
street_plm_job.py
=================
LightningAI Job version of StreetPLM_Eixample.ipynb.

What this script does:
  1. Queries Overture Maps pedestrian walk edges from BigQuery for the
     2 km x 2 km Eixample study area (same filter as Backend/Environment/overture_to_duckdb.py)
  2. Samples points every 250 m along each walk edge, heading aligned with street direction
  3. Checks Street View availability and fetches one 640x640 image per sample point
  4. Runs Qwen2.5-VL-3B-Instruct (full-image urban analysis) on each image
  5. Saves one JSON + JPG per location to OUTPUT_DIR
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
# Bootstrap: ensure accelerate and osmnx are installed
# ---------------------------------------------------------------------------
try:
    import accelerate  # noqa: F401
except ImportError:
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-q", "accelerate>=0.30"]
    )

try:
    import osmnx as _osmnx_check  # noqa: F401
except ImportError:
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-q", "osmnx"]
    )

try:
    import bitsandbytes as _bnb_check  # noqa: F401
except ImportError:
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-q", "bitsandbytes>=0.41"]
    )

import numpy as np
import pyproj
import requests
import torch
from google.cloud import bigquery
from huggingface_hub import login
from PIL import Image as _PILImage
from pydantic import BaseModel, Field, field_validator, FieldValidationInfo
from typing import Any
from shapely import wkt as shapely_wkt
from shapely.geometry import LineString  # noqa: F401
import geopandas as gpd
from tqdm.auto import tqdm
from dotenv import load_dotenv
from transformers import Qwen2_5_VLForConditionalGeneration, AutoProcessor, BitsAndBytesConfig

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

# Bounding box for study area
# Previous (2 km x 2 km centred on Passeig de Gracia):
# BBOX = {"min_lon": 2.1500, "min_lat": 41.3862, "max_lon": 2.1740, "max_lat": 41.4042}
BBOX = {
    "min_lon": 2.160677,     # shifted ~500 m west
    "min_lat": 41.391976,    # shifted ~500 m south
    "max_lon": 2.172096,
    "max_lat": 41.402141,    # shifted ~250 m north
}

BIGQUERY_PROJECT  = "bigquery-public-data"
OVERTURE_DATASET  = "overture_maps"

SAMPLE_DISTANCE_M = 200
SV_SIZE           = "640x640"
SV_FOV            = 90
SV_PITCH          = 0
SV_RADIUS         = 50
_MODEL_IDS = {
    "3b": "Qwen/Qwen2.5-VL-3B-Instruct",
    "7b": "Qwen/Qwen2.5-VL-7B-Instruct",
}
MODEL_ID          = _MODEL_IDS["7b"]   # overridden by --model-size arg
# Qwen2.5-VL uses dynamic-resolution patches (min/max pixels) instead of tiles.
# Scaled in load_model() based on available VRAM.
MAX_PIXELS        = 1280 * 28 * 28   # ~1 M pixels — good for L4/A100 (24 GB+)
MAX_NEW_TOKENS    = 900               # 7B richer output: 6 fields × 2-3 obs × ~50 tok ≈ 750 + headroom

UTM31N = "EPSG:32631"
_SCRIPT_DIR = Path(__file__).parent

# Optional: path to a DuckDB containing an OSM/Overture 'places' table.
# Set as a Lightning AI Secret or pass --landmark-db on the command line.
LANDMARK_DB_PATH = os.environ.get("LANDMARK_DB_PATH", "")


_SV_BASE   = "https://maps.googleapis.com/maps/api/streetview"
_META_BASE = "https://maps.googleapis.com/maps/api/streetview/metadata"

# ---------------------------------------------------------------------------
# Pydantic schema — zone-aware lists, one list per perceptual category.
# The model outputs zone-prefixed strings ("left: ... | center: ...") which
# are parsed into structured list entries by _normalise_result before
# reaching this schema.
# ---------------------------------------------------------------------------

class StreetSceneAnalysis(BaseModel):
    """Zone-aware schema: scene string + 5 zone-attribute lists + OCR list."""

    scene            : str  = "unknown"
    lighting         : list = Field(default_factory=list)
    spatial_character: list = Field(default_factory=list)
    crowdedness      : list = Field(default_factory=list)
    greenery         : list = Field(default_factory=list)
    street_amenities : list = Field(default_factory=list)
    visible_text     : list = Field(default_factory=list)

    @field_validator("scene", mode="before")
    @classmethod
    def _coerce_scene(cls, v):
        s = str(v).strip() if v else "unknown"
        return s or "unknown"

    @field_validator(
        "lighting", "spatial_character", "crowdedness", "greenery",
        "street_amenities", "visible_text",
        mode="before",
    )
    @classmethod
    def _coerce_to_list(cls, v):
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            return [v]
        return []


_KEY_ALIASES = {
    "scene"            : "scene",
    "scene_description": "scene",
    "overview"         : "scene",
    "summary"          : "scene",
    "lighting"         : "lighting",
    "light"            : "lighting",
    "light_quality"    : "lighting",
    "spatial"          : "spatial_character",
    "spatial_character": "spatial_character",
    "space"            : "spatial_character",
    "street_space"     : "spatial_character",
    "crowding"         : "crowdedness",
    "crowdedness"      : "crowdedness",
    "crowd"            : "crowdedness",
    "pedestrians"      : "crowdedness",
    "people"           : "crowdedness",
    "greenery"         : "greenery",
    "vegetation"       : "greenery",
    "plants"           : "greenery",
    "trees"            : "greenery",
    "amenities"        : "street_amenities",
    "street_amenities" : "street_amenities",
    "furniture"        : "street_amenities",
    "street_furniture" : "street_amenities",
    "fixtures"         : "street_amenities",
    "visible_text"     : "visible_text",
    "text"             : "visible_text",
    "signs"            : "visible_text",
    "ocr"              : "visible_text",
    "signage"          : "visible_text",
}

# ---------------------------------------------------------------------------
# Grammar-constrained generation schema (used with lm-format-enforcer)
# ---------------------------------------------------------------------------
# minItems:1 on zone attributes makes it impossible for the model to emit [].
# visible_text is 0+ (some locations genuinely have no readable text).
# Items are typed as generic objects so the model fills fields freely from the
# few-shot example rather than being blocked by strict property enums.
_GENERATION_SCHEMA: dict = {
    "type": "object",
    "required": [
        "lighting", "spatial_character", "crowdedness", "greenery",
        "street_amenities", "visible_text", "scene",
    ],
    "properties": {
        "scene": {"type": "string"},
        **{
            attr: {
                "type": "array",
                "minItems": 1,
                "maxItems": 3,
                "items": {"type": "object"},
            }
            for attr in (
                "lighting", "spatial_character", "crowdedness",
                "greenery", "street_amenities",
            )
        },
        "visible_text": {
            "type": "array",
            "minItems": 0,
            "maxItems": 4,
            "items": {"type": "object"},
        },
    },
}


# Google Street View watermark: "Google", "© Google", "© 2024 Google Maps", etc.
_GOOGLE_WATERMARK_RE = re.compile(
    r"^(©\s*)?(20\d{2}\s+)?google(\s+maps)?[\s.,]*$",
    re.IGNORECASE,
)

# License plate patterns — these typically appear on vehicles in the lower frame.
# Covers common EU/Spanish formats: "VJL 360", "1234 ABC", "AB·1234·CD", etc.
_LICENSE_PLATE_RE = re.compile(
    r"^[A-Z]{1,4}[\s·\-]?\d{2,4}[\s·\-]?[A-Z]{0,3}$"
    r"|^\d{4}[\s·\-][A-Z]{2,3}$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Zone-string parsers
# The model outputs strings like "left: tree shade, dim | center: direct sun, bright"
# These parsers convert them into the zone-aware list-of-dicts schema.
# ---------------------------------------------------------------------------

_ZONE_NAMES       = frozenset({"far_left", "left", "center", "right", "far_right"})
_LIGHTING_CONDS   = frozenset({"dark", "dim", "adequate", "bright"})
_COVERAGE_LEVELS  = frozenset({"none", "sparse", "moderate", "dense"})
_PRESENCE_LEVELS  = frozenset({"none", "few", "several", "many"})
_DENSITY_LEVELS   = frozenset({"empty", "sparse", "moderate", "dense"})
_WIDTH_LEVELS     = frozenset({"narrow", "moderate", "wide"})
_ENCLOSURE_LEVELS = frozenset({"open", "semi", "enclosed"})
_LANE_TYPES       = frozenset({"sidewalk", "road", "shared", "plaza"})
_CROSSING_TYPES   = frozenset({"none", "zebra", "signalised", "signalized", "crosswalk", "crossing"})


def _split_zone_segments(s: str) -> list[dict]:
    """Split 'left: desc | center: desc' into [{"zone":..., "desc":...}, ...]."""
    if not s or s.lower().strip() in ("none", "unknown", ""):
        return []
    out = []
    for seg in re.split(r"\s*[|;]\s*", s.strip()):
        seg = seg.strip()
        if not seg:
            continue
        if ":" in seg:
            zone_part, desc_part = seg.split(":", 1)
            zone = zone_part.strip().lower().replace(" ", "_")
            if zone in _ZONE_NAMES:
                out.append({"zone": zone, "desc": desc_part.strip()})
                continue
        # No zone prefix — assign to center
        out.append({"zone": "center", "desc": seg})
    return out


def _extract_enum(desc: str, options: frozenset, default: str) -> tuple[str, str]:
    """Return (matched_enum, desc_with_enum_removed). Case-insensitive."""
    for opt in options:
        if re.search(r"\b" + re.escape(opt) + r"\b", desc, re.IGNORECASE):
            cleaned = re.sub(r"\b" + re.escape(opt) + r"\b", "", desc, flags=re.IGNORECASE)
            return opt, re.sub(r"[,\s]+$", "", cleaned.strip()).strip(",").strip()
    return default, desc


def _parse_lighting(s: str) -> list:
    out = []
    for seg in _split_zone_segments(s):
        cond, element = _extract_enum(seg["desc"], _LIGHTING_CONDS, "adequate")
        # If stripping the condition keyword left nothing, use the raw desc as element
        element = element.strip() or seg["desc"].strip() or "natural light"
        out.append({"zone": seg["zone"], "element": element, "condition": cond})
    return out


def _parse_spatial(s: str) -> list:
    out = []
    for seg in _split_zone_segments(s):
        d = seg["desc"].lower()
        width     = next((w for w in _WIDTH_LEVELS     if w in d), "moderate")
        enclosure = next((e for e in _ENCLOSURE_LEVELS if e in d), "semi")
        lane_type = next((lt for lt in _LANE_TYPES     if lt in d), "road")
        _raw_crossing = next((ct for ct in _CROSSING_TYPES if ct in d), "none")
        crossing  = "zebra" if _raw_crossing in ("crosswalk", "crossing") else _raw_crossing
        passable  = "obstructed" if re.search(r"obstruct|block", d) else "clear"
        out.append({
            "zone": seg["zone"], "width": width, "enclosure": enclosure,
            "passability": passable, "lane_type": lane_type, "crossing": crossing,
        })
    return out


def _parse_crowdedness(s: str) -> list:
    out = []
    for seg in _split_zone_segments(s):
        density = next((lv for lv in _DENSITY_LEVELS if lv in seg["desc"].lower()), "sparse")
        out.append({"zone": seg["zone"], "density_level": density})
    return out


def _split_elements(desc: str) -> list[str]:
    """Split 'plane trees, potted plants and bollards' into individual element strings."""
    parts = re.split(r"\s*(?:,|and|&)\s*", desc, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]


def _parse_greenery(s: str) -> list:
    out = []
    for seg in _split_zone_segments(s):
        coverage, elements_str = _extract_enum(seg["desc"], _COVERAGE_LEVELS, "moderate")
        elements_str = elements_str.strip()
        # Guard: if stripping coverage left nothing (desc WAS just "moderate" etc.) use raw desc
        if not elements_str or elements_str.lower() in _COVERAGE_LEVELS:
            elements_str = seg["desc"]
        for element in _split_elements(elements_str):
            # Skip if element is itself just a coverage keyword
            if element and element.lower() not in _COVERAGE_LEVELS:
                out.append({"zone": seg["zone"], "element": element, "coverage": coverage})
    return out


def _parse_amenities(s: str) -> list:
    out = []
    for seg in _split_zone_segments(s):
        presence, elements_str = _extract_enum(seg["desc"], _PRESENCE_LEVELS, "few")
        for element in _split_elements(elements_str or seg["desc"]):
            if element:
                out.append({"zone": seg["zone"], "element": element, "presence": presence})
    return out


def _parse_visible_text(s: str) -> list:
    if not s or s.lower().strip() in ("none", "unknown", ""):
        return []
    segs = _split_zone_segments(s)
    if segs:
        entries = [{"text": seg["desc"], "zone": seg["zone"], "type": "sign"}
                   for seg in segs if seg["desc"]]
    else:
        entries = [{"text": t.strip(), "zone": "center", "type": "sign"}
                   for t in s.split(",") if t.strip()]
    # Filter Google watermarks and licence plates
    kept = []
    for e in entries:
        txt = e.get("text", "")
        if not txt or _GOOGLE_WATERMARK_RE.match(txt) or _LICENSE_PLATE_RE.match(txt):
            continue
        kept.append(e)
    return kept


_STR_PARSERS = {
    "lighting"         : _parse_lighting,
    "spatial_character": _parse_spatial,
    "crowdedness"      : _parse_crowdedness,
    "greenery"         : _parse_greenery,
    "street_amenities" : _parse_amenities,
    "visible_text"     : _parse_visible_text,
}


def _normalise_result(raw: dict) -> dict:
    normalised = {}
    for k, v in raw.items():
        canonical = _KEY_ALIASES.get(k.strip().lower())
        if canonical:
            normalised[canonical] = v

    # Convert zone-prefixed strings → structured lists
    parsed: dict = {}
    for field, value in normalised.items():
        parser = _STR_PARSERS.get(field)
        if parser and isinstance(value, str):
            parsed[field] = parser(value)
        else:
            parsed[field] = value

    try:
        return StreetSceneAnalysis(**parsed).model_dump()
    except Exception:
        return StreetSceneAnalysis().model_dump()


# ---------------------------------------------------------------------------
# PLM prompt
# ---------------------------------------------------------------------------

# Full nested-JSON prompt for Qwen2.5-VL-7B.
# The 7B model follows complex schemas and filled examples without copying values.
# Each list field produces one entry per zone per distinct element.
_SCENE_PROMPT = (
    "You are an expert urban perception analyst. Study this Barcelona street-view image carefully.\n"
    "Zones left-to-right: far_left | left | center | right | far_right\n\n"
    "Output a JSON object with these 7 fields (scene last):\n"
    "  lighting          - [{zone, element: describe the specific light source, condition: dark|dim|adequate|bright}]\n"
     "  spatial_character - [{zone, width: narrow|moderate|wide, enclosure: open|semi|enclosed,\n"
     "                         passability: clear|obstructed, lane_type: sidewalk|road|shared|plaza,\n"
     "                         crossing: none|zebra|signalised,\n"
     "                         architectural_style: neo_gothic|modernist|contemporary|neoclassical|vernacular|eclectic|art_deco|other,\n"
     "                         building_condition: excellent|good|fair|poor|under_construction,\n"
     "                         storefront_type: retail|restaurant|cafe|office|residential|hotel|vacant|cultural|industrial|other,\n"
     "                         architectural_details: free text describing balconies, cornices, arches, columns, ornamentation etc.}]\n"
    "  crowdedness       - [{zone, density_level: empty|sparse|moderate|dense}]\n"
    "  greenery          - [{zone, element: specific plant/tree type and description, coverage: none|sparse|moderate|dense}]\n"
    "  street_amenities  - [{zone, element: specific object type, material and colour, presence: none|few|several|many}]\n"
    "  visible_text      - [{text: exact readable string, zone, type: sign|label|graffiti}]\n"
    "  scene             - one-sentence overview of this specific street\n\n"
    "Rules:\n"
    "  * Cover all clearly visible zones - 1-3 entries per field\n"
    "  * Multiple distinct elements in the same zone = multiple list entries\n"
    "  * element fields: name material, colour, style, scale (e.g. 'grey cast-iron double-arm lamp post')\n"
    "  * visible_text: upper 90% of frame only\n"
    "  * Output only the JSON object - no prose, no markdown\n\n"
    "Example (DIFFERENT image - for schema format only, do not copy values):\n"
    '{"lighting":[{"zone":"left","element":"dappled shade from dense tree canopy","condition":"dim"},'
    '{"zone":"center","element":"direct overhead midday sun","condition":"bright"}],'
     '"spatial_character":[{"zone":"left","width":"narrow","enclosure":"enclosed","passability":"clear","lane_type":"sidewalk","crossing":"none",'
     '"architectural_style":"modernist","building_condition":"good","storefront_type":"retail","architectural_details":"wrought-iron balconies, stone relief carvings"},'
     '{"zone":"center","width":"moderate","enclosure":"semi","passability":"clear","lane_type":"road","crossing":"zebra","architectural_style":"eclectic","building_condition":"fair","storefront_type":"cafe","architectural_details":"awning, painted tile facade"}],'
    '"crowdedness":[{"zone":"left","density_level":"sparse"},{"zone":"center","density_level":"moderate"}],'
    '"greenery":[{"zone":"left","element":"mature London plane trees with mottled grey-green bark","coverage":"dense"},'
    '{"zone":"right","element":"terracotta-potted rosemary shrubs on window ledges","coverage":"sparse"}],'
    '"street_amenities":[{"zone":"left","element":"ornate cast-iron double-arm street lamp with frosted globe","presence":"few"},'
    '{"zone":"left","element":"dark grey granite kerb-side bench with metal armrests","presence":"few"},'
    '{"zone":"center","element":"yellow painted concrete bollard","presence":"several"},'
    '{"zone":"right","element":"dark green metal municipal waste bin with foot-pedal lid","presence":"few"}],'
    '"visible_text":[{"text":"MERCAT","zone":"far_left","type":"sign"}],'
    '"scene":"Tree-lined residential street with moderate pedestrian activity and parked vehicles."}\n\n'
    "Now analyse THIS image. Output only JSON:"
)


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

_ECHO_SENTINELS = frozenset({
    # Echoed the top-level format instruction literally
    "left: ... | center: ... | right: ...",
    # Echoed the bullet-point descriptions as values
    "light source and brightness (dark/dim/adequate/bright) per zone",
    "width (narrow/moderate/wide), enclosure (open/semi/enclosed), surface (sidewalk/road/plaza), any crossings per zone",
    "pedestrian density (empty/sparse/moderate/dense) per zone",
    "specific plant types and coverage (sparse/moderate/dense) per zone. write 'none' if absent.",
    "specific street objects — type, material, colour per zone. write 'none' if absent.",
    "readable text in the upper half of the image. write 'none' if absent.",
    "one sentence describing this specific street.",
    # Echoed enum option strings
    "empty/sparse/moderate/dense",
    "dark/dim/adequate/bright",
    "narrow/moderate/wide",
})


def _model_echoed_template(parsed: dict) -> bool:
    """Return True if any nested value in parsed matches a sentinel string.

    Recurses into lists so it catches echoed values inside array fields like
    result["greenery"][0]["element"] = "mature plane trees..." — the old flat
    check only looked one level deep and missed all list-of-dict fields.
    """
    def _has_sentinel(obj) -> bool:
        if isinstance(obj, str):
            return obj.lower() in _ECHO_SENTINELS
        if isinstance(obj, dict):
            return any(_has_sentinel(sv) for sv in obj.values())
        if isinstance(obj, list):
            return any(_has_sentinel(item) for item in obj)
        return False

    return any(_has_sentinel(v) for v in parsed.values())


def _extract_first_json_obj(text: str) -> str | None:
    """Return the first syntactically balanced {...} substring.

    Handles junk after the closing brace (e.g. repeated }}} from min_new_tokens
    padding) without being fooled by greedy regex that spans to the last }.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth, in_str, escape = 0, False, False
    for i, ch in enumerate(text[start:], start):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _parse_scene_json(text: str) -> dict:
    candidate = None
    for attempt in (text, text + "}"):
        try:
            candidate = json.loads(attempt)
            break
        except json.JSONDecodeError:
            pass
    if candidate is None:
        # brace-counting extractor: immune to junk/repeated-} after the object
        obj_str = _extract_first_json_obj(text)
        if obj_str:
            try:
                candidate = json.loads(obj_str)
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
        if _model_echoed_template(candidate):
            log.warning("Model echoed prompt template — returning default (all unknown)")
            return StreetSceneAnalysis().model_dump()
        return _normalise_result(candidate)
    return StreetSceneAnalysis().model_dump()


# ---------------------------------------------------------------------------
# Model globals (populated by load_model())
# ---------------------------------------------------------------------------

from typing import Optional
_model     : Optional[Qwen2_5_VLForConditionalGeneration] = None
_processor : Optional[AutoProcessor]                      = None
_device    : str   = "cpu"
_dtype            = torch.float32


def load_model():
    global _model, _processor, _device, _dtype, MAX_PIXELS

    login(token=HF_TOKEN, add_to_git_credential=False)

    _device = "cuda" if torch.cuda.is_available() else "cpu"

    if _device == "cuda":
        props   = torch.cuda.get_device_properties(0)
        vram_gb = props.total_memory / 1e9
        cc      = (props.major, props.minor)   # compute capability
        # BF16 tensor cores require Ampere (sm80+). Turing (T4, sm75) must use FP16.
        _dtype  = torch.bfloat16 if cc >= (8, 0) else torch.float16
        log.info("GPU: %s  (%.1f GB)  sm%d%d  dtype=%s",
                 props.name, vram_gb, cc[0], cc[1], _dtype)
        # T4 reports ~15.84 GB — use 15.0 as threshold to catch it correctly.
        # With 7B in 4-bit (~4.5 GB weights) the remaining budget is generous.
        if vram_gb >= 23:
            MAX_PIXELS = 1280 * 28 * 28   # ~1 M  — A100 / L4
        elif vram_gb >= 15:
            MAX_PIXELS = 784 * 28 * 28    # ~615 K — T4 (16 GB)
        else:
            MAX_PIXELS = 512 * 28 * 28    # ~401 K — smaller cards
        os.environ.setdefault("PYTORCH_ALLOC_CONF", "expandable_segments:True")
    else:
        _dtype = torch.float32
        log.warning("No GPU detected -- inference will be very slow on CPU")
        MAX_PIXELS = 256 * 28 * 28        # CPU: use minimum resolution

    log.info("max_pixels: %d  (%d vision tokens budget)", MAX_PIXELS, MAX_PIXELS // (28 * 28))
    log.info("Loading %s ...", MODEL_ID)

    _processor = AutoProcessor.from_pretrained(
        MODEL_ID,
        token      = HF_TOKEN,
        use_fast   = True,
        min_pixels = 256 * 28 * 28,
        max_pixels = MAX_PIXELS,
    )

    # 7B in full precision ≈ 14 GB — too tight even on 16 GB T4.
    # 4-bit NF4 brings weights to ~4.5 GB; compute dtype matches the GPU's
    # native half-precision (float16 on T4/Turing, bfloat16 on Ampere+).
    is_7b = "7b" in MODEL_ID.lower() or "7B" in MODEL_ID
    if is_7b and _device == "cuda":
        bnb_cfg = BitsAndBytesConfig(
            load_in_4bit              = True,
            bnb_4bit_compute_dtype    = _dtype,   # fp16 on T4, bf16 on Ampere+
            bnb_4bit_quant_type       = "nf4",
            bnb_4bit_use_double_quant = True,
        )
        log.info("Using 4-bit NF4 quantization (compute dtype: %s)", _dtype)
    else:
        bnb_cfg = None

    _model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        MODEL_ID,
        token                = HF_TOKEN,
        torch_dtype          = _dtype,
        device_map           = "auto",
        low_cpu_mem_usage    = True,
        quantization_config  = bnb_cfg,
    )
    _model.eval()

    if _device == "cuda":
        torch.cuda.empty_cache()
        gc.collect()

    log.info("%s loaded and ready", MODEL_ID)


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

def _infer_scene(image, greedy: bool = False):
    if _processor is None or _model is None:
        raise RuntimeError("Model not loaded — call load_model() first.")

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image"},
                {"type": "text", "text": _SCENE_PROMPT},
            ],
        }
    ]
    text = str(_processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    ))
    # Deep prime: force the model to start the first array entry directly.
    # The 7B model outputs nested JSON; priming past the opening brace prevents
    # it emitting an empty [] before any content.
    _PRIME = '{"lighting":[{'
    text += _PRIME

    # Qwen2.5-VL processor expects text as a list and images as a list
    inputs = _processor(
        text=[text],
        images=[image],
        padding=True,
        return_tensors="pt",
    )

    log.debug("Processor output keys: %s", list(inputs.keys()))
    for k in list(inputs.keys()):
        v = inputs[k]
        if hasattr(v, "to"):
            inputs[k] = v.to(_device, dtype=_dtype) if v.is_floating_point() else v.to(_device)

    gen_kwargs: dict = dict(
        max_new_tokens     = MAX_NEW_TOKENS,
        do_sample          = not greedy,
        temperature        = 0.3,    # 7B handles sampling well; adds description variety
        top_p              = 0.95,
        repetition_penalty = 1.1,
        eos_token_id       = _processor.tokenizer.eos_token_id,
        pad_token_id       = _processor.tokenizer.pad_token_id,
    )
    if greedy:
        gen_kwargs.pop("temperature", None)
        gen_kwargs.pop("top_p", None)

    with torch.no_grad():
        gen_ids = _model.generate(**inputs, **gen_kwargs)

    new_ids  = gen_ids[:, inputs["input_ids"].shape[1]:]
    raw_text = _PRIME + _processor.tokenizer.batch_decode(new_ids, skip_special_tokens=True)[0]

    if _device == "cuda":
        del gen_ids, new_ids, inputs
        torch.cuda.empty_cache()
        gc.collect()

    return _parse_scene_json(raw_text), raw_text


def _is_blank_image(img: _PILImage.Image, std_threshold: float = 18.0) -> bool:
    """Return True if the image is near-uniform in brightness (grey SV placeholder)."""
    arr = np.array(img.convert("L"), dtype=np.float32)
    return float(arr.std()) < std_threshold


def analyze_image(image_path):
    """
    Run the VLM on a full street-view image.
    Returns a dict with scene analysis fields and _latency_ms.
    Skips grey/blank placeholder images before sending to the model.
    """
    img = _PILImage.open(image_path).convert("RGB")
    if _is_blank_image(img):
        log.warning("  Blank/placeholder image skipped: %s", image_path)
        result = StreetSceneAnalysis().model_dump()
        result["_latency_ms"] = 0.0
        result["_blank"]      = True
        return result
    _MAX_ATTEMPTS = 4
    t0 = time.perf_counter()
    result, raw = None, ""
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        greedy = (attempt == _MAX_ATTEMPTS)   # last attempt: greedy decoding
        result, raw = _infer_scene(img, greedy=greedy)
        populated = sum(
            1 for k, v in result.items()
            if not k.startswith("_") and v not in ("unknown", "none", "", None, [], {})
        )
        if populated >= 3 or attempt == _MAX_ATTEMPTS:
            break
        log.warning("  Attempt %d/%d: only %d fields populated — retrying", attempt, _MAX_ATTEMPTS, populated)
    latency_ms = (time.perf_counter() - t0) * 1000

    total = sum(1 for k in result if not k.startswith("_"))
    log.info("  Scene analysis: %d ms  %d/%d fields populated", int(latency_ms), populated, total)
    if populated == 0:
        log.warning("  0 fields — raw VLM output: %r", raw[:500])

    result["_latency_ms"] = round(latency_ms, 1)
    return result


# ---------------------------------------------------------------------------
# Street View helpers
# ---------------------------------------------------------------------------

def sv_available(lat: float, lon: float) -> bool:
    r = requests.get(_META_BASE, params={
        "location": f"{lat},{lon}",
        "radius"  : SV_RADIUS,
        "source"  : "outdoor",
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
            "source"  : "outdoor",
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


def _bearing_label(deg: float) -> str:
    """Convert a bearing in degrees to an 8-point cardinal label."""
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[int((deg % 360 + 22.5) / 45) % 8]


def fetch_nearby_landmarks(
    lat: float,
    lon: float,
    db_path: str = "",
    radius_m: float = 150.0,
    max_results: int = 8,
) -> list:
    """Query a DuckDB Overture/OSM database for named amenities near (lat, lon).

    Auto-detects table and column names so it works with both eixample_overture.duckdb
    (table: amenities, cols: lat/lon/amenity) and eixample_osm.duckdb variants.
    Returns [] silently if db_path is unset or the query fails.
    """
    resolved = db_path or LANDMARK_DB_PATH
    if not resolved:
        return []
    try:
        import duckdb
        con = duckdb.connect(resolved, read_only=True)
        try:
            con.execute("LOAD spatial")
        except Exception:
            pass

        # --- discover table ---
        tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
        preferred = ["amenities", "places", "poi", "points_of_interest", "nodes", "features"]
        table = next((t for t in preferred if t in tables), tables[0] if tables else None)
        if table is None:
            log.warning("Landmark DB has no tables: %s", resolved)
            con.close()
            return []

        # --- discover columns ---
        col_map = {r[0].lower(): r[0] for r in con.execute(f'DESCRIBE "{table}"').fetchall()}
        log.debug("Landmark table=%s  cols=%s", table, sorted(col_map))

        # name column
        name_col = next((col_map[c] for c in ("name", "display_name") if c in col_map), None)
        if name_col is None:
            log.warning("No name column in '%s' (cols: %s)", table, sorted(col_map))
            con.close()
            return []

        # category column (Overture uses 'amenity'; OSM variants use 'type'/'class')
        cat_col  = next((col_map[c] for c in ("amenity", "category", "type", "class", "subtype") if c in col_map), None)
        cat_expr = f'CAST("{cat_col}" AS VARCHAR)' if cat_col else "'unknown'"

        # coordinate columns
        delta = radius_m / 111_000.0
        if "lat" in col_map and "lon" in col_map:
            lat_c, lon_c = col_map["lat"], col_map["lon"]
        elif "latitude" in col_map and "longitude" in col_map:
            lat_c, lon_c = col_map["latitude"], col_map["longitude"]
        else:
            log.warning("No lat/lon columns in '%s'", table)
            con.close()
            return []

        rows = con.execute(f"""
            SELECT "{name_col}", {cat_expr},
                   6371000 * acos(LEAST(1.0,
                       cos(radians({lat})) * cos(radians("{lat_c}")) *
                       cos(radians("{lon_c}") - radians({lon})) +
                       sin(radians({lat})) * sin(radians("{lat_c}"))
                   )) AS dist_m,
                   degrees(atan2("{lon_c}" - {lon}, "{lat_c}" - {lat})) AS bearing_deg
            FROM   "{table}"
            WHERE  "{name_col}" IS NOT NULL
              AND  "{lat_c}" BETWEEN {lat - delta} AND {lat + delta}
              AND  "{lon_c}" BETWEEN {lon - delta} AND {lon + delta}
            ORDER  BY dist_m
            LIMIT  {max_results}
        """).fetchall()
        con.close()
        return [
            {
                "name"      : r[0],
                "category"  : r[1] or "unknown",
                "distance_m": round(r[2], 1),
                "bearing"   : _bearing_label(r[3]),
            }
            for r in rows
            if r[2] is not None and r[2] <= radius_m
        ]
    except Exception as exc:
        log.warning("Landmark query failed (%s): %s", resolved, exc)
        return []


# ---------------------------------------------------------------------------
# BigQuery / point sampling
# ---------------------------------------------------------------------------

def _grid_sample_bbox(output_root: Path) -> list:
    """Fallback: uniform grid of sample points across BBOX — no BigQuery needed.
    Uses 50 m spacing with two headings (0° north, 90° east) per location.
    """
    GRID_STEP_M = 50
    mid_lat     = (BBOX["min_lat"] + BBOX["max_lat"]) / 2
    lat_step    = GRID_STEP_M / 111_320.0
    lon_step    = GRID_STEP_M / (111_320.0 * np.cos(np.radians(mid_lat)))

    points, seen = [], set()
    lat = BBOX["min_lat"]
    while lat <= BBOX["max_lat"] + 1e-9:
        lon = BBOX["min_lon"]
        while lon <= BBOX["max_lon"] + 1e-9:
            for heading in (0.0, 90.0):
                key = (round(lat, 5), round(lon, 5), int(heading))
                if key in seen:
                    lon += lon_step
                    continue
                seen.add(key)
                rlat, rlon = round(lat, 6), round(lon, 6)
                points.append({
                    "id"               : f"{rlat:.6f}_{rlon:.6f}_h{int(heading)}",
                    "lat"              : rlat,
                    "lon"              : rlon,
                    "heading"          : heading,
                    "street_name"      : "",
                    "highway_type"     : "unknown",
                    "edge_id"          : "grid",
                    "dist_along_edge_m": None,
                })
            lon += lon_step
        lat += lat_step

    log.info("Grid fallback: %d sample points (%d m spacing) across BBOX", len(points), GRID_STEP_M)
    points_file = output_root / "sample_points.json"
    points_file.write_text(json.dumps(points, indent=2, ensure_ascii=False), encoding="utf-8")
    return points


def _sample_edges(edges, output_root: Path) -> list:
    """Interpolate sample points along a list of Shapely LineString edges.

    Each edge dict must have keys: geometry (LineString, projected metric CRS),
    name (str), highway_type (str), edge_id (str).
    Returns the point list and writes sample_points.json.
    """
    transformer = pyproj.Transformer.from_crs(UTM31N, "EPSG:4326", always_xy=True)
    sample_points = []
    seen_cells    = set()

    for edge in edges:
        geom    = edge["geometry"]
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

            sample_points.append({
                "id"               : f"{lat:.6f}_{lon:.6f}",
                "lat"              : float(lat),
                "lon"              : float(lon),
                "heading"          : round(float(heading), 1),
                "street_name"      : edge["name"],
                "highway_type"     : edge["highway_type"],
                "edge_id"          : edge["edge_id"],
                "dist_along_edge_m": round(float(dist), 1),
            })

    points_file = output_root / "sample_points.json"
    points_file.write_text(
        json.dumps(sample_points, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    log.info("Sampled %d points -> %s", len(sample_points), points_file)
    return sample_points


def _load_walk_edges_from_osmnx() -> list | None:
    """Download the pedestrian walk network for BBOX from OpenStreetMap via OSMnx.

    Returns a list of edge dicts ready for _sample_edges(), or None on failure.
    Uses graph_from_polygon (stable across osmnx 1.x and 2.x) instead of
    graph_from_bbox whose positional-arg signature changed between versions.
    """
    try:
        import osmnx as ox
        from shapely.geometry import box as shapely_box

        log.info("Downloading walk network from OSM via OSMnx (v%s) ...", ox.__version__)

        bbox_poly = shapely_box(
            BBOX["min_lon"], BBOX["min_lat"],
            BBOX["max_lon"], BBOX["max_lat"],
        )
        G = ox.graph_from_polygon(
            bbox_poly,
            network_type="walk",
            retain_all=False,
        )

        gdf_edges = ox.graph_to_gdfs(G, nodes=False, edges=True).to_crs(UTM31N)
        log.info("OSMnx returned %d walk edges", len(gdf_edges))

        edges = []
        for (u, v, k), row in gdf_edges.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty or geom.length < 1:
                continue

            hw = row.get("highway", "unknown")
            if isinstance(hw, list):
                hw = hw[0]

            name = row.get("name", "")
            if isinstance(name, list):
                name = name[0]

            edges.append({
                "geometry"    : geom,
                "name"        : name if isinstance(name, str) else "",
                "highway_type": str(hw),
                "edge_id"     : f"{u}_{v}_{k}",
            })

        log.info("Built %d usable edges from OSMnx walk network", len(edges))
        return edges

    except Exception:
        log.warning("_load_walk_edges_from_osmnx failed — will fall back to grid",
                    exc_info=True)
        return None


def query_and_sample_points(output_root: Path) -> list:
    """Download walk-network edges via OSMnx and sample points along them.

    Priority order:
      1. Cached sample_points.json (resume / re-run)
      2. OSMnx  — live OSM walk network for BBOX
      3. Uniform BBOX grid fallback
    """
    points_file = output_root / "sample_points.json"

    if points_file.exists():
        with open(points_file, encoding="utf-8") as f:
            pts = json.load(f)
        log.info("Re-using %d saved sample points from %s", len(pts), points_file)
        return pts

    edges = _load_walk_edges_from_osmnx()
    if edges:
        return _sample_edges(edges, output_root)

    log.warning("OSMnx walk network unavailable — falling back to BBOX grid sampler.")
    return _grid_sample_bbox(output_root)


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

    _LIST_FIELDS = (
        "lighting", "spatial_character", "crowdedness",
        "greenery", "street_amenities",
    )
    print(f"\n--- Scene ---\n  {res.get('scene', 'unknown')}")
    for field in _LIST_FIELDS:
        items = res.get(field, [])
        if not items:
            print(f"\n  {field}: (no data)")
            continue
        print(f"\n  {field}:")
        for obs in items:
            if not isinstance(obs, dict):
                continue
            zone    = obs.get("zone", "?")
            element = obs.get("element", "")
            tags    = {k: v for k, v in obs.items() if k not in ("zone", "element")}
            tag_str = "  " + "  ".join(f"{k}={v}" for k, v in tags.items()) if tags else ""
            elem_str = f" [{element[:30]}]" if element else ""
            print(f"    [{zone:10s}]{elem_str}{tag_str}")
    vt = res.get("visible_text", [])
    if vt:
        print("\n  visible_text:")
        for entry in vt:
            if isinstance(entry, dict):
                print(f"    [{entry.get('zone','?'):10s}] {entry.get('text','?')}"
                      f"  ({entry.get('type','?')})")

    landmarks = fetch_nearby_landmarks(pt["lat"], pt["lon"])
    if landmarks:
        print("\n--- Nearby Landmarks ---")
        for lm in landmarks:
            print(f"  {lm['bearing']:2s}  {lm['distance_m']:5.0f} m  "
                  f"{lm['name']} ({lm['category']})")
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
                    "scene_analysis"  : None,
                    "nearby_landmarks": [],
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
                "scene_analysis"  : scene_result,
                "nearby_landmarks": fetch_nearby_landmarks(pt["lat"], pt["lon"]),
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
    p.add_argument("--trial-heading", type=float, default=None,
                   help="Manual heading override (0-360)")
    p.add_argument("--landmark-db",   type=str,   default="",
                   help="Path to DuckDB with OSM/Overture 'places' table for nearby landmark lookup")
    p.add_argument(
        "--model-size", choices=["3b", "7b"], default="7b",
        help="VLM size: '7b' (default, 4-bit NF4, ~4.5 GB VRAM) or '3b' (bfloat16, ~6 GB VRAM)",
    )
    return p.parse_args()


def main():
    args = _parse_args()

    global LANDMARK_DB_PATH, MODEL_ID
    if args.landmark_db:
        LANDMARK_DB_PATH = args.landmark_db
    MODEL_ID = _MODEL_IDS[args.model_size]

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
