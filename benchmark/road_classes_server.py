#!/usr/bin/env python3
"""
Standalone server for road_classes.html benchmark.

Reads directly from eixample_overture.duckdb — no main backend required.

Usage:
    python benchmark/road_classes_server.py
    # then open http://localhost:8002
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE        = Path(__file__).parent
PROJECT_ROOT = HERE.parent
DB_PATH     = PROJECT_ROOT / "Backend" / "Environment" / "eixample_overture.duckdb"
PORT        = int(sys.argv[1]) if len(sys.argv) > 1 else 8002


# ── .env loader ──────────────────────────────────────────────────────────────

def _load_env() -> dict[str, str]:
    for candidate in (PROJECT_ROOT / ".env", PROJECT_ROOT / ".env.example"):
        if candidate.exists():
            env: dict[str, str] = {}
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
            return env
    return {}


# ── DuckDB query ──────────────────────────────────────────────────────────────

def _build_geojson() -> bytes:
    """Query walk_edges from DuckDB and return GeoJSON bytes (cached after first call)."""
    import duckdb

    print(f"[road_classes] Opening {DB_PATH} …", flush=True)
    con = duckdb.connect(str(DB_PATH), read_only=True)
    con.install_extension("spatial")
    con.load_extension("spatial")

    rows = con.execute("""
        SELECT
            rowid          AS edge_id,
            ST_AsGeoJSON(geometry) AS geom_json,
            road_class
        FROM walk_edges
    """).fetchall()
    con.close()

    features = []
    counts: dict[str, int] = {}
    for edge_id, geom_json, road_class in rows:
        if not geom_json:
            continue
        rc = road_class or "unknown"
        counts[rc] = counts.get(rc, 0) + 1
        try:
            features.append({
                "type": "Feature",
                "geometry": json.loads(geom_json),
                "properties": {"road_class": rc, "edge_id": int(edge_id)},
            })
        except Exception:
            continue

    result = {"type": "FeatureCollection", "features": features, "counts": counts}
    print(f"[road_classes] Loaded {len(features)} edges, {len(counts)} classes", flush=True)
    return json.dumps(result).encode()


# ── HTTP handler ──────────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    _geojson_cache: bytes | None = None

    def log_message(self, fmt, *args):  # quieter logs
        print(f"  {self.address_string()} {fmt % args}", flush=True)

    def do_GET(self):
        path = urlparse(self.path).path

        if path in ("/", "/road_classes.html"):
            self._serve_file(HERE / "road_classes.html", "text/html; charset=utf-8")

        elif path == "/api/config":
            env = _load_env()
            self._send_json(json.dumps({"mapbox_token": env.get("MAPBOX_TOKEN", "")}).encode())

        elif path == "/api/road_classes":
            if _Handler._geojson_cache is None:
                try:
                    _Handler._geojson_cache = _build_geojson()
                except Exception as exc:
                    err = json.dumps({"error": str(exc), "type": "FeatureCollection",
                                      "features": [], "counts": {}}).encode()
                    self._send_json(err, status=500)
                    return
            self._send_json(_Handler._geojson_cache)

        else:
            self.send_error(404)

    def _serve_file(self, fpath: Path, content_type: str):
        if not fpath.exists():
            self.send_error(404)
            return
        data = fpath.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, data: bytes, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not DB_PATH.exists():
        print(f"ERROR: database not found at {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    server = HTTPServer(("127.0.0.1", PORT), _Handler)
    print(f"Road-classes server → http://localhost:{PORT}")
    print(f"Open:  http://localhost:{PORT}/road_classes.html")
    print("Press Ctrl-C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
