"""
GTFSPlugin — loads public transit stops from a GTFS feed into ext_transit_stops.

Default feed URL is auto-resolved from the bbox centroid using the Transitland API
(free, no key required). Override by setting GTFS_URL in .env.

Table: ext_transit_stops
  stop_id   VARCHAR — original GTFS stop_id
  name      VARCHAR — stop name
  stop_type VARCHAR — "bus", "metro", "tram", "rail", "other"
  routes    VARCHAR — comma-separated route short_names serving this stop
  lon       DOUBLE
  lat       DOUBLE
  geometry  GEOMETRY — ST_Point(lon, lat)
"""
from __future__ import annotations
import csv
import io
import logging
import os
import zipfile
from typing import Any

import requests

from .base_plugin import ExternalDataPlugin

logger = logging.getLogger(__name__)

# Transitland Atlas API — free, no auth, returns GTFS feed metadata + download URL
_TRANSITLAND_FEEDS_URL = "https://transit.land/api/v2/rest/feeds"

# Barcelona TMB hardcoded fallback when Transitland lookup fails
_BCN_GTFS_URL = "https://www.tmb.cat/documents/20182/84059/GTFS.zip"


class GTFSPlugin(ExternalDataPlugin):
    name = "gtfs"
    display_name = "Transit Stops (GTFS)"
    description = "Bus, metro and tram stops from public GTFS feeds. Enables commuter and tourist routing via public transport."
    is_live = False
    required_env_vars = []

    def get_schema_sql(self) -> str:
        return """
        CREATE TABLE IF NOT EXISTS ext_transit_stops (
            stop_id   VARCHAR,
            name      VARCHAR,
            stop_type VARCHAR,
            routes    VARCHAR,
            lon       DOUBLE,
            lat       DOUBLE,
            geometry  GEOMETRY
        )
        """

    def fetch(self, bbox: dict, con: Any, progress_cb=None, **kwargs) -> int:
        def _progress(pct: float, msg: str) -> None:
            if progress_cb:
                progress_cb(pct, msg)
            else:
                logger.info(f"[gtfs] {pct:.0f}% — {msg}")

        _progress(5, "Resolving GTFS feed URL…")
        gtfs_url = os.getenv("GTFS_URL") or _resolve_feed_url(bbox) or _BCN_GTFS_URL
        _progress(15, f"Downloading GTFS feed from {gtfs_url[:60]}…")

        try:
            resp = requests.get(gtfs_url, timeout=60, stream=True)
            resp.raise_for_status()
            raw = resp.content
        except Exception as e:
            raise RuntimeError(f"GTFS download failed: {e}")

        _progress(40, "Parsing stops.txt…")
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile as e:
            raise RuntimeError(f"Downloaded file is not a valid ZIP: {e}")

        if "stops.txt" not in zf.namelist():
            raise RuntimeError("GTFS ZIP has no stops.txt")

        stops = list(csv.DictReader(io.StringIO(zf.read("stops.txt").decode("utf-8-sig"))))

        # Build stop_id → routes mapping from stop_times + trips if available
        stop_routes: dict[str, set[str]] = {}
        if "stop_times.txt" in zf.namelist() and "trips.txt" in zf.namelist() and "routes.txt" in zf.namelist():
            _progress(55, "Building routes index…")
            routes_df = {r["route_id"]: r.get("route_short_name", r["route_id"])
                         for r in csv.DictReader(io.StringIO(zf.read("routes.txt").decode("utf-8-sig")))}
            trip_route = {t["trip_id"]: t["route_id"]
                          for t in csv.DictReader(io.StringIO(zf.read("trips.txt").decode("utf-8-sig")))}
            for st in csv.DictReader(io.StringIO(zf.read("stop_times.txt").decode("utf-8-sig"))):
                sid = st.get("stop_id", "")
                rname = routes_df.get(trip_route.get(st.get("trip_id", ""), ""), "")
                if sid and rname:
                    stop_routes.setdefault(sid, set()).add(rname)

        _progress(70, "Filtering stops to bbox…")
        min_lon, min_lat = bbox["min_lon"], bbox["min_lat"]
        max_lon, max_lat = bbox["max_lon"], bbox["max_lat"]

        rows = []
        for s in stops:
            try:
                lon = float(s.get("stop_lon", 0))
                lat = float(s.get("stop_lat", 0))
            except (ValueError, TypeError):
                continue
            if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
                continue
            stop_id = s.get("stop_id", "")
            name = s.get("stop_name", "")
            location_type = s.get("location_type", "0")
            # location_type: 0=stop, 1=station, 2=entrance, 3=generic, 4=boarding
            # Only keep 0 (platform/stop) and 1 (station entrance) for walking context
            if location_type not in ("", "0", "1"):
                continue
            routes_str = ", ".join(sorted(stop_routes.get(stop_id, set())))
            rows.append((stop_id, name, "transit", routes_str, lon, lat))

        _progress(85, f"Writing {len(rows)} stops to DuckDB…")
        con.execute(self.get_schema_sql())
        con.execute(f"DELETE FROM {self.table_name}")
        if rows:
            con.executemany(
                f"""INSERT INTO {self.table_name} (stop_id, name, stop_type, routes, lon, lat, geometry)
                   VALUES (?, ?, ?, ?, ?, ?, ST_Point(?, ?))""",
                [(r[0], r[1], r[2], r[3], r[4], r[5], r[4], r[5]) for r in rows]
            )

        self.mark_loaded(con, len(rows))
        _progress(100, f"Done — {len(rows)} transit stops loaded.")
        return len(rows)


def _resolve_feed_url(bbox: dict) -> str | None:
    """Try to find a GTFS feed for the bbox centroid via Transitland Atlas."""
    try:
        lon = (bbox["min_lon"] + bbox["max_lon"]) / 2
        lat = (bbox["min_lat"] + bbox["max_lat"]) / 2
        resp = requests.get(
            _TRANSITLAND_FEEDS_URL,
            params={"lon": lon, "lat": lat, "radius": 50000, "spec": "gtfs", "limit": 1},
            timeout=10,
        )
        resp.raise_for_status()
        feeds = resp.json().get("feeds", [])
        if feeds:
            urls = feeds[0].get("urls", {})
            return urls.get("static_current") or urls.get("static_historic", [None])[0]
    except Exception as e:
        logger.debug(f"Transitland lookup failed: {e}")
    return None
