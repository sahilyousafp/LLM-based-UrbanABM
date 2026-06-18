import duckdb


def setup_duckdb_with_spatial(db_path):
    """Open DuckDB with spatial and parquet extensions loaded."""
    con = duckdb.connect(str(db_path))
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute("INSTALL parquet; LOAD parquet;")
    return con
