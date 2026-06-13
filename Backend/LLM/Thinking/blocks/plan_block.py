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
import asyncio
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
    """Load plans configuration from JSON file.

    Prefers a ``<stem>.local.json`` sibling when present so user edits made
    through the frontend (POST /api/profiles) override the shipped defaults
    without touching the canonical file.
    """
    if path is None:
        # plans.json lives in the parent Thinking/ directory, not inside blocks/
        path = Path(__file__).parent.parent / "plans.json"
    local_path = path.with_name(f"{path.stem}.local.json")
    chosen = local_path if local_path.exists() else path
    if not chosen.exists():
        logger.warning(f"Plans config not found at {chosen}, using empty plans")
        return {}
    try:
        with open(chosen, "r", encoding="utf-8") as f:
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
        needs = await self.memory.status.get("needs", {})
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
            encountered_list = plan.setdefault("encountered_qualities", [])
            encountered_list.extend(encountered)
            if len(encountered_list) > 200:
                encountered_list[:] = encountered_list[-200:]

        # --- En-route stop: check completion ---
        # If an en_route_stop is active, check whether the stop goal is met first.
        # On completion, restore the interrupted main phase.
        if plan.get("en_route_stop_active") and plan.get("current_phase"):
            stop_complete = await self._check_phase_complete(
                plan["current_phase"], nearby_amenities, step
            )
            if stop_complete:
                completed_stop_id = plan["current_phase"]["id"]
                plan["completed_phases"].append(completed_stop_id)
                await self.memory.stream.add(
                    topic="plan", step=step,
                    description=f"Completed en-route stop: {completed_stop_id} ({plan['current_phase']['goal']})",
                    metadata={"stop_id": completed_stop_id},
                )
                plan["current_phase"] = plan.get("interrupted_phase")
                plan["interrupted_phase"] = None
                plan["en_route_stop_active"] = False

        # --- Main phase: check completion (only when no en_route_stop running) ---
        if not plan.get("en_route_stop_active"):
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
                    plan.setdefault("phase_start_steps", {})[next_phase["id"]] = step
                    model = self.context.get("model")
                    if model and next_phase.get("target_types"):
                        position = await self.memory.status.get("position", {})
                        active_target = await self._resolve_target(
                            model, position, next_phase["target_types"]
                        )
                        if active_target:
                            plan["current_phase"]["active_target"] = active_target
                            await self._sync_plan_target_to_destination(active_target)
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
                active_target = await self._resolve_target(
                    model, position, current_phase["target_types"]
                )
                if active_target:
                    current_phase["active_target"] = active_target
                    await self._sync_plan_target_to_destination(active_target)

        # --- En-route stop: check triggers (only when main phase is active, no stop running) ---
        if plan.get("current_phase") and not plan.get("en_route_stop_active"):
            await self._check_and_trigger_en_route_stop(plan, needs, step)

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

        current_phase = plan.get("current_phase", {}) or {}
        return BlockResult(
            action="plan_updated",
            params={
                "current_phase_index": plan.get("current_phase_index", 0),
                "current_phase": plan.get("current_phase"),
                "en_route_stop_active": plan.get("en_route_stop_active", False),
                "status": plan.get("status", "active"),
            },
            reasoning=f"Plan phase {plan.get('current_phase_index', 0)}: {current_phase.get('goal', 'none')}",
            fallback=False,
        )

    def _init_plan(self, archetype: str) -> dict:
        """Initialize plan from config for given archetype.

        Supports both legacy {"phases": [...]} format and new
        {"profile": {...}, "daily_plan": [...]} format.
        """
        archetype_plan = self.plans_config.get(archetype, {})
        if "daily_plan" in archetype_plan:
            phases = archetype_plan["daily_plan"]
            plan_profile = archetype_plan.get("profile", {})
        else:
            phases = archetype_plan.get("phases", [])
            plan_profile = {}
        return {
            "phases": phases,
            "profile": plan_profile,
            "current_phase_index": 0,
            "current_phase": None,
            "completed_phases": [],
            "interrupted_phase": None,
            "en_route_stop_active": False,
            "target_override": None,
            "encountered_qualities": [],
            "phase_start_steps": {},
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

    async def _resolve_target(
        self, model, position: dict, target_types: list
    ) -> Optional[dict]:
        """Find nearest amenity matching target_types.
        First searches nearby radius; if none found, queries the full DB.
        """
        try:
            lon = position.get("lon", 0.0)
            lat = position.get("lat", 0.0)
            from shapely.geometry import Point
            point_geom = Point(lon, lat)
            types_lower = [t.lower() for t in target_types]

            # 1. Check nearby amenities first (fast, in-memory)
            nearby = model.get_nearby_amenities(point_geom)
            for amenity in nearby:
                if amenity.get("type", "").lower() in types_lower:
                    return {
                        "name": amenity.get("name", "Unknown"),
                        "type": amenity.get("type", ""),
                        "lon": amenity.get("lon", lon),
                        "lat": amenity.get("lat", lat),
                        "dist": amenity.get("dist", 0),
                    }

            # 2. Fall back to full DB query if model has a DuckDB connection
            if hasattr(model, "con"):
                types_sql = ", ".join(f"'{t}'" for t in types_lower)
                query = f"""
                    SELECT name, amenity, ST_X(geometry) as lon, ST_Y(geometry) as lat,
                        ST_Distance(geometry, ST_GeomFromText('POINT ({lon} {lat})')) AS dist
                    FROM amenities
                    WHERE LOWER(amenity) IN ({types_sql})
                    ORDER BY dist
                    LIMIT 1
                """
                row = await asyncio.to_thread(model.con.execute, query)
                row = row.fetchone()
                if row:
                    return {
                        "name": row[0] or "Unknown",
                        "type": row[1] or target_types[0],
                        "lon": row[2],
                        "lat": row[3],
                        "dist": row[4],
                    }
        except Exception as e:
            logger.warning(f"Failed to resolve target for types {target_types}: {e}")
        return None

    async def _sync_plan_target_to_destination(self, active_target: dict) -> None:
        """Write the plan's active_target into destination memory so Dijkstra routes to it.

        Does not override destinations explicitly set by the user (source == "user_configured")
        so the test lab's configured target is always respected.
        """
        try:
            lon, lat = active_target.get("lon", 0.0), active_target.get("lat", 0.0)
            model = self.context.get("model")

            existing = await self.memory.status.get("destination", {}) or {}
            if existing.get("source") == "user_configured":
                return

            # Find the nearest reachable network node (main connected component)
            target_node = None
            if model:
                target_node = model._find_nearest_node(lon, lat)

            await self.memory.status.update("destination", {
                **existing,
                "name": active_target.get("name", "Plan target"),
                "amenity_type": active_target.get("type", ""),
                "lon": lon,
                "lat": lat,
                "target_node": target_node,
                "source": "plan",
                "visited": False,
            })
        except Exception as e:
            logger.warning(f"Failed to sync plan target to destination: {e}")

    async def _check_and_trigger_en_route_stop(
        self, plan: dict, needs: dict, step: int
    ) -> None:
        """Check if any en_route_stop trigger fires for the current main phase.

        Skips triggering if the destination is user-configured — the stop's
        _sync_plan_target_to_destination would be blocked by the guard anyway,
        leaving the agent with no valid Dijkstra target for the stop.
        """
        destination = await self.memory.status.get("destination", {}) or {}
        if destination.get("source") == "user_configured":
            return

        current_phase = plan.get("current_phase", {})
        stops = current_phase.get("en_route_stops", [])
        if not stops:
            return

        visited = await self.memory.status.get("visited_amenities", [])
        phase_start = plan.get("phase_start_steps", {}).get(current_phase.get("id", ""), 0)

        for stop in stops:
            trigger = stop.get("trigger", {})
            need_key = trigger.get("need")
            threshold = trigger.get("threshold", 1.0)
            if not need_key or needs.get(need_key, 0) < threshold:
                continue

            # Skip if already satisfied this stop during the current phase
            stop_types = [t.lower() for t in stop.get("target_types", [])]
            already_done = any(
                v.get("type", "").lower() in stop_types and v.get("step", 0) >= phase_start
                for v in visited
            )
            if already_done:
                continue

            # Build the stop as an ephemeral phase dict
            stop_phase = {
                "id": stop.get("id", f"stop_{step}"),
                "goal": stop.get("goal", "en-route stop"),
                "target_types": stop.get("target_types", []),
                "max_visits": stop.get("max_visits", 1),
                "priority": "medium",
                "perception_preferences": [],
                "perception_avoid": [],
                "en_route_stops": [],
            }

            plan["interrupted_phase"] = plan["current_phase"]
            plan["current_phase"] = stop_phase
            plan["en_route_stop_active"] = True
            plan.setdefault("phase_start_steps", {})[stop_phase["id"]] = step

            # Resolve and sync target for the stop
            model = self.context.get("model")
            if model and stop_phase["target_types"]:
                position = await self.memory.status.get("position", {})
                active_target = await self._resolve_target(model, position, stop_phase["target_types"])
                if active_target:
                    plan["current_phase"]["active_target"] = active_target
                    await self._sync_plan_target_to_destination(active_target)

            await self.memory.stream.add(
                topic="plan", step=step,
                description=(
                    f"En-route stop triggered: {stop_phase['id']} — {stop['goal']} "
                    f"({need_key}={needs.get(need_key, 0):.2f} ≥ {threshold})"
                ),
                metadata={"stop_id": stop_phase["id"]},
            )
            break  # only one stop at a time

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
        Each avoid entry is either:
          - a plain string  → substring match against edge["perception"] text
          - {"field": str, "value": str} → field-scoped match; uses
            edge["perception_dict"] when available, else falls back to text
        Hard filter: remove edges that match any avoid entry.
        If all edges are filtered out, return empty list (forced deviation).
        """
        if not avoid_list:
            return edges

        filtered = []
        for edge in edges:
            perception_str = edge.get("perception", "")
            perception_dict = edge.get("perception_dict", {})
            has_avoided = False
            for item in avoid_list:
                if isinstance(item, dict):
                    field = item.get("field", "")
                    value = item.get("value", "")
                    if perception_dict and field in perception_dict:
                        if value.lower() in str(perception_dict[field]).lower():
                            has_avoided = True
                            break
                    elif value.lower() in perception_str.lower():
                        has_avoided = True
                        break
                else:
                    if str(item).lower() in perception_str.lower():
                        has_avoided = True
                        break
            if not has_avoided:
                filtered.append(edge)

        return filtered if filtered else edges
