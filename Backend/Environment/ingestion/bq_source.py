"""
Google BigQuery source for Overture Maps data.

Fallback path when S3/httpfs is unavailable. Requires GCP credentials
(service account or Application Default Credentials).
"""

import os
import tempfile

BIGQUERY_PROJECT = "bigquery-public-data"
OVERTURE_DATASET = "overture_maps"


def get_bq_client(gcp_project: str | None = None):
    """Initialize BigQuery client (OAuth2 only — API keys not supported by BigQuery)."""
    from google.cloud import bigquery

    project_id = (
        gcp_project
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GCLOUD_PROJECT")
    )
    creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if creds_path and os.path.exists(creds_path):
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(creds_path)
        return bigquery.Client(credentials=creds, project=project_id or creds.project_id)

    import google.auth
    creds, default_project = google.auth.default()
    pid = project_id or default_project
    if hasattr(creds, "with_quota_project"):
        creds = creds.with_quota_project(pid)
    return bigquery.Client(credentials=creds, project=pid)


def setup_bigquery_client():
    """Legacy alias: initialize BigQuery client from environment credentials."""
    return get_bq_client()


def bq_buildings(bq_client, mem_con, bbox: dict, log_fn) -> bool:
    sql = f"""
        SELECT id, ST_AsBinary(geometry) as geometry,
               names.primary as name, height, num_floors, class as building_type,
               bbox.xmin as bbox_xmin, bbox.ymin as bbox_ymin,
               bbox.xmax as bbox_xmax, bbox.ymax as bbox_ymax
        FROM `{BIGQUERY_PROJECT}.{OVERTURE_DATASET}.building`
        WHERE bbox.xmin >= {bbox['min_lon']} AND bbox.ymin >= {bbox['min_lat']}
          AND bbox.xmax <= {bbox['max_lon']} AND bbox.ymax <= {bbox['max_lat']}
    """
    try:
        df = bq_client.query(sql).to_dataframe()
        if len(df) == 0:
            log_fn("  ✗ No buildings in bbox (BigQuery)")
            return False
        with tempfile.NamedTemporaryFile(suffix='.parquet', delete=False) as tmp:
            tmp_path = tmp.name
        df.to_parquet(tmp_path, index=False)
        mem_con.execute(f"""
            CREATE OR REPLACE TABLE buildings AS
            SELECT id, ST_GeomFromWKB(geometry) as geometry,
                   name, height, num_floors, building_type,
                   bbox_xmin, bbox_ymin, bbox_xmax, bbox_ymax
            FROM read_parquet('{tmp_path}')
        """)
        os.unlink(tmp_path)
        count = mem_con.execute("SELECT COUNT(*) FROM buildings").fetchone()[0]
        log_fn(f"  [OK] {count:,} buildings (BigQuery)")
        return True
    except Exception as exc:
        log_fn(f"  ✗ BQ buildings error: {exc}")
        return False


def bq_amenities(bq_client, mem_con, bbox: dict, log_fn) -> bool:
    sql = f"""
        SELECT id, ST_AsBinary(geometry) as geometry,
               names.primary as name, categories.primary as amenity,
               bbox.xmin as lon, bbox.ymin as lat
        FROM `{BIGQUERY_PROJECT}.{OVERTURE_DATASET}.place`
        WHERE bbox.xmin >= {bbox['min_lon']} AND bbox.ymin >= {bbox['min_lat']}
          AND bbox.xmax <= {bbox['max_lon']} AND bbox.ymax <= {bbox['max_lat']}
    """
    try:
        df = bq_client.query(sql).to_dataframe()
        if len(df) == 0:
            log_fn("  ✗ No places in bbox (BigQuery)")
            return False
        with tempfile.NamedTemporaryFile(suffix='.parquet', delete=False) as tmp:
            tmp_path = tmp.name
        df.to_parquet(tmp_path, index=False)
        mem_con.execute(f"""
            CREATE OR REPLACE TABLE amenities AS
            SELECT id, ST_GeomFromWKB(geometry) as geometry,
                   name, amenity, lon, lat
            FROM read_parquet('{tmp_path}')
        """)
        os.unlink(tmp_path)
        count = mem_con.execute("SELECT COUNT(*) FROM amenities").fetchone()[0]
        log_fn(f"  [OK] {count:,} amenities (BigQuery)")
        return True
    except Exception as exc:
        log_fn(f"  ✗ BQ amenities error: {exc}")
        return False


def bq_transport(bq_client, mem_con, bbox: dict, log_fn) -> bool:
    sql = f"""
        SELECT id, ST_AsBinary(geometry) as geometry,
               subtype as road_type, class as road_class, names.primary as name
        FROM `{BIGQUERY_PROJECT}.{OVERTURE_DATASET}.segment`
        WHERE (class IN ('pedestrian','footway','path','steps',
                         'living_street','residential','service'))
          AND bbox.xmin >= {bbox['min_lon']} AND bbox.ymin >= {bbox['min_lat']}
          AND bbox.xmax <= {bbox['max_lon']} AND bbox.ymax <= {bbox['max_lat']}
    """
    try:
        df = bq_client.query(sql).to_dataframe()
        if len(df) == 0:
            log_fn("  ✗ No walk edges in bbox (BigQuery)")
            return False
        with tempfile.NamedTemporaryFile(suffix='.parquet', delete=False) as tmp:
            tmp_path = tmp.name
        df.to_parquet(tmp_path, index=False)
        mem_con.execute(f"""
            CREATE OR REPLACE TABLE walk_edges AS
            SELECT id, ST_GeomFromWKB(geometry) as geometry,
                   road_type, road_class, name,
            ST_Length(geometry) as length
            FROM read_parquet('{tmp_path}')
        """)
        os.unlink(tmp_path)
        count = mem_con.execute("SELECT COUNT(*) FROM walk_edges").fetchone()[0]
        log_fn(f"  [OK] {count:,} walk edges (BigQuery)")
        return True
    except Exception as exc:
        log_fn(f"  ✗ BQ transport error: {exc}")
        return False
