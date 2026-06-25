"""
Export data from DuckDB to SQLite+SpatiaLite and PostgreSQL+PostGIS
for benchmark comparison in notebook 01.

Usage:
    python benchmark/scripts/setup_benchmark_dbs.py [--sqlite] [--postgis] [--all]
"""
import argparse
import sys
import time
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).parent.parent.parent
DUCK_DB_PATH = PROJECT_ROOT / "Backend" / "Environment" / "eixample_overture.duckdb"
SQLITE_DB_PATH = PROJECT_ROOT / "benchmark" / "data" / "eixample_spatialite.db"

PG_DSN = "host=localhost port=5433 dbname=eixample_bench user=postgres password=postgres"

TABLES = {
    "amenities": {
        "geom_type": "POINT",
        "columns": [
            ("id", "TEXT"),
            ("name", "TEXT"),
            ("amenity", "TEXT"),
            ("amenity_tags", "TEXT"),
            ("address", "TEXT"),
            ("website", "TEXT"),
            ("phone", "TEXT"),
            ("lon", "REAL"),
            ("lat", "REAL"),
        ],
    },
    "buildings": {
        "geom_type": "MULTIPOLYGON",
        "columns": [
            ("id", "TEXT"),
            ("name", "TEXT"),
            ("height", "REAL"),
            ("num_floors", "INTEGER"),
            ("building_type", "TEXT"),
            ("bbox_minx", "REAL"),
            ("bbox_miny", "REAL"),
            ("bbox_maxx", "REAL"),
            ("bbox_maxy", "REAL"),
        ],
    },
    "walk_edges": {
        "geom_type": "LINESTRING",
        "columns": [
            ("id", "TEXT"),
            ("road_type", "TEXT"),
            ("road_class", "TEXT"),
            ("name", "TEXT"),
            ("level", "INTEGER"),
            ("is_bridge", "INTEGER"),
            ("is_tunnel", "INTEGER"),
            ("width", "REAL"),
            ("surface", "INTEGER"),
            ("length", "REAL"),
        ],
    },
}


def read_duckdb_table(table: str) -> list[dict]:
    con = duckdb.connect(str(DUCK_DB_PATH), read_only=True)
    con.execute("LOAD spatial")
    col_names = [c[0] for c in TABLES[table]["columns"]]
    select_cols = ", ".join(col_names)
    rows = con.execute(
        f"SELECT {select_cols}, ST_AsText(geometry) AS wkt FROM {table}"
    ).fetchall()
    con.close()
    return [dict(zip(col_names + ["wkt"], row)) for row in rows]


# ── SpatiaLite ──────────────────────────────────────────────────────────

def export_to_spatialite():
    import sqlite3

    if SQLITE_DB_PATH.exists():
        SQLITE_DB_PATH.unlink()
    SQLITE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(str(SQLITE_DB_PATH))
    con.enable_load_extension(True)
    loaded = False
    for ext in ("mod_spatialite", "spatialite"):
        try:
            con.execute(f"SELECT load_extension('{ext}')")
            loaded = True
            break
        except Exception:
            continue
    if not loaded:
        con.close()
        print("ERROR: SpatiaLite extension not found.")
        print("  Install: conda install -c conda-forge libspatialite")
        print("  Or download mod_spatialite.dll from gaia-gis.it")
        sys.exit(1)

    con.execute("SELECT InitSpatialMetaData(1)")

    for table, spec in TABLES.items():
        col_defs = ", ".join(f"{c} {t}" for c, t in spec["columns"])
        con.execute(f"DROP TABLE IF EXISTS {table}")
        con.execute(f"CREATE TABLE {table} ({col_defs})")
        con.execute(
            f"SELECT AddGeometryColumn('{table}', 'geometry', 4326, "
            f"'{spec['geom_type']}', 'XY')"
        )

        rows = read_duckdb_table(table)
        col_names = [c[0] for c in spec["columns"]]
        placeholders = ", ".join(["?"] * len(col_names))
        insert_sql = (
            f"INSERT INTO {table} ({', '.join(col_names)}, geometry) "
            f"VALUES ({placeholders}, GeomFromText(?, 4326))"
        )

        batch = []
        for row in rows:
            vals = tuple(row[c] for c in col_names) + (row["wkt"],)
            batch.append(vals)
            if len(batch) >= 500:
                con.executemany(insert_sql, batch)
                batch.clear()
        if batch:
            con.executemany(insert_sql, batch)

        con.execute(f"SELECT CreateSpatialIndex('{table}', 'geometry')")
        count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {count} rows exported")

    con.commit()
    con.close()
    size_mb = SQLITE_DB_PATH.stat().st_size / 1024 / 1024
    print(f"  SQLite DB: {SQLITE_DB_PATH} ({size_mb:.1f} MB)")


# ── PostGIS ─────────────────────────────────────────────────────────────

def wait_for_postgres(dsn: str, timeout: int = 30) -> bool:
    import psycopg2
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            con = psycopg2.connect(dsn)
            con.close()
            return True
        except psycopg2.OperationalError:
            time.sleep(1)
    return False


def export_to_postgis():
    import psycopg2

    if not wait_for_postgres(PG_DSN):
        print("ERROR: PostgreSQL not reachable on port 5433.")
        print("  Start: docker compose -f benchmark/docker-compose.benchmark.yml up -d")
        sys.exit(1)

    con = psycopg2.connect(PG_DSN)
    con.autocommit = True
    cur = con.cursor()
    cur.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    pg_type_map = {"TEXT": "TEXT", "REAL": "DOUBLE PRECISION", "INTEGER": "INTEGER"}

    for table, spec in TABLES.items():
        cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        col_defs = ", ".join(
            f"{c} {pg_type_map.get(t, t)}" for c, t in spec["columns"]
        )
        cur.execute(
            f"CREATE TABLE {table} ({col_defs}, "
            f"geometry geometry({spec['geom_type']}, 4326))"
        )

        rows = read_duckdb_table(table)
        col_names = [c[0] for c in spec["columns"]]
        placeholders = ", ".join(["%s"] * len(col_names))
        insert_sql = (
            f"INSERT INTO {table} ({', '.join(col_names)}, geometry) "
            f"VALUES ({placeholders}, ST_GeomFromText(%s, 4326))"
        )

        batch = []
        for row in rows:
            vals = tuple(row[c] for c in col_names) + (row["wkt"],)
            batch.append(vals)
            if len(batch) >= 500:
                cur.executemany(insert_sql, batch)
                batch.clear()
        if batch:
            cur.executemany(insert_sql, batch)

        cur.execute(
            f"CREATE INDEX idx_{table}_geom ON {table} USING GIST (geometry)"
        )
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        count = cur.fetchone()[0]
        print(f"  {table}: {count} rows exported")

    con.close()
    print(f"  PostGIS DB: {PG_DSN}")


# ── Main ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Export DuckDB data for benchmarks")
    parser.add_argument("--sqlite", action="store_true", help="Export to SpatiaLite")
    parser.add_argument("--postgis", action="store_true", help="Export to PostGIS")
    parser.add_argument("--all", action="store_true", help="Export to both")
    args = parser.parse_args()

    if not any([args.sqlite, args.postgis, args.all]):
        args.all = True

    if not DUCK_DB_PATH.exists():
        print(f"ERROR: DuckDB not found at {DUCK_DB_PATH}")
        sys.exit(1)

    if args.sqlite or args.all:
        print("\n── Exporting to SQLite+SpatiaLite ──")
        export_to_spatialite()

    if args.postgis or args.all:
        print("\n── Exporting to PostgreSQL+PostGIS ──")
        export_to_postgis()

    print("\nDone.")


if __name__ == "__main__":
    main()
