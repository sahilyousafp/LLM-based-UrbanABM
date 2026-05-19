"""
Episodic spatial memory for agent cognition lab testing.
Records what the agent perceives, where it is, and what it needs at each step.
"""

from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field, asdict
from datetime import datetime


@dataclass
class DiaryEntry:
    """One episode: agent at one step, one place, with one perception."""
    step: int
    edge_id: str
    position: tuple  # (lon, lat)
    perception: Dict[str, str]  # from VLM: scene_overview, buildings, vegetation, etc.
    nearby_amenities: List[Dict[str, Any]]
    needs: Dict[str, float]  # {hunger, energy, social, comfort}
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class AdherenceEntry:
    """Path choice: did agent follow Dijkstra hint?"""
    step: int
    followed_dijkstra: bool
    chosen_edge_id: str
    dijkstra_edge_id: str


class PerceptionDiary:
    """Episodic store of agent's spatial experience."""

    def __init__(self, max_entries: int = 50):
        self.max_entries = max_entries
        self.entries: List[DiaryEntry] = []
        self.adherence_log: List[AdherenceEntry] = []
        self._visited_amenities_set = set()  # deduped (name, type) tuples
        self._visited_amenities_list: List[Dict[str, Any]] = []  # ordered, with first_seen_step

    def record(
        self,
        step: int,
        edge_id: str,
        position: tuple,
        perception: Dict[str, str],
        nearby_amenities: List[Dict[str, Any]],
        needs: Dict[str, float],
    ):
        """Record one step of the agent's journey."""
        entry = DiaryEntry(
            step=step,
            edge_id=edge_id,
            position=position,
            perception=perception,
            nearby_amenities=nearby_amenities,
            needs=needs,
        )
        self.entries.append(entry)

        # Dedup and track visited amenities
        for amenity in nearby_amenities:
            key = (amenity.get("name"), amenity.get("type"))
            if key not in self._visited_amenities_set:
                self._visited_amenities_set.add(key)
                self._visited_amenities_list.append(
                    {**amenity, "first_seen_step": step}
                )

        # Trim to max_entries (keep most recent)
        if len(self.entries) > self.max_entries:
            self.entries = self.entries[-self.max_entries :]

    def record_adherence(
        self, step: int, followed: bool, chosen_edge: str, dijkstra_edge: str
    ):
        """Record if agent followed the Dijkstra hint."""
        entry = AdherenceEntry(
            step=step,
            followed_dijkstra=followed,
            chosen_edge_id=chosen_edge,
            dijkstra_edge_id=dijkstra_edge,
        )
        self.adherence_log.append(entry)
        if len(self.adherence_log) > self.max_entries:
            self.adherence_log = self.adherence_log[-self.max_entries :]

    def get_history_prompt_text(self, last_n: int = 10) -> str:
        """Format the diary as LLM-ready text for injection into narrative prompt."""
        entries = self.entries[-last_n:]
        if not entries:
            return "No spatial history yet."

        lines = []
        for entry in entries:
            perception = entry.perception or {}
            scene = perception.get("scene_overview", "unknown scene")
            amenities_str = ", ".join([a.get("name", "") for a in entry.nearby_amenities])
            if not amenities_str:
                amenities_str = "no amenities nearby"

            comfort = entry.needs.get("comfort", 0.0)
            energy = entry.needs.get("energy", 0.0)

            lines.append(
                f"Step {entry.step} – {scene}\n"
                f"   Nearby: {amenities_str}\n"
                f"   Energy {energy:.2f}, Comfort {comfort:.2f}"
            )

        return "\n".join(lines)

    def get_visited_amenities(self) -> List[Dict[str, Any]]:
        """All distinct amenity encounters, in order of first encounter."""
        return self._visited_amenities_list.copy()

    def get_timeline(self) -> List[Dict[str, Any]]:
        """Full diary as JSON-serializable list."""
        return [asdict(e) for e in self.entries]

    def get_adherence_log(self) -> List[Dict[str, Any]]:
        """Path adherence log as JSON-serializable list."""
        return [asdict(e) for e in self.adherence_log]

    def get_adherence_stats(self) -> Dict[str, Any]:
        """Summary: % steps followed Dijkstra."""
        if not self.adherence_log:
            return {"pct_followed": 0.0, "total_steps": 0, "steps_followed": 0}

        followed = sum(1 for e in self.adherence_log if e.followed_dijkstra)
        total = len(self.adherence_log)
        return {
            "pct_followed": (followed / total * 100) if total > 0 else 0.0,
            "total_steps": total,
            "steps_followed": followed,
        }

    def get_spatial_stats(self) -> Dict[str, Any]:
        """Aggregate statistics from the diary."""
        if not self.entries:
            return {
                "unique_edges": 0,
                "avg_comfort": 0.0,
                "avg_energy": 0.0,
                "amenity_encounters": 0,
                "busy_step_pct": 0.0,
                "vegetation_rich_pct": 0.0,
            }

        # Unique edges
        edges = set(e.edge_id for e in self.entries)

        # Average needs
        comforts = [e.needs.get("comfort", 0.5) for e in self.entries]
        energies = [e.needs.get("energy", 0.5) for e in self.entries]
        avg_comfort = sum(comforts) / len(comforts) if comforts else 0.0
        avg_energy = sum(energies) / len(energies) if energies else 0.0

        # Busy vs quiet (look at pedestrian_activity)
        busy_count = 0
        vegetation_count = 0
        for e in self.entries:
            perception = e.perception or {}
            ped_activity = perception.get("pedestrian_activity", "").lower()
            if any(w in ped_activity for w in ["busy", "crowded", "bustling", "active"]):
                busy_count += 1
            veg = perception.get("vegetation", "").lower()
            if any(w in veg for w in ["abundant", "lush", "trees", "green", "planted"]):
                vegetation_count += 1

        return {
            "unique_edges": len(edges),
            "avg_comfort": avg_comfort,
            "avg_energy": avg_energy,
            "amenity_encounters": len(self._visited_amenities_list),
            "busy_step_pct": (busy_count / len(self.entries) * 100) if self.entries else 0.0,
            "vegetation_rich_pct": (
                (vegetation_count / len(self.entries) * 100) if self.entries else 0.0
            ),
        }

    def get_memory_audit(self) -> Dict[str, Any]:
        """Full audit of what the diary holds."""
        return {
            "diary_entries": len(self.entries),
            "visited_amenities": self._visited_amenities_list,
            "adherence_log_length": len(self.adherence_log),
            "stats": self.get_spatial_stats(),
            "adherence": self.get_adherence_stats(),
        }
