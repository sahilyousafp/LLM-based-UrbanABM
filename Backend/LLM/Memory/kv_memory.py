"""
KVMemory — async key-value memory store with locking.
Adapted from AgentSociety's KVMemory pattern (without vector embeddings in Phase 1).

Predefined schema keys for CityAgent:
  position            : {"lon": float, "lat": float, "edge_id": int}
  needs               : {"hunger": float, "energy": float, "social": float, "comfort": float}  (0-1)
  visited_edges       : {edge_id: visit_count}
  visited_amenities   : [{"name": str, "type": str, "lon": float, "lat": float}]
  agent_profile       : {"archetype": str, "age": int, "preferences": list[str]}
  current_plan        : {"goal": str, "target_edge_id": int | None}
  cognition_state     : {"mood": str, "curiosity": float, "fatigue": float}
  destination         : {"name": str | None, "amenity_type": str | None,
                         "lon": float | None, "lat": float | None,
                         "target_node": tuple | None}
"""
import asyncio
import copy
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Default schema for a CityAgent memory
DEFAULT_SCHEMA: dict[str, Any] = {
    "position": {"lon": 0.0, "lat": 0.0, "edge_id": None},
    "needs": {"hunger": 0.0, "energy": 1.0, "social": 0.2, "comfort": 0.7},
    "visited_edges": {},
    "visited_amenities": [],
    "agent_profile": {"archetype": "resident", "age": 30, "preferences": []},
    "current_plan": {"goal": "explore", "target_edge_id": None},
    "plan": {
        "phases": [],
        "current_phase_index": 0,
        "current_phase": None,
        "completed_phases": [],
        "target_override": None,
        "encountered_qualities": [],
        "status": "active",
    },
    "cognition_state": {"mood": "neutral", "curiosity": 0.7, "fatigue": 0.0},
    "destination": {"name": None, "amenity_type": None, "lon": None, "lat": None, "target_node": None},
    "memory_summaries": [],       # list[str] — working set of recent LLM narratives
    "memory_summary_unified": "", # str — resident-only consolidated long-term memory (never pruned)
}


class KVMemory:
    """Thread-safe async key-value store for agent state."""

    def __init__(self, schema: Optional[dict] = None):
        self._data: dict[str, Any] = copy.deepcopy(schema or DEFAULT_SCHEMA)
        self._lock = asyncio.Lock()

    async def get(self, key: str, default: Any = None) -> Any:
        """Read a value (returns deepcopy to prevent mutation)."""
        async with self._lock:
            if key not in self._data:
                return default
            return copy.deepcopy(self._data[key])

    async def update(self, key: str, value: Any, mode: str = "replace") -> None:
        """
        Write a value.
        mode="replace": overwrite entirely
        mode="merge": shallow-merge dicts, append to lists
        """
        async with self._lock:
            if mode == "merge" and key in self._data:
                existing = self._data[key]
                if isinstance(existing, dict) and isinstance(value, dict):
                    self._data[key] = {**existing, **value}
                elif isinstance(existing, list) and isinstance(value, list):
                    self._data[key] = existing + value
                else:
                    self._data[key] = value
            else:
                self._data[key] = copy.deepcopy(value)

    async def get_all(self) -> dict:
        """Return full snapshot of memory (deepcopy)."""
        async with self._lock:
            return copy.deepcopy(self._data)

    async def increment(self, key: str, subkey: str, delta: float = 1.0) -> None:
        """Increment a numeric value inside a nested dict key."""
        async with self._lock:
            if key in self._data and isinstance(self._data[key], dict):
                current = self._data[key].get(subkey, 0)
                self._data[key][subkey] = current + delta

    async def clamp(self, key: str, subkey: str, lo: float = 0.0, hi: float = 1.0) -> None:
        """Clamp a numeric subkey value to [lo, hi]."""
        async with self._lock:
            if key in self._data and isinstance(self._data[key], dict):
                val = self._data[key].get(subkey, lo)
                self._data[key][subkey] = max(lo, min(hi, val))
