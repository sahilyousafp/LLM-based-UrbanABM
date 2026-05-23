"""
PlanBlock — manages archetype-specific daily plans with perception-driven routing.

Responsibilities:
  1. Load plan config from JSON at init (per archetype)
  2. On first run: initialise memory.status["plan"] with phases
  3. Each step: check if current phase is complete (agent reached target amenity type)
  4. Advance to next phase when complete
  5. Resolve target_types -> nearest matching amenity via DuckDB query
  6. Record encountered perception qualities at current location
   7. Apply perception_avoid as hard filter on candidate edges (rule_based mode only);
      for LLM modes, perception_avoid is passed as soft guidance via plan_context

Plan memory structure:
{
    "phases": [...],              # from JSON config
    "current_phase_index": 0,     # which phase is active
    "current_phase": {...},       # resolved phase with active_target
    "completed_phases": [],       # history of completed phase IDs
    "target_override": {...},     # user-selected target from frontend
    "encountered_qualities": [],  # perception qualities seen at each step
    "status": "active"            # active | completed | blocked
}
"""
import json
import logging
from pathlib import Path
from typing import Optional

from LLM.Thinking.block import Block, BlockResult

logger = logging.getLogger(__name__)

# Valid perception field keys that can appear in perception_preferences/perception_avoid
VALID_PERCEPTION_KEYS = {
    "scene_overview", "buildings", "materials", "building_condition",
    "street_furniture", "vegetation", "signage", "ground_surfaces",
    "spatial_enclosure", "pedestrian_activity", "lighting_atmosphere",
    "as_resident", "as_commuter", "as_tourist", "as_student",
}


def load_plans_json(path: Optional[Path] = None) -> dict:
    """Load plans configuration from JSON file."""
    if path is None:
        path = Path(__file__).parent / "plans.json"
    if not path.exists():
        logger.warning(f"Plans config not found at {path}, using empty plans")
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load plans config: {e}")
        return {}


class PlanBlock(Block):
    """Manages daily plan execution with perception-driven routing."""

    def __init__(self, llm_client, memory, context=None):
        super().__init__(llm_client, memory, context)
        plans_path = context.get("plans_path") if context else None
        self.plans_config = load_plans_json(plans_path)

    async def run(
        self,
        step: int,
        nearby_amenities: Optional[list] = None,
        street_perception: Optional[dict] = None,
        candidate_edges: Optional[list] = None,
        **kwargs,
    ) -> BlockResult:
        """
        Update plan state for this step.

        Args:
            step: current simulation step
            nearby_amenities: amenities near current position
            street_perception: perception dict at current location
            candidate_edges: edges available for movement (for filtering)
        """
        profile = await self.memory.status.get("agent_profile", {})
        archetype = profile.get("archetype", "resident")
        plan = await self.memory.status.get("plan", {})

        # Initialize plan from config on first run
        if not plan.get("phases"):
            plan = self._init_plan(archetype)
            await self.memory.status.update("plan", plan)

        if plan.get("status") == "completed":
            return BlockResult(
                action="plan_completed",
                params={"plan": plan},
                reasoning="All plan phases completed",
                fallback=False,
            )

        # Record encountered perception qualities at current location
        if street_perception:
            encountered = self._extract_qualities(street_perception)
            plan.setdefault("encountered_qualities", []).extend(encountered)

        # Check if current phase is complete
        current_phase = plan.get("current_phase")
        if current_phase:
            phase_complete = await self._check_phase_complete(
                current_phase, nearby_amenities, step
            )
            if phase_complete:
                plan["completed_phases"].append(current_phase["id"])
                plan["current_phase"] = None
                await self.memory.stream.add(
                    topic="plan",
                    step=step,
                    description=f"Completed phase: {current_phase['id']} ({current_phase['goal']})",
                    metadata={"phase_id": current_phase["id"]},
                )

        # Advance to next phase if needed
        if plan.get("current_phase") is None:
            next_phase = self._advance_phase(plan)
            if next_phase:
                plan["current_phase"] = next_phase
                # Record phase start step
                plan.setdefault("phase_start_steps", {})[next_phase["id"]] = step
                # Resolve active target from target_types
                model = self.context.get("model")
                if model and next_phase.get("target_types"):
                    position = await self.memory.status.get("position", {})
                    active_target = self._resolve_target(
                        model, position, next_phase["target_types"]
                    )
                    if active_target:
                        plan["current_phase"]["active_target"] = active_target
                await self.memory.stream.add(
                    topic="plan",
                    step=step,
                    description=f"Started phase: {next_phase['id']} ({next_phase['goal']})",
                    metadata={"phase_id": next_phase["id"]},
                )

        # Resolve active_target for pre-initialized phase if not yet set
        current_phase = plan.get("current_phase")
        if current_phase and not current_phase.get("active_target") and current_phase.get("target_types"):
            model = self.context.get("model")
            if model:
                position = await self.memory.status.get("position", {})
                active_target = self._resolve_target(
                    model, position, current_phase["target_types"]
                )
                if active_target:
                    current_phase["active_target"] = active_target

        # Apply perception_avoid hard filter only for rule_based mode
        # LLM modes: perception_avoid is passed as soft guidance via plan_context
        perception_mode = getattr(self.context.get("model"), "perception_mode", "both")
        if (candidate_edges and plan.get("current_phase")
                and perception_mode == "rule_based"):
            avoid_list = plan["current_phase"].get("perception_avoid", [])
            if avoid_list:
                filtered = self._filter_edges(candidate_edges, avoid_list)
                if len(filtered) < len(candidate_edges):
                    plan["current_phase"]["forced_deviation"] = len(filtered) == 0
                    plan["current_phase"]["deviation_reason"] = (
                        f"Filtered {len(candidate_edges) - len(filtered)} edges with avoided qualities"
                        if filtered
                        else "No valid edges after filtering — forced deviation"
                    )

        await self.memory.status.update("plan", plan)

        return BlockResult(
            action="plan_updated",
            params={
                "current_phase_index": plan.get("current_phase_index", 0),
                "current_phase": plan.get("current_phase"),
                "status": plan.get("status", "active"),
            },
            reasoning=f"Plan phase {plan.get('current_phase_index', 0)}: {plan.get('current_phase', {}).get('goal', 'none')}",
            fallback=False,
        )

    def _init_plan(self, archetype: str) -> dict:
        """Initialize plan from config for given archetype."""
        archetype_plan = self.plans_config.get(archetype, {})
        phases = archetype_plan.get("phases", [])
        return {
            "phases": phases,
            "current_phase_index": 0,
            "current_phase": None,
            "completed_phases": [],
            "target_override": None,
            "encountered_qualities": [],
            "phase_start_steps": {},  # phase_id -> step number when started
            "status": "active" if phases else "completed",
        }

    async def _get_phase_start_step(self, phase_id: str) -> int:
        """Get the step number when a phase started, default 0."""
        plan = await self.memory.status.get("plan", {})
        return plan.get("phase_start_steps", {}).get(phase_id, 0)

    def _advance_phase(self, plan: dict) -> Optional[dict]:
        """Move to next phase. Returns new phase or None if all done."""
        idx = plan.get("current_phase_index", 0)
        phases = plan.get("phases", [])
        idx += 1
        if idx >= len(phases):
            plan["status"] = "completed"
            return None
        plan["current_phase_index"] = idx
        return phases[idx]

    async def _check_phase_complete(
        self, phase: dict, nearby_amenities: Optional[list], step: int
    ) -> bool:
        """Check if current phase goals are met by checking visited amenities."""
        target_types = phase.get("target_types", [])
        max_visits = phase.get("max_visits", 0)
        phase_id = phase.get("id", "")

        if not target_types or max_visits == 0:
            return False

        # Check visited_amenities in memory (maintained by NeedsBlock)
        visited = await self.memory.status.get("visited_amenities", [])
        # Count visits to target-type amenities since this phase started
        phase_start = await self._get_phase_start_step(phase_id)
        target_visits = [
            v for v in visited
            if v.get("type", "").lower() in [t.lower() for t in target_types]
            and v.get("step", 0) >= phase_start
        ]
        return len(target_visits) >= max_visits

    def _resolve_target(
        self, model, position: dict, target_types: list
    ) -> Optional[dict]:
        """Find nearest amenity matching target_types."""
        try:
            lon = position.get("lon", 0.0)
            lat = position.get("lat", 0.0)
            point_geom = __import__("shapely.geometry", fromlist=["Point"]).Point(lon, lat)
            nearby = model.get_nearby_amenities(point_geom)
            for amenity in nearby:
                if amenity.get("type", "").lower() in [t.lower() for t in target_types]:
                    return {
                        "name": amenity.get("name", "Unknown"),
                        "type": amenity.get("type", ""),
                        "lon": amenity.get("lon", lon),
                        "lat": amenity.get("lat", lat),
                        "dist": amenity.get("dist", 0),
                    }
        except Exception as e:
            logger.warning(f"Failed to resolve target for types {target_types}: {e}")
        return None

    def _extract_qualities(self, perception: dict) -> list:
        """Extract non-empty perception field names as quality labels."""
        qualities = []
        for key, val in perception.items():
            if val and isinstance(val, str) and val.strip().lower() not in ("", "unknown"):
                qualities.append(key)
        return qualities

    def _filter_edges(
        self, edges: list, avoid_list: list
    ) -> list:
        """
        Filter candidate edges based on perception_avoid.
        Hard filter: remove edges that contain avoided qualities.
        If all edges are filtered out, return empty list (forced deviation).
        """
        if not avoid_list:
            return edges

        filtered = []
        for edge in edges:
            perception = edge.get("perception", "")
            has_avoided = False
            for avoid_key in avoid_list:
                if avoid_key.lower() in perception.lower():
                    has_avoided = True
                    break
            if not has_avoided:
                filtered.append(edge)

        return filtered if filtered else edges
