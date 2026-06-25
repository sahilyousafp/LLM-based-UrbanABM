"""Global simulation state — single source of truth for all mutable singletons.

Routers import `sim` from this module instead of using `global` keywords.
"""
import os


class SimState:
    def __init__(self):
        self.city_model = None           # CityModel instance (set at startup)
        self.initial_num_agents: int = int(os.getenv("NUM_AGENTS", 50))
        self.initial_spawn_seed = None   # set from env at startup

        # Background job registries — keyed by job_id
        self.sv_jobs: dict = {}          # streetview download jobs
        self.analyze_jobs: dict = {}     # VLM analysis jobs
        self.overture_jobs: dict = {}    # Overture pipeline jobs
        self.ext_jobs: dict = {}         # external plugin jobs
        self.benchmark_jobs: dict = {}   # EQ-Bench benchmark jobs

        self._current_zone_bbox: list | None = None  # [west, south, east, north]

    def set_zone_bbox(self, bbox: list) -> None:
        self._current_zone_bbox = bbox

    def get_zone_bbox(self) -> list | None:
        return self._current_zone_bbox

    def reset_model(self, **kwargs):
        """Rebuild CityModel and replace the singleton atomically."""
        old = self.city_model
        if old is not None:
            for attr in ("con", "perception_con"):
                c = getattr(old, attr, None)
                if c is not None:
                    try:
                        c.close()
                    except Exception:
                        pass
        from model import CityModel
        self.city_model = CityModel(**kwargs)


sim = SimState()
