"""
Backwards-compatibility shim.

All logic has moved to the ingestion/ package.
Import directly from there for new code:

    from ingestion import OverturePipeline, load_streetview_perception
    from ingestion.s3_source import s3_buildings
    from ingestion.bq_source import setup_bigquery_client
    from duckdb_utils import setup_duckdb_with_spatial
"""

from ingestion.pipeline import (
    OverturePipeline,
    BBOX_DEFAULT,
    _DEFAULT_DB_PATH,
    ALL_LAYERS,
    PLACE_NAME,
    main,
)
from ingestion.perception import load_streetview_perception
from ingestion.bq_source import setup_bigquery_client
from duckdb_utils import setup_duckdb_with_spatial

__all__ = [
    "OverturePipeline",
    "load_streetview_perception",
    "setup_bigquery_client",
    "setup_duckdb_with_spatial",
    "BBOX_DEFAULT",
    "_DEFAULT_DB_PATH",
    "ALL_LAYERS",
    "PLACE_NAME",
]

if __name__ == "__main__":
    main()
