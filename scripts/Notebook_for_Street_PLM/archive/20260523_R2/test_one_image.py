"""
test_one_image.py
=================
Single-image end-to-end test for the migrated street_plm_job.py.

Imports model, inference, and schema functions directly from street_plm_job.
No BigQuery. No sample point list. Fetches one image, runs VLM, prints results.

Usage:
    # Default: Casa Battló junction
    GOOGLE_STREETVIEW_API_KEY=... HF_TOKEN=... python test_one_image.py

    # Pick a preset junction:
    python test_one_image.py --junction arago_casanova

    # Custom coordinates:
    python test_one_image.py --lat 41.3906 --lon 2.1614 --heading 95

    # Fetch image only — no VLM (check API key works first):
    python test_one_image.py --image-only

Output saved to: scripts/Notebook_for_Street_PLM/test_output/
    <junction>_image.jpg        — raw Street View image
    <junction>_annotated.jpg    — image with L/CL/CR/R panel dividers drawn on
    <junction>_result.json      — full analysis JSON matching pipeline schema
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw

# ── Import everything from the migrated pipeline script ────────────────────
# sys.path insert ensures this works whether run from repo root or this dir
sys.path.insert(0, str(Path(__file__).parent))

from street_plm_job import (
    STREETVIEW_API_KEY,
    HF_TOKEN,
    MODEL_ID,
    STORAGE_KEYWORDS,
    VALID_PANELS,
    fetch_sv,
    load_model,
    analyze_image,
    landmark_lookup,
    build_agent_context,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Preset junctions ───────────────────────────────────────────────────────
# All are standard Eixample grid crossings.
# Heading aligns with the main street so L/R panels show the cross street
# and CL/CR panels show the path the agent would continue along.

JUNCTIONS = {
    "casa_battlo": {
        "description": "Passeig de Gràcia × Carrer de Provença (Casa Battló crossing) — tourist core",
        "lat": 41.39148, "lon": 2.16530, "heading": 2,
    },
    "arago_casanova": {
        "description": "Carrer d'Aragó × Carrer de Casanova — residential Esquerra",
        "lat": 41.3882, "lon": 2.1534, "heading": 98,
    },
    "consell_cent_balmes": {
        "description": "Carrer del Consell de Cent × Carrer de Balmes — mixed use",
        "lat": 41.3906, "lon": 2.1614, "heading": 95,
    },
    "passeig_gracia": {
        "description": "Passeig de Gràcia × Carrer de Provença — tourist core",
        "lat": 41.3956, "lon": 2.1653, "heading": 2,
    },
    "enric_granados": {
        "description": "Carrer d'Enric Granados × Carrer de València — pedestrian rambla",
        "lat": 41.3928, "lon": 2.1613, "heading": 352,
    },
    "diputacio_muntaner": {
        "description": "Carrer de la Diputació × Carrer de Muntaner — Esquerra commercial",
        "lat": 41.3896, "lon": 2.1572, "heading": 5,
    },
}

DEFAULT_JUNCTION = "casa_battlo"

# ── Panel annotation ───────────────────────────────────────────────────────

def annotate_panels(src: Path, dst: Path) -> Path:
    """Draw L/CL/CR/R dividers on the image so panels are visually verifiable."""
    img  = Image.open(src).convert("RGB")
    draw = ImageDraw.Draw(img)
    w, h = img.size

    panels = [
        (0,        w // 4,   "L",  (52, 152, 219)),
        (w // 4,   w // 2,   "CL", (39, 174, 96)),
        (w // 2,   3*w // 4, "CR", (39, 174, 96)),
        (3*w // 4, w,        "R",  (52, 152, 219)),
    ]
    for x0, x1, label, colour in panels:
        if x0 > 0:
            draw.line([(x0, 0), (x0, h)], fill=colour, width=2)
        bx = x0 + (x1 - x0) // 2 - 18
        draw.rectangle([bx, 8, bx + 36, 30], fill=colour)
        draw.text((bx + 4, 10), label, fill="white")

    img.save(dst)
    return dst

# ── Audit checks ───────────────────────────────────────────────────────────

def run_audit(scene: dict) -> bool:
    obs = scene.get("observations", [])
    checks = [
        ("VLM returned observations",         len(obs) > 0),
        ("At least 3 keywords populated",     len({o["feature"] for o in obs}) >= 3),
        ("No empty element fields",           all(o.get("element", "") not in ("", "unknown") for o in obs)),
        ("All panels are valid values",       all(o.get("panel", "") in VALID_PANELS for o in obs)),
        ("All features are valid keywords",   all(o.get("feature", "") in STORAGE_KEYWORDS for o in obs)),
        ("openness in range 1–5",             1 <= scene.get("openness", 0) <= 5),
        ("crowdedness in range 1–5",          1 <= scene.get("crowdedness", 0) <= 5),
        ("raw_vlm_output stored",             len(scene.get("raw_vlm_output", "")) > 10),
    ]

    print("\n=== AUDIT CHECKS ===")
    all_pass = True
    for label, passed in checks:
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}]  {label}")
        if not passed:
            all_pass = False

    print(f"\n  {'All checks passed.' if all_pass else 'Some checks FAILED — review the JSON.'}")
    return all_pass

# ── Main test ──────────────────────────────────────────────────────────────

def run_test(
    junction_name: str,
    lat: float,
    lon: float,
    heading: float,
    out_dir: Path,
    image_only: bool = False,
) -> dict | None:

    out_dir.mkdir(parents=True, exist_ok=True)
    slug = junction_name.replace(" ", "_")

    print("\n" + "=" * 60)
    print(f"  Junction : {junction_name}")
    print(f"  Coords   : {lat:.6f}, {lon:.6f}  heading {heading:.0f}°")
    print("=" * 60)

    # 1. Fetch Street View image
    raw_img_path = out_dir / f"{slug}_image.jpg"
    img_path = fetch_sv(lat, lon, heading, out_dir)

    # fetch_sv saves with its own naming — rename to our convention
    if img_path and img_path != raw_img_path:
        img_path.rename(raw_img_path)
        img_path = raw_img_path

    if img_path is None:
        log.error("No Street View coverage at (%.6f, %.6f). Try a nearby coordinate.", lat, lon)
        return None

    print(f"\n  Image saved : {img_path}")

    # 2. Annotate panels
    ann_path = annotate_panels(img_path, out_dir / f"{slug}_annotated.jpg")
    print(f"  Annotated   : {ann_path}")
    print("  Open the annotated image to visually verify each observation's panel.")

    if image_only:
        print("\n  --image-only flag set. Skipping VLM.")
        return None

    # 3. OSM landmark lookup (GPS — not VLM)
    print("\n  Querying OSM for nearby landmarks...")
    landmark = landmark_lookup(lat, lon)
    print(f"  Landmark    : '{landmark}'" if landmark else "  Landmark    : (none within 100 m)")

    # 4. VLM inference
    print("\n  Running Qwen VLM...")
    t0    = time.perf_counter()
    scene = analyze_image(img_path)
    ms    = round((time.perf_counter() - t0) * 1000, 1)
    scene_latency = scene.pop("_latency_ms", ms)

    # 5. Print observations
    obs = scene.get("observations", [])
    print(f"\n=== OBSERVATIONS ({len(obs)} found, {int(scene_latency)} ms) ===")
    print(f"  {'feature':<22} {'element':<22} {'panel':<8} descriptor")
    print("  " + "-" * 74)
    for o in obs:
        print(f"  {o['feature']:<22} {o['element']:<22} {o['panel']:<8} {o['descriptor']}")

    print(f"\n  openness={scene['openness']}/5  "
          f"crowdedness={scene['crowdedness']}/5  "
          f"passable={scene['passable']}")

    # 6. Agent contexts (all 4 archetypes)
    print("\n=== AGENT CONTEXTS ===")
    agent_results = {}
    for arch in ["tourist", "commuter", "resident", "student"]:
        ctx = build_agent_context(scene, arch, landmark)
        print(f"\n  [{arch.upper()}]")
        print(f"  {ctx}")
        agent_results[arch] = ctx

    # 7. Audit checks
    run_audit(scene)

    # 8. Save full JSON
    result = {
        "metadata": {
            "test_script":    "test_one_image.py",
            "junction":       junction_name,
            "latitude":       lat,
            "longitude":      lon,
            "heading":        heading,
            "image_file":     img_path.name,
            "annotated_file": ann_path.name,
            "landmark_osm":   landmark,
            "vlm_latency_ms": scene_latency,
            "model":          MODEL_ID,
            "schema_version": "v6",
        },
        "scene_analysis": {
            "observations":   obs,
            "landmark_name":  landmark,
            "openness":       scene["openness"],
            "crowdedness":    scene["crowdedness"],
            "passable":       scene["passable"],
            "raw_vlm_output": scene.get("raw_vlm_output", ""),
        },
        "agent_contexts": agent_results,
    }

    json_path = out_dir / f"{slug}_result.json"
    json_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\n  JSON saved  : {json_path}")

    return result

# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Single-image test for migrated street_plm_job.py"
    )
    parser.add_argument(
        "--junction", default=DEFAULT_JUNCTION,
        choices=list(JUNCTIONS.keys()),
        help=f"Preset Eixample junction (default: {DEFAULT_JUNCTION})",
    )
    parser.add_argument("--lat",     type=float, help="Override latitude")
    parser.add_argument("--lon",     type=float, help="Override longitude")
    parser.add_argument("--heading", type=float, help="Override heading (0–360°)")
    parser.add_argument(
        "--out-dir",
        default=str(Path(__file__).parent / "test_output"),
        help="Directory for output image and JSON",
    )
    parser.add_argument(
        "--image-only", action="store_true",
        help="Only fetch the Street View image — skip VLM (no HF_TOKEN needed)",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List all preset junctions and exit",
    )
    args = parser.parse_args()

    if args.list:
        print("\nPreset junctions:")
        for name, j in JUNCTIONS.items():
            print(f"  {name:<28}  {j['description']}")
        return

    if not STREETVIEW_API_KEY:
        log.error("GOOGLE_STREETVIEW_API_KEY not set")
        sys.exit(1)

    if not args.image_only and not HF_TOKEN:
        log.error("HF_TOKEN not set (use --image-only to skip VLM)")
        sys.exit(1)

    # Resolve junction coordinates
    if args.lat and args.lon:
        name    = "custom"
        lat     = args.lat
        lon     = args.lon
        heading = args.heading or 98.0
    else:
        j       = JUNCTIONS[args.junction]
        name    = args.junction
        lat     = args.lat     or j["lat"]
        lon     = args.lon     or j["lon"]
        heading = args.heading or j["heading"]
        print(f"\n  {j['description']}")

    # Load model (skip if image-only)
    if not args.image_only:
        load_model()

    run_test(
        junction_name = name,
        lat           = lat,
        lon           = lon,
        heading       = heading,
        out_dir       = Path(args.out_dir),
        image_only    = args.image_only,
    )


if __name__ == "__main__":
    main()
