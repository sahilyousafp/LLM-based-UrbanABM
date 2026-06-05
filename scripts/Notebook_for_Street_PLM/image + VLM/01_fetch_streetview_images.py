"""
01_fetch_streetview_images.py
==============================
Step 1 of 2 — Download Google Street View images for a study area.

What this script does:
  1. Generates sample points along the pedestrian walk network (OSMnx)
     or falls back to a uniform BBOX grid
  2. Checks Street View availability for each point via the Metadata API
  3. Downloads one 640×640 JPEG per available point
  4. Writes sample_points.json and download_manifest.json to the output dir

Run Step 2 (02_run_vlm_analysis.py) afterwards to run VLM inference on the images.

Required environment variable:
  GOOGLE_STREETVIEW_API_KEY  — Google Maps Platform Street View Static API key

Usage:
  python 01_fetch_streetview_images.py
  python 01_fetch_streetview_images.py --output-dir /my/output
  python 01_fetch_streetview_images.py --bbox 2.160 41.391 2.172 41.402
  python 01_fetch_streetview_images.py --spacing 150 --workers 8
  python 01_fetch_streetview_images.py --dry-run
"""

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests
from dotenv import load_dotenv
from tqdm.auto import tqdm

# ---------------------------------------------------------------------------
# Bootstrap optional dependencies
# ---------------------------------------------------------------------------
try:
    import pyproj
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "pyproj"])
    import pyproj

try:
    import osmnx as ox
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "osmnx"])
    import osmnx as ox

from shapely.geometry import box as shapely_box

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
# Load .env  (script dir → repo root → fallback)
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).parent
for _env in [
    _SCRIPT_DIR / ".env",
    _SCRIPT_DIR.parent / ".env",
    _SCRIPT_DIR.parent.parent.parent / ".env",  # repo root
]:
    if _env.exists():
        load_dotenv(_env)
        break

# ---------------------------------------------------------------------------
# Defaults — override via CLI args
# ---------------------------------------------------------------------------
DEFAULT_BBOX = {
    "min_lon": 2.1500,
    "min_lat": 41.3862,
    "max_lon": 2.1740,
    "max_lat": 41.4042,
}
DEFAULT_OUTPUT_DIR = (
    Path(__file__).parent.parent.parent.parent  # repo root
    / "Backend" / "Environment" / "output"
)
DEFAULT_SAMPLE_DISTANCE_M = 200
DEFAULT_GRID_SPACING_M    = 50
DEFAULT_SV_RADIUS_M       = 50
DEFAULT_WORKERS           = 6

SV_SIZE  = "640x640"
SV_FOV   = 90
SV_PITCH = 0
UTM31N   = "EPSG:32631"

_SV_BASE   = "https://maps.googleapis.com/maps/api/streetview"
_META_BASE = "https://maps.googleapis.com/maps/api/streetview/metadata"


# ---------------------------------------------------------------------------
# Point sampling
# ---------------------------------------------------------------------------

def sample_points_from_osmnx(bbox: dict, spacing_m: int, output_root: Path) -> list:
    """Download walk network from OSM and interpolate sample points along edges."""
    log.info("Downloading walk network from OSM via OSMnx (v%s) …", ox.__version__)

    bbox_poly = shapely_box(
        bbox["min_lon"], bbox["min_lat"],
        bbox["max_lon"], bbox["max_lat"],
    )
    G = ox.graph_from_polygon(bbox_poly, network_type="walk", retain_all=False)
    gdf_edges = ox.graph_to_gdfs(G, nodes=False, edges=True).to_crs(UTM31N)
    log.info("OSMnx: %d walk edges", len(gdf_edges))

    transformer = pyproj.Transformer.from_crs(UTM31N, "EPSG:4326", always_xy=True)
    points, seen = [], set()

    for (u, v, _k), row in gdf_edges.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty or geom.length < 10:
            continue

        hw = row.get("highway", "unknown")
        if isinstance(hw, list):
            hw = hw[0]
        hw = str(hw)

        name = row.get("name", "")
        if isinstance(name, list):
            name = name[0]
        name = str(name) if name else ""

        length  = geom.length
        n_steps = max(1, int(length / spacing_m))

        for i in range(n_steps + 1):
            dist   = min(i * spacing_m, length)
            p_proj = geom.interpolate(dist)

            offset = 1.0 if dist + 1.0 < length else -1.0
            p2     = geom.interpolate(dist + offset)
            dx, dy = p2.x - p_proj.x, p2.y - p_proj.y
            heading = (np.degrees(np.arctan2(dx, dy)) + 360) % 360

            lon, lat = transformer.transform(p_proj.x, p_proj.y)
            cell = (round(lat, 4), round(lon, 4))
            if cell in seen:
                continue
            seen.add(cell)

            points.append({
                "id"               : f"{lat:.6f}_{lon:.6f}",
                "lat"              : round(float(lat), 6),
                "lon"              : round(float(lon), 6),
                "heading"          : round(float(heading), 1),
                "street_name"      : name,
                "highway_type"     : hw,
                "edge_id"          : f"{u}_{v}",
                "dist_along_edge_m": round(float(dist), 1),
            })

    # Clip to bbox — OSMnx may return edges that cross the polygon boundary
    before = len(points)
    points = [
        p for p in points
        if bbox["min_lat"] <= p["lat"] <= bbox["max_lat"]
        and bbox["min_lon"] <= p["lon"] <= bbox["max_lon"]
    ]
    if len(points) < before:
        log.info("Clipped %d out-of-bounds points (OSMnx boundary overshoot)", before - len(points))

    _save_points(points, output_root)
    log.info("OSMnx sampling: %d candidate points", len(points))
    return points


def sample_points_from_grid(bbox: dict, spacing_m: int, output_root: Path) -> list:
    """Uniform BBOX grid fallback — two headings (N/E) per cell."""
    log.info("Using BBOX grid fallback (%d m spacing) …", spacing_m)
    mid_lat  = (bbox["min_lat"] + bbox["max_lat"]) / 2
    lat_step = spacing_m / 111_320.0
    lon_step = spacing_m / (111_320.0 * np.cos(np.radians(mid_lat)))

    points, seen = [], set()
    lat = bbox["min_lat"]
    while lat <= bbox["max_lat"] + 1e-9:
        lon = bbox["min_lon"]
        while lon <= bbox["max_lon"] + 1e-9:
            for heading in (0.0, 90.0):
                key = (round(lat, 5), round(lon, 5), int(heading))
                if key not in seen:
                    seen.add(key)
                    rlat, rlon = round(lat, 6), round(lon, 6)
                    points.append({
                        "id"               : f"{rlat:.6f}_{rlon:.6f}_h{int(heading)}",
                        "lat"              : rlat,
                        "lon"              : rlon,
                        "heading"          : float(heading),
                        "street_name"      : "",
                        "highway_type"     : "unknown",
                        "edge_id"          : "grid",
                        "dist_along_edge_m": None,
                    })
            lon += lon_step
        lat += lat_step

    _save_points(points, output_root)
    log.info("Grid fallback: %d candidate points", len(points))
    return points


def _save_points(points: list, output_root: Path) -> None:
    f = output_root / "sample_points.json"
    f.write_text(json.dumps(points, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Saved %d sample points → %s", len(points), f)


# ---------------------------------------------------------------------------
# Zone fetch helper
# ---------------------------------------------------------------------------

def fetch_zone_from_server(server_url: str) -> dict:
    """Fetch the user-drawn zone bbox from the running backend API.

    Returns a bbox dict with min_lon/min_lat/max_lon/max_lat keys.
    Falls back to DEFAULT_BBOX silently if the server is unreachable.
    """
    url = server_url.rstrip("/") + "/api/zone/current"
    try:
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        data = r.json()
        bbox_list = data["bbox"]  # [west, south, east, north]
        log.info("Zone fetched from server (%s): %s", data.get("source", "?"), bbox_list)
        return {
            "min_lon": bbox_list[0], "min_lat": bbox_list[1],
            "max_lon": bbox_list[2], "max_lat": bbox_list[3],
        }
    except Exception as exc:
        log.warning("Could not fetch zone from %s: %s — using DEFAULT_BBOX", url, exc)
        return DEFAULT_BBOX


# ---------------------------------------------------------------------------
# Street View API helpers
# ---------------------------------------------------------------------------

def sv_metadata(lat: float, lon: float, api_key: str, radius_m: int) -> dict:
    """Return the raw metadata dict from the Street View Metadata API."""
    try:
        r = requests.get(_META_BASE, params={
            "location": f"{lat},{lon}",
            "radius"  : radius_m,
            "source"  : "outdoor",
            "key"     : api_key,
        }, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception as exc:
        log.debug("Metadata error (%s,%s): %s", lat, lon, exc)
    return {}


def download_image(
    lat: float,
    lon: float,
    heading: float,
    images_dir: Path,
    api_key: str,
    radius_m: int,
) -> dict:
    """
    Check availability and download one Street View JPEG.

    Returns a result dict:
      status: "downloaded" | "exists" | "unavailable" | "blank" | "error"
      path:   relative path from images_dir (or None)
    """
    fname = f"sv_{lat:.6f}_{lon:.6f}_h{int(heading)}.jpg"
    fpath = images_dir / fname

    # Already downloaded — skip
    if fpath.exists() and fpath.stat().st_size > 5_120:
        return {"status": "exists", "path": fname, "lat": lat, "lon": lon, "heading": heading}

    # Metadata check
    meta = sv_metadata(lat, lon, api_key, radius_m)
    if meta.get("status") != "OK":
        return {"status": "unavailable", "path": None, "lat": lat, "lon": lon, "heading": heading}

    # Download image
    try:
        r = requests.get(_SV_BASE, params={
            "size"    : SV_SIZE,
            "location": f"{lat},{lon}",
            "heading" : heading,
            "pitch"   : SV_PITCH,
            "fov"     : SV_FOV,
            "source"  : "outdoor",
            "key"     : api_key,
        }, timeout=30)
        r.raise_for_status()

        # Reject Google's grey placeholder (< 5 KB)
        if len(r.content) < 5_120:
            return {"status": "blank", "path": None, "lat": lat, "lon": lon, "heading": heading}

        # Reject low-variance blank frames
        try:
            import io
            from PIL import Image as PILImage
            img = PILImage.open(io.BytesIO(r.content)).convert("L")
            if np.array(img).std() < 18.0:
                return {"status": "blank", "path": None, "lat": lat, "lon": lon, "heading": heading}
        except Exception:
            pass  # PIL not available — skip pixel check

        fpath.write_bytes(r.content)
        return {"status": "downloaded", "path": fname, "lat": lat, "lon": lon, "heading": heading}

    except Exception as exc:
        log.debug("SV fetch error (%s,%s h%s): %s", lat, lon, int(heading), exc)
        return {"status": "error", "path": None, "lat": lat, "lon": lon, "heading": heading}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Download Street View images for a study area.")
    parser.add_argument("--output-dir",  default=str(DEFAULT_OUTPUT_DIR),
                        help="Root output directory (images/ subdirectory created inside)")
    parser.add_argument("--bbox",        nargs=4, type=float,
                        metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
                        help="Bounding box override")
    parser.add_argument("--spacing",     type=int, default=DEFAULT_SAMPLE_DISTANCE_M,
                        help="Sample spacing in metres along walk edges (default: 200)")
    parser.add_argument("--radius",      type=int, default=DEFAULT_SV_RADIUS_M,
                        help="Street View search radius in metres (default: 50)")
    parser.add_argument("--workers",     type=int, default=DEFAULT_WORKERS,
                        help="Parallel download workers (default: 6)")
    parser.add_argument("--use-grid",    action="store_true",
                        help="Skip OSMnx — use uniform BBOX grid instead")
    parser.add_argument("--resume",      action="store_true", default=True,
                        help="Reuse existing sample_points.json if present (default: on)")
    parser.add_argument("--no-resume",   dest="resume", action="store_false",
                        help="Regenerate sample points even if file exists")
    parser.add_argument("--dry-run",     action="store_true",
                        help="Generate points only — skip image downloads")
    parser.add_argument("--api-key",     default="",
                        help="Override GOOGLE_STREETVIEW_API_KEY env variable")
    parser.add_argument("--from-zone",  nargs="?", const="http://127.0.0.1:8000",
                        metavar="SERVER_URL",
                        help="Fetch bbox from running backend (GET /api/zone/current) "
                             "instead of DEFAULT_BBOX. Optionally specify server URL "
                             "(default: http://127.0.0.1:8000)")
    args = parser.parse_args()

    # ── API key ──────────────────────────────────────────────────────────────
    api_key = args.api_key or os.environ.get("GOOGLE_STREETVIEW_API_KEY", "")
    if not api_key and not args.dry_run:
        log.error("GOOGLE_STREETVIEW_API_KEY not set. Add it to .env or pass --api-key.")
        sys.exit(1)

    # ── Bbox ──────────────────────────────────────────────────────────────────
    if args.from_zone is not None:
        bbox = fetch_zone_from_server(args.from_zone)
    elif args.bbox:
        bbox = {"min_lon": args.bbox[0], "min_lat": args.bbox[1],
                "max_lon": args.bbox[2], "max_lat": args.bbox[3]}
    else:
        bbox = DEFAULT_BBOX
    log.info("Study area: lon [%.4f, %.4f]  lat [%.4f, %.4f]",
             bbox["min_lon"], bbox["max_lon"], bbox["min_lat"], bbox["max_lat"])

    # ── Output dirs ───────────────────────────────────────────────────────────
    output_root = Path(args.output_dir)
    images_dir  = output_root / "images"
    output_root.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)
    log.info("Output root : %s", output_root)
    log.info("Images dir  : %s", images_dir)

    # ── Sample points ─────────────────────────────────────────────────────────
    points_file = output_root / "sample_points.json"
    if args.resume and points_file.exists():
        points = json.loads(points_file.read_text(encoding="utf-8"))
        log.info("Resuming — loaded %d points from %s", len(points), points_file)
    elif args.use_grid:
        points = sample_points_from_grid(bbox, args.spacing, output_root)
    else:
        try:
            points = sample_points_from_osmnx(bbox, args.spacing, output_root)
        except Exception as exc:
            log.warning("OSMnx failed (%s) — falling back to BBOX grid", exc)
            points = sample_points_from_grid(bbox, DEFAULT_GRID_SPACING_M, output_root)

    log.info("Total candidate points: %d", len(points))

    if args.dry_run:
        log.info("Dry-run mode — skipping downloads. Exiting.")
        return

    # ── Download images ───────────────────────────────────────────────────────
    log.info("Downloading images with %d workers …", args.workers)
    results = []
    counts  = {"downloaded": 0, "exists": 0, "unavailable": 0, "blank": 0, "error": 0}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                download_image,
                p["lat"], p["lon"], p["heading"],
                images_dir, api_key, args.radius,
            ): p
            for p in points
        }

        with tqdm(total=len(futures), unit="pt", desc="Street View") as bar:
            for future in as_completed(futures):
                pt  = futures[future]
                res = future.result()
                # Merge point metadata into result
                res.update({k: pt[k] for k in ("street_name", "highway_type", "edge_id") if k in pt})
                results.append(res)
                counts[res["status"]] += 1
                bar.set_postfix(
                    dl=counts["downloaded"],
                    skip=counts["exists"],
                    no_sv=counts["unavailable"],
                )
                bar.update(1)

    # ── Summary ───────────────────────────────────────────────────────────────
    log.info("─" * 60)
    log.info("Downloaded  : %d new images", counts["downloaded"])
    log.info("Already had : %d images",     counts["exists"])
    log.info("Unavailable : %d points",     counts["unavailable"])
    log.info("Blank/tiny  : %d rejected",   counts["blank"])
    log.info("Errors      : %d",            counts["error"])
    total_images = counts["downloaded"] + counts["exists"]
    log.info("Total ready : %d images in %s", total_images, images_dir)
    log.info("─" * 60)

    # ── Write manifest ────────────────────────────────────────────────────────
    manifest = {
        "generated_at"  : datetime.now(timezone.utc).isoformat(),
        "bbox"          : bbox,
        "sample_spacing": args.spacing,
        "sv_radius"     : args.radius,
        "total_points"  : len(points),
        "images_dir"    : str(images_dir),
        "counts"        : counts,
        "results"       : results,
    }
    manifest_path = output_root / "download_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Manifest saved → %s", manifest_path)
    log.info("Run 02_run_vlm_analysis.py next to analyse these images.")


if __name__ == "__main__":
    main()
