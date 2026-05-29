"""
WeatherPlugin — snapshots current weather conditions into ext_weather.

Uses Open-Meteo (https://open-meteo.com) — completely free, no API key required.
Fetches once at download time; stored as a static snapshot in DuckDB.

Table: ext_weather
  fetched_at   VARCHAR  — ISO timestamp of the snapshot
  condition    VARCHAR  — human-readable description (e.g. "Light rain")
  temp_c       DOUBLE   — temperature in °C
  rain_mm      DOUBLE   — precipitation in mm/h (current)
  wind_ms      DOUBLE   — wind speed in m/s
  humidity_pct DOUBLE   — relative humidity %
  lon          DOUBLE   — centroid longitude (of the bbox)
  lat          DOUBLE   — centroid latitude
  geometry     GEOMETRY — ST_Point(lon, lat)

Weather codes from Open-Meteo WMO standard:
  0 = Clear sky, 1-3 = Partly cloudy, 45-48 = Fog,
  51-67 = Drizzle/Rain, 71-77 = Snow, 80-82 = Rain showers,
  95 = Thunderstorm
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Any

import requests

from .base_plugin import ExternalDataPlugin

logger = logging.getLogger(__name__)

_WMO_DESCRIPTIONS = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Icy fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow",
    80: "Rain showers", 81: "Heavy showers", 82: "Violent showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Heavy thunderstorm with hail",
}


class WeatherPlugin(ExternalDataPlugin):
    name = "weather"
    display_name = "Weather Snapshot"
    description = "Current weather at the simulation area (Open-Meteo, free). Affects agent comfort and energy decay rates."
    is_live = False
    required_env_vars = []

    def get_schema_sql(self) -> str:
        return """
        CREATE TABLE IF NOT EXISTS ext_weather (
            fetched_at   VARCHAR,
            condition    VARCHAR,
            temp_c       DOUBLE,
            rain_mm      DOUBLE,
            wind_ms      DOUBLE,
            humidity_pct DOUBLE,
            lon          DOUBLE,
            lat          DOUBLE,
            geometry     GEOMETRY
        )
        """

    def fetch(self, bbox: dict, con: Any, progress_cb=None, **kwargs) -> int:
        def _progress(pct: float, msg: str) -> None:
            if progress_cb:
                progress_cb(pct, msg)

        lon = (bbox["min_lon"] + bbox["max_lon"]) / 2
        lat = (bbox["min_lat"] + bbox["max_lat"]) / 2

        _progress(20, f"Fetching weather at ({lat:.4f}, {lon:.4f})…")

        try:
            resp = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,rain,wind_speed_10m,relative_humidity_2m,weather_code",
                    "wind_speed_unit": "ms",
                    "timezone": "auto",
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            raise RuntimeError(f"Open-Meteo request failed: {e}")

        current = data.get("current", {})
        weather_code = int(current.get("weather_code", 0))
        condition = _WMO_DESCRIPTIONS.get(weather_code, f"Code {weather_code}")
        temp_c = float(current.get("temperature_2m", 20.0))
        rain_mm = float(current.get("rain", 0.0))
        wind_ms = float(current.get("wind_speed_10m", 0.0))
        humidity = float(current.get("relative_humidity_2m", 50.0))
        fetched_at = datetime.now(timezone.utc).isoformat()

        _progress(80, f"Writing: {condition}, {temp_c:.1f}°C, rain={rain_mm}mm/h…")

        con.execute(self.get_schema_sql())
        con.execute(f"DELETE FROM {self.table_name}")
        con.execute(
            f"""INSERT INTO {self.table_name}
               (fetched_at, condition, temp_c, rain_mm, wind_ms, humidity_pct, lon, lat, geometry)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ST_Point(?, ?))""",
            [fetched_at, condition, temp_c, rain_mm, wind_ms, humidity, lon, lat, lon, lat],
        )

        self.mark_loaded(con, 1)
        _progress(100, f"Done — {condition}, {temp_c:.1f}°C.")
        return 1
