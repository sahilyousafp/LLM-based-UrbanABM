"""
02_run_vlm_analysis.py
=======================
Step 2 of 2 — Run Qwen3-VL on downloaded Street View images.

Reads:
  {output_dir}/images/          — JPEGs from 01_fetch_streetview_images.py
  {output_dir}/sample_points.json — point metadata for lat/lon/heading/street

Writes:
  {output_dir}/results/{lat}_{lon}_analysis.json — one JSON per analysed image

Requires:
  HF_TOKEN          — HuggingFace token (Qwen model access)
  GPU with ≥6 GB VRAM (3B model) or ≥12 GB (7B model)

Usage:
  python 02_run_vlm_analysis.py
  python 02_run_vlm_analysis.py --output-dir /my/output --model-size 3b
  python 02_run_vlm_analysis.py --limit 10               # first 10 images only
  python 02_run_vlm_analysis.py --reanalyse              # redo existing results
"""

import argparse
import gc
import io
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import requests
from dotenv import load_dotenv
from tqdm.auto import tqdm

# ---------------------------------------------------------------------------
# Bootstrap optional dependencies
# ---------------------------------------------------------------------------
def _pip_install(pkg: str) -> None:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pkg])

try:
    import torch
except ImportError:
    _pip_install("torch")
    import torch

try:
    from PIL import Image as PILImage
except ImportError:
    _pip_install("Pillow")
    from PIL import Image as PILImage

try:
    from transformers import (
        Qwen3VLForConditionalGeneration,
        AutoProcessor,
        BitsAndBytesConfig,
    )
except ImportError:
    _pip_install("transformers>=4.45")
    from transformers import (
        Qwen3VLForConditionalGeneration,
        AutoProcessor,
        BitsAndBytesConfig,
    )

try:
    from pydantic import BaseModel, Field, field_validator
except ImportError:
    _pip_install("pydantic>=2.0")
    from pydantic import BaseModel, Field, field_validator

try:
    from huggingface_hub import login as hf_login
except ImportError:
    _pip_install("huggingface_hub")
    from huggingface_hub import login as hf_login

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
# Load .env
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).parent
for _env in [
    _SCRIPT_DIR / ".env",
    _SCRIPT_DIR.parent / ".env",
    _SCRIPT_DIR.parent.parent.parent / ".env",
]:
    if _env.exists():
        load_dotenv(_env)
        break

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
# Simulation study area — 2 km × 2 km centred on Passeig de Gràcia, Eixample
BBOX = {
    "min_lon": 2.1500,
    "min_lat": 41.3862,
    "max_lon": 2.1740,
    "max_lat": 41.4042,
}

DEFAULT_OUTPUT_DIR = (
    Path(__file__).parent.parent.parent.parent
    / "Backend" / "Environment" / "output"
)
# Safety check: if parent-4 goes above project root (e.g. script copied outside
# the repo), fall back to CWD + "output" so it never tries /Backend/...
if not (DEFAULT_OUTPUT_DIR.parent.parent.exists()):
    DEFAULT_OUTPUT_DIR = Path.cwd() / "output"
    log.warning("Computed output dir not under repo root — fallback to %s", DEFAULT_OUTPUT_DIR)
MODEL_IDS = {
    "3b": "Qwen/Qwen3-VL-2B-Instruct",
    "7b": "Qwen/Qwen3-VL-8B-Instruct",
}
MAX_NEW_TOKENS = 1200
TEMPERATURE    = 0.7
TOP_P          = 0.8
TOP_K          = 20
REP_PENALTY    = 1.1
MIN_FIELDS_OK  = 3     # retry if fewer than this many fields are populated
MAX_RETRIES    = 4


# ---------------------------------------------------------------------------
# Pydantic output schema  (mirrors street_plm_job.py)
# ---------------------------------------------------------------------------
class StreetSceneAnalysis(BaseModel):
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
        return str(v).strip() if v else "unknown"

    @field_validator(
        "lighting", "spatial_character", "crowdedness",
        "greenery", "street_amenities", "visible_text",
        mode="before",
    )
    @classmethod
    def _coerce_list(cls, v):
        if isinstance(v, list):  return v
        if isinstance(v, dict):  return [v]
        return []


# Key aliases — normalise VLM paraphrasing to canonical field names
_KEY_ALIASES: dict[str, str] = {
    "scene": "scene", "scene_description": "scene", "overview": "scene", "summary": "scene",
    "lighting": "lighting", "light": "lighting", "light_quality": "lighting",
    "spatial": "spatial_character", "spatial_character": "spatial_character",
    "space": "spatial_character", "street_space": "spatial_character",
    "crowding": "crowdedness", "crowdedness": "crowdedness",
    "crowd": "crowdedness", "pedestrians": "crowdedness", "people": "crowdedness",
    "greenery": "greenery", "vegetation": "greenery", "plants": "greenery", "trees": "greenery",
    "amenities": "street_amenities", "street_amenities": "street_amenities",
    "furniture": "street_amenities", "street_furniture": "street_amenities",
    "visible_text": "visible_text", "text": "visible_text",
    "signs": "visible_text", "ocr": "visible_text", "signage": "visible_text",
}

# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------
_model     = None
_processor = None


def load_model(model_id: str) -> tuple:
    global _model, _processor

    vram_gb = 0.0
    if torch.cuda.is_available():
        vram_gb = torch.cuda.get_device_properties(0).total_memory / 1e9
        log.info("GPU: %s  (%.1f GB VRAM)", torch.cuda.get_device_name(0), vram_gb)
    else:
        log.warning("No GPU detected — inference will be very slow on CPU")

    # Dynamic resolution based on VRAM
    if vram_gb >= 23:
        max_pixels = 1280 * 28 * 28
    elif vram_gb >= 15:
        max_pixels = 784 * 28 * 28
    else:
        max_pixels = 512 * 28 * 28
    log.info("max_pixels=%d  model=%s", max_pixels, model_id)

    # 4-bit NF4 quantization
    bnb_cfg = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16 if vram_gb < 15 else torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )

    log.info("Loading model …")
    _model = Qwen3VLForConditionalGeneration.from_pretrained(
        model_id,
        quantization_config=bnb_cfg,
        device_map="auto",
        torch_dtype="auto",
    )
    _processor = AutoProcessor.from_pretrained(
        model_id, max_pixels=max_pixels, min_pixels=256 * 28 * 28
    )
    log.info("Model loaded.")
    return _model, _processor, vram_gb


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "You are an expert urban analyst. Study the street-level image and output ONLY a "
    "JSON object with these exact keys: scene, lighting, spatial_character, crowdedness, "
    "greenery, street_amenities, visible_text.\n"
    "Each key (except 'scene') must be a JSON array of zone-observation objects.\n"
    "Zone values: far_left | left | center | right | far_right.\n"
    "Output ONLY valid JSON — no markdown, no commentary."
)

USER_PROMPT = """\
Analyse this street-level image. Return a single JSON object with ALL of these keys filled in:
{
  "scene": "<one sentence overall description>",
  "lighting": [{"zone": "...", "element": "...", "condition": "dark|dim|adequate|bright"}, ...],
  "spatial_character": [{"zone": "...", "width": "narrow|moderate|wide", "enclosure": "open|semi|enclosed", "passability": "clear|obstructed", "lane_type": "sidewalk|road|shared|plaza", "crossing": "none|zebra|signalised", "architectural_style": "neo_gothic|modernist|contemporary|neoclassical|vernacular|eclectic|art_deco|other", "building_condition": "excellent|good|fair|poor|under_construction", "storefront_type": "retail|restaurant|cafe|office|residential|hotel|vacant|cultural|industrial|other", "architectural_details": "..."}, ...],
  "crowdedness": [{"zone": "...", "density_level": "empty|sparse|moderate|dense"}, ...],
  "greenery": [{"zone": "...", "element": "...", "coverage": "none|sparse|moderate|dense"}, ...],
  "street_amenities": [{"zone": "...", "element": "...", "material_and_colour": "...", "presence": "none|few|several|many"}, ...],
  "visible_text": [{"text": "...", "zone": "...", "type": "sign|label|graffiti"}, ...]
}
Zone values: far_left | left | center | right | far_right.
Output ONLY the JSON object. No markdown, no explanation."""


def _parse_json(raw: str) -> dict:
    """Four-layer JSON extraction: direct → brace-count → truncation-recovery → regex."""
    raw = raw.strip()

    # 1. Direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 2. Extract first {...} block via brace counting
    start = raw.find("{")
    if start != -1:
        depth, end = 0, -1
        for i, c in enumerate(raw[start:], start):
            if c == "{":  depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end != -1:
            try:
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                pass

    # 3. Truncation recovery — add missing closing braces/brackets
    for attempt in range(10):
        candidate = raw + "]}" * (attempt + 1)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # 4. Regex key-value fallback
    result: dict[str, Any] = {}
    for key in ("scene", "lighting", "spatial_character", "crowdedness",
                "greenery", "street_amenities", "visible_text"):
        m = re.search(rf'"{key}"\s*:\s*(".*?"|[\[{{].*?[\]}}])', raw, re.DOTALL)
        if m:
            try:
                result[key] = json.loads(m.group(1))
            except Exception:
                result[key] = m.group(1)
    return result


def _normalise(raw_dict: dict) -> dict:
    """Remap aliased keys → canonical schema keys."""
    out = {}
    for k, v in raw_dict.items():
        canonical = _KEY_ALIASES.get(k.lower().strip())
        if canonical:
            out[canonical] = v
    return out


def _build_input(image, greedy: bool):
    """Build processor inputs for a single PIL image."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": [
            {"type": "image", "image": image},
            {"type": "text",  "text": USER_PROMPT},
        ]},
    ]
    inputs = _processor.apply_chat_template(
        messages, tokenize=True, add_generation_prompt=True,
        return_dict=True, return_tensors="pt",
    ).to(_model.device)
    return inputs


def infer_scene(image, greedy: bool = False) -> tuple[dict, float]:
    """Run Qwen3-VL on a single PIL image. Returns (result_dict, latency_ms)."""
    inputs    = _build_input(image, greedy)
    input_len = inputs["input_ids"].shape[1]

    gen_kwargs: dict = dict(max_new_tokens=MAX_NEW_TOKENS, repetition_penalty=REP_PENALTY)
    if greedy:
        gen_kwargs["do_sample"] = False
    else:
        gen_kwargs.update(do_sample=True, temperature=TEMPERATURE, top_p=TOP_P, top_k=TOP_K)

    t0 = time.time()
    with torch.no_grad():
        out_ids = _model.generate(**inputs, **gen_kwargs)
    latency_ms = (time.time() - t0) * 1000

    new_ids  = out_ids[0, input_len:]
    raw_text = _processor.decode(new_ids, skip_special_tokens=True)
    raw_dict = _parse_json(raw_text)
    result   = _normalise(raw_dict)
    return result, latency_ms


def _count_populated(result: dict) -> int:
    return sum(
        1 for k, v in result.items()
        if v not in ("unknown", "", None, []) and not k.startswith("_")
    )


def analyse(image_path) -> tuple[dict, float]:
    """Analyse a single image with retry on low-quality output."""
    image = PILImage.open(image_path).convert("RGB")

    result, latency_ms = infer_scene(image, greedy=False)

    for attempt in range(2, MAX_RETRIES + 1):
        if _count_populated(result) >= MIN_FIELDS_OK:
            break
        log.debug("Only %d fields populated — retry %d/%d", _count_populated(result), attempt, MAX_RETRIES)
        greedy = (attempt == MAX_RETRIES)
        result, latency_ms = infer_scene(image, greedy=greedy)

    try:
        validated = StreetSceneAnalysis(**result).model_dump()
    except Exception:
        validated = result
    return validated, latency_ms


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Run VLM analysis on downloaded Street View images.")
    parser.add_argument("--output-dir",   default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--model-size",   choices=["3b", "7b"], default="7b",
                        help="Qwen3-VL model size (default: 7b)")
    parser.add_argument("--limit",        type=int, default=0,
                        help="Process at most N images (0 = all)")
    parser.add_argument("--reanalyse",    action="store_true",
                        help="Re-run VLM even if results JSON already exists")
    parser.add_argument("--hf-token",     default="",
                        help="Override HF_TOKEN env variable")
    args = parser.parse_args()

    # ── HuggingFace auth ──────────────────────────────────────────────────────
    hf_token = args.hf_token or os.environ.get("HF_TOKEN", "")
    if hf_token:
        hf_login(token=hf_token, add_to_git_credential=False)
    else:
        log.warning("HF_TOKEN not set — model download may fail for gated repos")

    # ── Paths ─────────────────────────────────────────────────────────────────
    output_root = Path(args.output_dir)
    images_dir  = output_root / "images"
    results_dir = output_root / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    if not images_dir.exists():
        log.error("images/ directory not found: %s", images_dir)
        log.error("Run 01_fetch_streetview_images.py first.")
        sys.exit(1)

    # ── Load point metadata (for lat/lon/heading/street enrichment) ───────────
    point_lookup: dict[str, dict] = {}
    points_file = output_root / "sample_points.json"
    if points_file.exists():
        for pt in json.loads(points_file.read_text(encoding="utf-8")):
            key = f"sv_{pt['lat']:.6f}_{pt['lon']:.6f}"
            point_lookup[key] = pt
        log.info("Loaded %d point records for metadata enrichment", len(point_lookup))

    # ── Collect images to process ─────────────────────────────────────────────
    all_images = sorted(images_dir.glob("sv_*.jpg"))
    if not all_images:
        log.error("No sv_*.jpg images found in %s", images_dir)
        log.error("Run 01_fetch_streetview_images.py first.")
        sys.exit(1)

    if not args.reanalyse:
        pending = [
            p for p in all_images
            if not (results_dir / f"{p.stem.replace('sv_', '').rsplit('_h', 1)[0]}_analysis.json").exists()
        ]
        log.info("%d images total — %d already done — %d to process",
                 len(all_images), len(all_images) - len(pending), len(pending))
    else:
        pending = all_images
        log.info("%d images to process (--reanalyse mode)", len(pending))

    if args.limit > 0:
        pending = pending[:args.limit]
        log.info("Limiting to first %d images (--limit)", args.limit)

    if not pending:
        log.info("Nothing to do — all images already analysed.")
        return

    # ── Load model ────────────────────────────────────────────────────────────
    model_id      = MODEL_IDS[args.model_size]
    _, _, vram_gb = load_model(model_id)
    log.info("VRAM: %.1f GB", vram_gb)

    # ── Run inference ─────────────────────────────────────────────────────────
    run_ts   = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    ok, fail = 0, 0

    # Parse filenames upfront — skip unparseable
    valid_pending = []
    for img_path in pending:
        stem = img_path.stem
        try:
            body         = stem[3:]
            lat_lon, h   = body.rsplit("_h", 1)
            lat_s, lon_s = lat_lon.split("_", 1)
            float(lat_s); float(lon_s); float(h)
            valid_pending.append(img_path)
        except (ValueError, IndexError):
            log.warning("Cannot parse lat/lon from filename: %s — skipping", img_path.name)
            fail += 1

    with tqdm(total=len(valid_pending), unit="img", desc="VLM") as bar:
        for img_path in valid_pending:
            try:
                scene_analysis, latency_ms = analyse(img_path)
            except Exception as exc:
                log.warning("Error on %s: %s — skipping", img_path.name, exc)
                fail += 1
                bar.update(1)
                continue

            stem          = img_path.stem
            body          = stem[3:]
            lat_lon, h    = body.rsplit("_h", 1)
            lat_s, lon_s  = lat_lon.split("_", 1)
            lat, lon, hdg = float(lat_s), float(lon_s), float(h)
            result_path   = results_dir / f"{lat_s}_{lon_s}_analysis.json"
            pt_meta       = point_lookup.get(f"sv_{lat:.6f}_{lon:.6f}", {})

            output = {
                "metadata": {
                    "timestamp"        : run_ts,
                    "latitude"         : lat,
                    "longitude"        : lon,
                    "heading"          : hdg,
                    "street_name"      : pt_meta.get("street_name", ""),
                    "highway_type"     : pt_meta.get("highway_type", ""),
                    "edge_id"          : pt_meta.get("edge_id", ""),
                    "dist_along_edge_m": pt_meta.get("dist_along_edge_m"),
                    "source_image"     : img_path.name,
                    "model"            : model_id,
                    "device"           : str(next(_model.parameters()).device),
                    "latency_ms"       : latency_ms,
                    "status"           : "ok",
                },
                "scene_analysis"  : scene_analysis,
                "nearby_landmarks": [],
            }
            result_path.write_text(
                json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            ok += 1
            bar.update(1)

            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    # ── Summary ───────────────────────────────────────────────────────────────
    log.info("─" * 60)
    log.info("Analysed  : %d images", ok)
    log.info("Failed    : %d images", fail)
    log.info("Results   → %s", results_dir)
    log.info("─" * 60)


if __name__ == "__main__":
    main()
