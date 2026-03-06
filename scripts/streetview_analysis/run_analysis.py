"""
Run Analysis — Orchestrate the full street-view perception pipeline.

Usage:
    python run_analysis.py --lat 41.3874 --lng 2.1686 --heading 90
    python run_analysis.py --image path/to/image.jpg
    python run_analysis.py --serve                       # start HTML viewer server
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR / "output"


def ensure_dirs():
    for sub in ("images", "grids", "results"):
        (OUTPUT_DIR / sub).mkdir(parents=True, exist_ok=True)


def run_pipeline(
    lat: float | None = None,
    lng: float | None = None,
    heading: float = 0,
    image_path: str | None = None,
    api_key: str | None = None,
) -> Path:
    """Run fetch → split → analyze → save and return the result JSON path."""
    ensure_dirs()

    # ---- Step 1: Obtain source image ----
    if image_path:
        src = Path(image_path)
        if not src.exists():
            print(f"[ERROR] Image not found: {src}")
            sys.exit(1)
        print(f"[1/3] Using local image: {src}")
    elif lat is not None and lng is not None:
        from streetview_fetcher import fetch_streetview

        print(f"[1/3] Fetching Street View image at ({lat}, {lng}) heading={heading} …")
        src = fetch_streetview(lat, lng, heading=heading, api_key=api_key)
        print(f"       Saved → {src}")
    else:
        print("[ERROR] Provide --lat/--lng or --image")
        sys.exit(1)

    # ---- Step 2: Split into 3×3 grid ----
    from grid_splitter import split_image_3x3

    grid_dir = OUTPUT_DIR / "grids"
    print("[2/3] Splitting into 3×3 grid …")
    cells = split_image_3x3(src, grid_dir)
    for pos, p in cells.items():
        print(f"       {pos}: {p.name}")

    # ---- Step 3: Analyze each cell with PerceptionLM ----
    from plm_analyzer import PLMAnalyzer

    print("[3/3] Analyzing grid cells with PerceptionLM-1B …")
    analyzer = PLMAnalyzer()
    cell_results = analyzer.analyze_grid(cells)

    # ---- Build combined result ----
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    result = {
        "metadata": {
            "timestamp": timestamp,
            "source_image": src.name,
            "source_path": str(src),
            "latitude": lat,
            "longitude": lng,
            "heading": heading,
            "model": "facebook/Perception-LM-1B",
            "device": analyzer.device,
        },
        "grid_analysis": cell_results,
    }

    result_path = OUTPUT_DIR / "results" / f"{src.stem}_{timestamp}.json"
    result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n✓ Result saved → {result_path}")
    return result_path


def serve_viewer(port: int = 8500):
    """Start a simple HTTP server to view results."""
    import http.server
    import functools

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SCRIPT_DIR))
    with http.server.HTTPServer(("", port), handler) as httpd:
        print(f"[Viewer] Serving at http://localhost:{port}/viewer.html")
        print("         Press Ctrl+C to stop.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[Viewer] Stopped.")


def main():
    parser = argparse.ArgumentParser(description="PerceptionLM Street View Analyzer")
    parser.add_argument("--lat", type=float, help="Latitude")
    parser.add_argument("--lng", type=float, help="Longitude")
    parser.add_argument("--heading", type=float, default=0, help="Camera heading 0-360")
    parser.add_argument("--image", type=str, help="Path to a local image (skips API fetch)")
    parser.add_argument("--api-key", type=str, help="Google Street View API key (or set env)")
    parser.add_argument("--serve", action="store_true", help="Start HTML viewer server")
    parser.add_argument("--port", type=int, default=8500, help="Viewer server port")
    args = parser.parse_args()

    if args.serve:
        serve_viewer(args.port)
        return

    run_pipeline(
        lat=args.lat,
        lng=args.lng,
        heading=args.heading,
        image_path=args.image,
        api_key=args.api_key,
    )


if __name__ == "__main__":
    main()
