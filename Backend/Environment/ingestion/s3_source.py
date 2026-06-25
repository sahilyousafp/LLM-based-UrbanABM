"""
S3/httpfs source for Overture Maps data.

Standalone functions that fetch Overture layers from the public S3 bucket
using DuckDB's httpfs extension (no authentication required).
"""

import os
import duckdb

OVERTURE_RELEASE = os.getenv("OVERTURE_RELEASE", "2024-11-13.0")


def setup_httpfs(con: duckdb.DuckDBPyConnection) -> None:
    """Configure DuckDB httpfs for anonymous S3 access."""
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute("SET s3_region='us-west-2';")
    con.execute("SET s3_access_key_id='';")
    con.execute("SET s3_secret_access_key='';")
    con.execute("SET s3_use_ssl=true;")


def s3_buildings(
    mem_con: duckdb.DuckDBPyConnection,
    bbox: dict,
    log_fn,
    release: str = OVERTURE_RELEASE,
) -> bool:
    s3_path = f"s3://overturemaps-us-west-2/release/{release}/theme=buildings/type=building/*"
    log_fn(f"  Querying Overture buildings: {s3_path}")
    sql = f"""
        CREATE OR REPLACE TABLE buildings AS
        SELECT
            id,
            geometry,
            names.primary as name,
            height,
            num_floors,
            class as building_type,
            bbox.xmin as bbox_xmin,
            bbox.ymin as bbox_ymin,
            bbox.xmax as bbox_xmax,
            bbox.ymax as bbox_ymax
        FROM read_parquet('{s3_path}')
        WHERE bbox.xmin >= {bbox['min_lon']}
          AND bbox.ymin >= {bbox['min_lat']}
          AND bbox.xmax <= {bbox['max_lon']}
          AND bbox.ymax <= {bbox['max_lat']}
    """
    try:
        mem_con.execute(sql)
        count = mem_con.execute("SELECT COUNT(*) FROM buildings").fetchone()[0]
        log_fn(f"  [OK] {count:,} buildings loaded")
        return True
    except Exception as exc:
        log_fn(f"  ✗ buildings error: {exc}")
        return False


def s3_amenities(
    mem_con: duckdb.DuckDBPyConnection,
    bbox: dict,
    log_fn,
    release: str = OVERTURE_RELEASE,
) -> bool:
    s3_path = f"s3://overturemaps-us-west-2/release/{release}/theme=places/type=place/*"
    log_fn(f"  Querying Overture places: {s3_path}")
    sql = f"""
        CREATE OR REPLACE TABLE amenities AS
        SELECT
            id,
            geometry,
            names.primary as name,
            categories.primary as amenity,
            bbox.xmin as lon,
            bbox.ymin as lat
        FROM read_parquet('{s3_path}')
        WHERE bbox.xmin >= {bbox['min_lon']}
            AND bbox.ymin >= {bbox['min_lat']}
            AND bbox.xmax <= {bbox['max_lon']}
            AND bbox.ymax <= {bbox['max_lat']}
    """
    try:
        mem_con.execute(sql)
        count = mem_con.execute("SELECT COUNT(*) FROM amenities").fetchone()[0]
        log_fn(f"  [OK] {count:,} amenities loaded")
        return True
    except Exception as exc:
        log_fn(f"  ✗ amenities error: {exc}")
        return False


def s3_transport(
    mem_con: duckdb.DuckDBPyConnection,
    bbox: dict,
    log_fn,
    release: str = OVERTURE_RELEASE,
) -> bool:
    s3_path = f"s3://overturemaps-us-west-2/release/{release}/theme=transportation/type=segment/*"
    log_fn(f"  Querying Overture transport: {s3_path}")
    sql = f"""
        CREATE OR REPLACE TABLE walk_edges AS
        SELECT
            id,
            geometry,
            subtype as road_type,
            class as road_class,
            names.primary as name,
            ST_Length(geometry) as length
        FROM read_parquet('{s3_path}')
        WHERE (class IN ('pedestrian','footway','path','steps',
                         'living_street','residential','service'))
          AND bbox.xmin >= {bbox['min_lon']}
          AND bbox.ymin >= {bbox['min_lat']}
          AND bbox.xmax <= {bbox['max_lon']}
          AND bbox.ymax <= {bbox['max_lat']}
    """
    try:
        mem_con.execute(sql)
        count = mem_con.execute("SELECT COUNT(*) FROM walk_edges").fetchone()[0]
        log_fn(f"  [OK] {count:,} walk edges loaded")
        return True
    except Exception as exc:
        log_fn(f"  ✗ transport error: {exc}")
        return False
