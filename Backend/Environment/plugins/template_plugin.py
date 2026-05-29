"""
TemplatePlugin — starter template for custom external data sources.

Copy this file, rename it to <your_source>_plugin.py, and fill in the
sections marked with TODO. The PluginRegistry will auto-discover it.

Steps:
1. Set name, display_name, description (and is_live / required_env_vars if needed)
2. Define your DuckDB table schema in get_schema_sql()
3. Implement fetch() to download and write your data

The table MUST have a 'geometry GEOMETRY' column for agents to query it
spatially via model.get_nearby_external_data().
"""
from __future__ import annotations
from typing import Any

from .base_plugin import ExternalDataPlugin


class TemplatePlugin(ExternalDataPlugin):
    # TODO: set a unique slug (lowercase, no spaces) — becomes table name ext_{name}
    name = "template"

    # TODO: human-readable label shown in the UI card
    display_name = "Custom Data Source"

    # TODO: one sentence about what this data adds
    description = "Template plugin — replace with your own data source."

    # Set is_live=True if this plugin queries a live API per simulation step
    # (e.g. real-time crowd counts). Set required_env_vars to the .env keys needed.
    is_live = False
    required_env_vars = []  # e.g. ["MY_API_KEY"]

    def get_schema_sql(self) -> str:
        # TODO: define columns for your data. Keep geometry GEOMETRY for spatial queries.
        return """
        CREATE TABLE IF NOT EXISTS ext_template (
            id       VARCHAR,
            name     VARCHAR,
            category VARCHAR,
            value    DOUBLE,
            lon      DOUBLE,
            lat      DOUBLE,
            geometry GEOMETRY
        )
        """

    def fetch(self, bbox: dict, con: Any, progress_cb=None, **kwargs) -> int:
        """
        TODO: implement your fetch logic.

        bbox = {"min_lon": float, "min_lat": float, "max_lon": float, "max_lat": float}
        con  = open DuckDB connection with spatial extension loaded

        Guidelines:
        - Call progress_cb(pct 0-100, message) to update the UI progress bar
        - Write data into self.table_name (= "ext_template")
        - Call self.mark_loaded(con, row_count) before returning
        - Return the number of rows inserted

        Example: fetching a JSON endpoint and inserting rows
        """
        if progress_cb:
            progress_cb(10, "Fetching data…")

        # ── Replace this with your actual data retrieval ───────────────────
        # import requests
        # resp = requests.get("https://your-api.com/data", params={"bbox": ...})
        # resp.raise_for_status()
        # features = resp.json()["features"]
        # ──────────────────────────────────────────────────────────────────

        if progress_cb:
            progress_cb(60, "Writing to DuckDB…")

        con.execute(self.get_schema_sql())
        con.execute(f"DELETE FROM {self.table_name}")

        # TODO: insert your rows here:
        # con.executemany(
        #     f"INSERT INTO {self.table_name} (id, name, category, value, lon, lat, geometry) "
        #     "VALUES (?, ?, ?, ?, ?, ?, ST_Point(?, ?))",
        #     [(row["id"], row["name"], row["cat"], row["val"], row["lon"], row["lat"],
        #       row["lon"], row["lat"]) for row in features],
        # )

        row_count = 0  # TODO: replace with actual count

        self.mark_loaded(con, row_count)
        if progress_cb:
            progress_cb(100, f"Done — {row_count} rows loaded.")
        return row_count
