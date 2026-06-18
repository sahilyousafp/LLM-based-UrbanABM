"""Shared DuckDB connection accessor.

On Windows, DuckDB uses exclusive OS-level file locking, so only ONE connection
to the .duckdb file can exist at a time per process.  All read queries must go
through the single connection already held by CityModel.

get_db_connection() returns a thin wrapper around sim.city_model.con whose
.close() is a no-op — callers keep the same try/finally pattern they had
before, but we never open a second file handle.
"""
import duckdb
from paths import DB_PATH


class _SharedConProxy:
    """Forwards DuckDB calls to the shared connection; ignores close()."""

    def __init__(self, con):
        self._con = con

    def execute(self, *args, **kwargs):
        return self._con.execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        return self._con.executemany(*args, **kwargs)

    def close(self):
        pass  # shared — never close


class _FailedConProxy:
    """Returned when the DB file is locked and CityModel isn't ready yet."""

    def execute(self, *args, **kwargs):
        raise RuntimeError(
            "Database unavailable: the DuckDB file is locked by another process "
            "and CityModel has not initialised yet. Ensure RELOAD=false in .env "
            "and that no other process (agent_lab_server, etc.) holds the file."
        )

    def executemany(self, *args, **kwargs):
        self.execute()

    def close(self):
        pass


def get_db_connection() -> _SharedConProxy:
    """Return a proxy to the shared CityModel DuckDB connection.

    Falls back to opening a temporary read-only connection if the model is not
    yet initialised (e.g. during startup health-checks).
    """
    from state import sim  # local import avoids circular dependency

    con = getattr(sim.city_model, "con", None) if sim.city_model else None
    if con is not None:
        return _SharedConProxy(con)

    # Fallback: open a fresh connection (startup / model not ready).
    _backup = DB_PATH.with_suffix(".backup.duckdb")
    for _fb_fn in [
        lambda: duckdb.connect(str(DB_PATH)),
        lambda: duckdb.connect(str(DB_PATH), read_only=True),
        lambda: duckdb.connect(str(_backup)),
        lambda: duckdb.connect(str(_backup), read_only=True),
    ]:
        try:
            fallback = _fb_fn()
            fallback.install_extension("spatial")
            fallback.load_extension("spatial")
            return _SharedConProxy(fallback)
        except Exception:
            continue
    return _FailedConProxy()
