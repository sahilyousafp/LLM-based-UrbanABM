"""
Overture Maps download pipeline.

OverturePipeline orchestrates fetching Overture Maps layers (buildings,
amenities, transport) into a DuckDB database.  S3/httpfs is the primary
path; Google BigQuery is the optional fallback.

Each source method delegates to the standalone functions in s3_source.py
and bq_source.py so they can be tested and reused independently.
"""

import duckdb
import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv

# ingestion/ → Environment/ → Backend/ → project root
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
load_dotenv(PROJECT_ROOT / ".env")

from .s3_source import setup_httpfs, s3_buildings, s3_amenities, s3_transport
from .bq_source import get_bq_client, bq_buildings, bq_amenities, bq_transport

# ---------------------------------------------------------------------------
# Module-level constants (re-exported from this module for backwards compat)
# ---------------------------------------------------------------------------
PLACE_NAME = "Eixample, Barcelona, Spain"
DB_DIR = Path(__file__).parent.parent  # Backend/Environment/
_DEFAULT_DB_PATH = DB_DIR / "eixample_overture.duckdb"

BBOX_DEFAULT = {
    'min_lon': 2.1500,
    'min_lat': 41.3862,
    'max_lon': 2.1740,
    'max_lat': 41.4042,
}

ALL_LAYERS = ["buildings", "amenities", "transport"]


# ===========================================================================
# OverturePipeline
# ===========================================================================

class OverturePipeline:
    """
    Download Overture Maps data into DuckDB.

    Parameters
    ----------
    bbox : dict
        Keys: min_lon, min_lat, max_lon, max_lat
    location_name : str
        Human-readable name for logging.
    layers : list[str]
        Any subset of ["buildings", "amenities", "transport"].
    gcp_project : str | None
        If set, use BigQuery instead of S3/httpfs.
    db_path : Path | None
        Target DuckDB file. Defaults to eixample_overture.duckdb.
    job_id : str | None
        Used to name the intermediate pending database file.
    """

    def __init__(
        self,
        bbox: dict,
        location_name: str = "custom_zone",
        layers: list | None = None,
        gcp_project: str | None = None,
        db_path: "Path | None" = None,
        job_id: str | None = None,
    ):
        self.bbox = bbox
        self.location_name = location_name
        self.layers = layers if layers is not None else list(ALL_LAYERS)
        self.gcp_project = gcp_project
        self.db_path = db_path if db_path is not None else _DEFAULT_DB_PATH
        self.job_id = job_id or "unnamed"
        self.pending_path = self.db_path.parent / f"{self.job_id}_pending.duckdb"
        self.progress: dict = {"pct": 0.0, "log": [], "status": "idle"}

    # ------------------------------------------------------------------
    # Progress / logging
    # ------------------------------------------------------------------

    def _log(self, msg: str, pct: float | None = None) -> None:
        print(msg, flush=True)
        self.progress["log"].append(msg)
        if pct is not None:
            self.progress["pct"] = pct

    # ------------------------------------------------------------------
    # S3 delegates
    # ------------------------------------------------------------------

    def _setup_httpfs(self, con: duckdb.DuckDBPyConnection) -> None:
        setup_httpfs(con)

    def _s3_buildings(self, mem_con: duckdb.DuckDBPyConnection) -> bool:
        return s3_buildings(mem_con, self.bbox, self._log)

    def _s3_amenities(self, mem_con: duckdb.DuckDBPyConnection) -> bool:
        return s3_amenities(mem_con, self.bbox, self._log)

    def _s3_transport(self, mem_con: duckdb.DuckDBPyConnection) -> bool:
        return s3_transport(mem_con, self.bbox, self._log)

    # ------------------------------------------------------------------
    # BigQuery delegates
    # ------------------------------------------------------------------

    def _get_bq_client(self):
        return get_bq_client(self.gcp_project)

    def _bq_buildings(self, bq_client, mem_con: duckdb.DuckDBPyConnection) -> bool:
        return bq_buildings(bq_client, mem_con, self.bbox, self._log)

    def _bq_amenities(self, bq_client, mem_con: duckdb.DuckDBPyConnection) -> bool:
        return bq_amenities(bq_client, mem_con, self.bbox, self._log)

    def _bq_transport(self, bq_client, mem_con: duckdb.DuckDBPyConnection) -> bool:
        return bq_transport(bq_client, mem_con, self.bbox, self._log)

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Synchronous run — call from a thread."""
        self.progress["status"] = "running"
        self.progress["log"] = []
        self.progress["pct"] = 0.0

        try:
            self._log(
                f"Starting Overture download: {self.location_name} "
                f"({self.bbox}) layers={self.layers}",
                pct=0.0,
            )

            # ── 1. Build tables in in-memory DuckDB ───────────────────
            mem_con = duckdb.connect(":memory:")
            if not self.gcp_project:
                self._log("Method: DuckDB httpfs (S3 anonymous)")
                try:
                    self._setup_httpfs(mem_con)
                except Exception as exc:
                    self._log(f"  httpfs setup warning: {exc}")
            else:
                self._log(f"Method: BigQuery (project={self.gcp_project})")

            n_layers = len(self.layers)
            for idx, layer in enumerate(self.layers):
                pct_start = 10 + idx * (70 / n_layers)
                pct_end = 10 + (idx + 1) * (70 / n_layers)
                self._log(f"[{idx+1}/{n_layers}] Downloading layer: {layer}", pct=pct_start)

                if self.gcp_project:
                    bq_client = self._get_bq_client()
                    ok = False
                    if layer == "buildings":
                        ok = self._bq_buildings(bq_client, mem_con)
                    elif layer == "amenities":
                        ok = self._bq_amenities(bq_client, mem_con)
                    elif layer == "transport":
                        ok = self._bq_transport(bq_client, mem_con)
                else:
                    ok = False
                    if layer == "buildings":
                        ok = self._s3_buildings(mem_con)
                    elif layer == "amenities":
                        ok = self._s3_amenities(mem_con)
                    elif layer == "transport":
                        ok = self._s3_transport(mem_con)

                if not ok:
                    self._log(f"  Warning: {layer} layer had errors (continuing)")
                self._log(f"  Layer {layer} done.", pct=pct_end)

            # ── 2. Merge into pending database ────────────────────────
            self._log("Merging tables into main database…", pct=85.0)

            table_map = {
                "buildings": "buildings",
                "amenities": "amenities",
                "transport": "walk_edges",
            }
            tables_to_merge = []
            for layer in self.layers:
                tbl = table_map.get(layer)
                if tbl:
                    try:
                        mem_con.execute(f"SELECT 1 FROM {tbl} LIMIT 1")
                        tables_to_merge.append(tbl)
                    except Exception:
                        pass

            if tables_to_merge:
                if self.pending_path.exists():
                    self.pending_path.unlink()
                pending_con = duckdb.connect(str(self.pending_path))
                pending_con.execute("INSTALL spatial; LOAD spatial;")
                for tbl in tables_to_merge:
                    try:
                        with tempfile.NamedTemporaryFile(suffix='.parquet', delete=False) as tmp:
                            tmp_path = tmp.name
                        if tbl in ("buildings", "amenities", "walk_edges"):
                            mem_con.execute(f"""
                                COPY (
                                    SELECT * REPLACE (ST_AsWKB(geometry) AS geometry)
                                    FROM {tbl}
                                ) TO '{tmp_path}' (FORMAT PARQUET)
                            """)
                            pending_con.execute(f"""
                                CREATE OR REPLACE TABLE {tbl} AS
                                SELECT * REPLACE (ST_GeomFromWKB(geometry) AS geometry)
                                FROM read_parquet('{tmp_path}')
                            """)
                        else:
                            mem_con.execute(f"COPY {tbl} TO '{tmp_path}' (FORMAT PARQUET)")
                            pending_con.execute(f"""
                                CREATE OR REPLACE TABLE {tbl} AS
                                SELECT * FROM read_parquet('{tmp_path}')
                            """)
                        os.unlink(tmp_path)
                        count = pending_con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                        self._log(f"  ✓ {tbl}: {count:,} rows written to pending database")
                    except Exception as exc:
                        self._log(f"  ✗ Failed to merge {tbl}: {exc}")
                pending_con.close()
                self.progress["pending_path"] = str(self.pending_path)
            else:
                self._log("  No tables to merge (all layers had errors)")

            mem_con.close()
            self._log("Done.", pct=100.0)
            self.progress["status"] = "done"

        except Exception as exc:
            self.progress["status"] = "error"
            self._log(f"Pipeline error: {exc}")


# ===========================================================================
# CLI entry point
# ===========================================================================

def main():
    """CLI: download Overture data and (optionally) load streetview perception."""
    import argparse
    from .perception import load_streetview_perception

    parser = argparse.ArgumentParser(description="Overture Maps → DuckDB pipeline")
    parser.add_argument("--bbox", default=None,
                        help="w,s,e,n bounding box (default: Eixample)")
    parser.add_argument("--layers", default="buildings,amenities,transport",
                        help="Comma-separated layers (default: all)")
    parser.add_argument("--gcp-project", default=None,
                        help="GCP project for BigQuery fallback")
    parser.add_argument("--location-name", default="Eixample Barcelona",
                        help="Human-readable location name")
    parser.add_argument("--no-streetview", action="store_true",
                        help="Skip loading local streetview perception JSONs")
    args = parser.parse_args()

    if args.bbox:
        parts = [float(x) for x in args.bbox.split(",")]
        bbox = {"min_lon": parts[0], "min_lat": parts[1],
                "max_lon": parts[2], "max_lat": parts[3]}
    else:
        bbox = BBOX_DEFAULT

    layers = [l.strip() for l in args.layers.split(",") if l.strip()]

    print("=" * 60)
    print("OVERTURE MAPS → DUCKDB PIPELINE")
    print("=" * 60)
    print(f"Location : {args.location_name}")
    print(f"BBox     : {bbox}")
    print(f"Layers   : {layers}")
    print(f"DB path  : {_DEFAULT_DB_PATH}")
    if args.gcp_project:
        print(f"GCP      : {args.gcp_project}")
    print("=" * 60 + "\n")

    pipeline = OverturePipeline(
        bbox=bbox,
        location_name=args.location_name,
        layers=layers,
        gcp_project=args.gcp_project,
        db_path=_DEFAULT_DB_PATH,
    )
    pipeline.run()

    if not args.no_streetview and pipeline.progress["status"] in ("done", "error"):
        print("\nLoading local streetview perception data…")
        con = duckdb.connect(str(_DEFAULT_DB_PATH))
        con.execute("INSTALL spatial; LOAD spatial;")
        load_streetview_perception(con)
        con.close()

    print("\n" + ("✅ SUCCESS" if pipeline.progress["status"] == "done" else "❌ ERRORS OCCURRED"))
    for line in pipeline.progress["log"][-20:]:
        print(" ", line)
