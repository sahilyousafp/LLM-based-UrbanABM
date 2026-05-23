#!/usr/bin/env python3
"""
Quick single-image test for the compact zone-aware VLM prompt.

Usage:
  # Test on an already-cached image
  python test_single_image.py --image /path/to/sv_41.395200_2.162000_h0.jpg

  # Fetch a fresh image by coordinates and test
  python test_single_image.py --lat 41.3952 --lon 2.1620 --heading 90

  # With nearby landmark lookup
  python test_single_image.py --image sv.jpg --landmark-db /path/to/eixample_osm.duckdb
"""

import argparse
import json
import sys
import time
from pathlib import Path

# Re-use all machinery from street_plm_job (model, prompt, parsers, helpers).
# Import before __main__ guard so the module-level code runs.
import street_plm_job as job


def main():
    ap = argparse.ArgumentParser(
        description="Run the VLM on a single street-view image and print results."
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--image",   type=Path,  help="Path to an existing street-view .jpg")
    src.add_argument("--lat",     type=float, help="Latitude (fetches Street View on the fly)")
    ap.add_argument("--lon",      type=float, help="Longitude (required with --lat)")
    ap.add_argument("--heading",  type=float, default=0.0,
                    help="Camera heading in degrees 0-360 (default: 0)")
    ap.add_argument("--images-dir", type=Path,
                    default=Path("/teamspace/studios/this_studio/StreetPLM/images"),
                    help="Directory to cache downloaded Street View images")
    ap.add_argument("--landmark-db", type=str, default="",
                    help="Path to DuckDB with OSM/Overture 'places' table")
    ap.add_argument("--raw", action="store_true",
                    help="Include raw VLM output string in the printed JSON")
    args = ap.parse_args()

    # ---- resolve image path ------------------------------------------------
    if args.image:
        img_path = args.image
        if not img_path.exists():
            sys.exit(f"Image not found: {img_path}")
        lat = lon = 0.0
    else:
        if args.lon is None:
            ap.error("--lon is required when using --lat")
        lat, lon = args.lat, args.lon
        args.images_dir.mkdir(parents=True, exist_ok=True)
        img_path = job.fetch_sv(lat, lon, args.heading, args.images_dir)
        if img_path is None:
            sys.exit(f"No Street View coverage at ({lat}, {lon}).")

    # ---- load model --------------------------------------------------------
    job.load_model()

    # ---- infer -------------------------------------------------------------
    from PIL import Image
    img = Image.open(img_path).convert("RGB")
    t0  = time.perf_counter()
    scene_dict, raw_text = job._infer_scene(img)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    # ---- nearby landmarks --------------------------------------------------
    landmarks = job.fetch_nearby_landmarks(lat, lon, db_path=args.landmark_db)

    # ---- assemble result ---------------------------------------------------
    result = {
        "image"           : str(img_path),
        "latency_ms"      : round(elapsed_ms, 1),
        "approx_tokens"   : len(raw_text.split()),
        "scene_analysis"  : scene_dict,
        "nearby_landmarks": landmarks,
    }
    if args.raw:
        result["raw_output"] = raw_text

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
