"""
Street View Fetcher — Download images from the Google Street View Static API.
"""

import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPT_DIR.parent / ".env")

STREETVIEW_URL = "https://maps.googleapis.com/maps/api/streetview"
DEFAULT_SIZE = "640x640"
DEFAULT_FOV = 90
DEFAULT_PITCH = 0


def fetch_streetview(
    lat: float,
    lng: float,
    heading: float = 0,
    pitch: float = DEFAULT_PITCH,
    fov: int = DEFAULT_FOV,
    size: str = DEFAULT_SIZE,
    output_dir: str | Path | None = None,
    api_key: str | None = None,
) -> Path:
    """Fetch a single Street View image and save it to *output_dir*."""
    api_key = api_key or os.getenv("GOOGLE_STREETVIEW_API_KEY", "")
    if not api_key:
        raise ValueError("GOOGLE_STREETVIEW_API_KEY not set in environment or .env")

    params = {
        "size": size,
        "location": f"{lat},{lng}",
        "heading": heading,
        "pitch": pitch,
        "fov": fov,
        "key": api_key,
    }

    resp = requests.get(STREETVIEW_URL, params=params, timeout=30)
    resp.raise_for_status()

    if output_dir is None:
        output_dir = SCRIPT_DIR / "output" / "images"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    filename = f"sv_{lat:.6f}_{lng:.6f}_h{int(heading)}.jpg"
    filepath = output_dir / filename
    filepath.write_bytes(resp.content)
    return filepath


def fetch_batch(
    locations: list[dict],
    output_dir: str | Path | None = None,
    api_key: str | None = None,
    delay: float = 0.25,
) -> list[Path]:
    """Fetch multiple Street View images.

    *locations* is a list of dicts with keys: lat, lng, and optionally heading,
    pitch, fov, size.
    """
    paths: list[Path] = []
    for loc in locations:
        p = fetch_streetview(
            lat=loc["lat"],
            lng=loc["lng"],
            heading=loc.get("heading", 0),
            pitch=loc.get("pitch", DEFAULT_PITCH),
            fov=loc.get("fov", DEFAULT_FOV),
            size=loc.get("size", DEFAULT_SIZE),
            output_dir=output_dir,
            api_key=api_key,
        )
        paths.append(p)
        if delay and len(locations) > 1:
            time.sleep(delay)
    return paths


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python streetview_fetcher.py <lat> <lng> [heading]")
        sys.exit(1)
    lat, lng = float(sys.argv[1]), float(sys.argv[2])
    heading = float(sys.argv[3]) if len(sys.argv) > 3 else 0
    path = fetch_streetview(lat, lng, heading=heading)
    print(f"Saved: {path}")
