"""
ExternalDataPlugin — base class for all external data source plugins.

To add a new data source:
1. Create a new file in this directory (e.g. my_source_plugin.py)
2. Subclass ExternalDataPlugin
3. Set class attributes: name, display_name, description
4. Implement get_schema_sql() and fetch()
5. The PluginRegistry auto-discovers it — no registration needed
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any


class ExternalDataPlugin(ABC):
    """
    Base class for external data source plugins.

    Convention: every plugin writes to a DuckDB table named ext_{name}.
    The ext_sources registry table tracks load status across all plugins.
    """

    # ── Class-level attributes to override ────────────────────────────────────
    name: str = ""              # slug — used as table suffix: ext_{name}
    display_name: str = ""      # shown in UI
    description: str = ""       # one-line description shown in UI
    is_live: bool = False        # False = download button; True = env-var config (per-step)
    required_env_vars: list[str] = []   # env vars needed (live plugins only)

    # ── Table name convention ──────────────────────────────────────────────────
    @property
    def table_name(self) -> str:
        return f"ext_{self.name}"

    # ── Abstract interface ─────────────────────────────────────────────────────
    @abstractmethod
    def get_schema_sql(self) -> str:
        """
        Return a CREATE TABLE IF NOT EXISTS SQL string for this plugin's table.
        Always include: geometry GEOMETRY for spatial queries.
        """

    @abstractmethod
    def fetch(self, bbox: dict, con: Any, progress_cb=None, **kwargs) -> int:
        """
        Fetch data from the external source and write it into the DuckDB connection.
        bbox: {"min_lon": float, "min_lat": float, "max_lon": float, "max_lat": float}
        con:  open duckdb.DuckDBPyConnection (read-write, spatial extension loaded)
        progress_cb: optional callable(pct: float, msg: str) for job progress updates
        Returns: number of rows inserted
        """

    # ── Status helpers (default implementation reads ext_sources table) ────────
    def get_status(self, con: Any) -> dict:
        """Return {loaded: bool, row_count: int, last_updated: str|None}."""
        try:
            result = con.execute(
                "SELECT row_count, loaded_at FROM ext_sources WHERE source_name = ?",
                [self.name]
            ).fetchone()
            if result:
                return {"loaded": True, "row_count": result[0], "last_updated": str(result[1])}
        except Exception:
            pass
        return {"loaded": False, "row_count": 0, "last_updated": None}

    def mark_loaded(self, con: Any, row_count: int) -> None:
        """Record load status in the ext_sources registry table."""
        _ensure_sources_table(con)
        con.execute("""
            INSERT OR REPLACE INTO ext_sources (source_name, row_count, loaded_at)
            VALUES (?, ?, ?)
        """, [self.name, row_count, datetime.utcnow().isoformat()])

    def drop_table(self, con: Any) -> None:
        """Drop this plugin's table and remove its registry entry."""
        con.execute(f"DROP TABLE IF EXISTS {self.table_name}")
        try:
            con.execute("DELETE FROM ext_sources WHERE source_name = ?", [self.name])
        except Exception:
            pass

    # ── Metadata for UI ────────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "is_live": self.is_live,
            "required_env_vars": self.required_env_vars,
            "table_name": self.table_name,
        }


def _ensure_sources_table(con: Any) -> None:
    """Create the ext_sources registry table if it doesn't exist."""
    con.execute("""
        CREATE TABLE IF NOT EXISTS ext_sources (
            source_name VARCHAR PRIMARY KEY,
            row_count   INTEGER,
            loaded_at   VARCHAR
        )
    """)
