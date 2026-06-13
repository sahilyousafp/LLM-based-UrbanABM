"""
CognitionBlock — updates agent attitudes and emotional state periodically.
Runs every N steps to avoid excessive LLM calls.
Adapted from AgentSociety's CognitionBlock pattern.
"""
import logging

from LLM.Thinking.block import Block, BlockResult
from LLM.Thinking.prompts import (
    cognition_update_prompt,
    memory_summary_prompt,
    memory_consolidation_prompt,
)

logger = logging.getLogger(__name__)

COGNITION_INTERVAL = 10   # Run LLM cognition update every this many steps

ARCHETYPE_MEMORY_CONFIG: dict = {
    "tourist":  {"interval": 30, "max_summaries": 1,
                 "focus": "places seen, sights encountered, and fleeting impressions"},
    "commuter": {"interval": 45, "max_summaries": 2,
                 "focus": "routes taken, efficiency patterns, and time-of-day habits"},
    "student":  {"interval": 45, "max_summaries": 3,
                 "focus": "social interactions, study spots visited, and local discoveries"},
    "resident": {"interval": 60, "max_summaries": 5,
                 "focus": "neighbourhood familiarity, routine patterns, and known places"},
}


class CognitionBlock(Block):
    """Updates agent's mood, curiosity, and fatigue based on recent experiences."""

    async def run(self, step: int, **kwargs) -> BlockResult:
        """Only performs an LLM update every COGNITION_INTERVAL steps."""
        if step % COGNITION_INTERVAL != 0:
            # Lightweight rule-based update between LLM calls
            await self._rule_based_update()
            return BlockResult(
                action="cognition_skipped",
                params={},
                reasoning=f"Cognition runs every {COGNITION_INTERVAL} steps",
                fallback=True,
            )

        street_perception = kwargs.get("street_perception")
        time_of_day = kwargs.get("time_of_day", "")

        profile = await self.memory.status.get("agent_profile", {})
        archetype = profile.get("archetype", "resident")
        current_cognition = await self.memory.status.get("cognition_state", {})
        current_needs = await self.memory.status.get("needs", {})

        # Get recent experiences across all topics for context
        recent_all = await self.memory.stream.get_recent_all(n=15)
        history_text = self.memory.stream.format_for_prompt(recent_all)

        # Build memory context from summaries
        memory_summaries = await self.memory.status.get("memory_summaries", [])
        memory_unified = await self.memory.status.get("memory_summary_unified", "")
        mem_parts = []
        if memory_unified:
            mem_parts.append(f"[Long-term memory]\n{memory_unified}")
        if memory_summaries:
            mem_parts.append("[Recent memory]\n" + "\n---\n".join(memory_summaries))
        memory_context = "\n\n".join(mem_parts)

        # Compose scene description from text fields for the LLM prompt
        perception_text = ""
        if street_perception:
            scene_fields = [
                ("scene_overview",      "Scene"),
                ("buildings",           "Buildings"),
                ("vegetation",          "Vegetation"),
                ("pedestrian_activity", "Pedestrian activity"),
                ("lighting_atmosphere", "Lighting/atmosphere"),
                ("as_resident",         "Resident perspective"),
                ("as_tourist",          "Tourist perspective"),
            ]
            lines = []
            for key, label in scene_fields:
                val = street_perception.get(key, "")
                if val and val.strip().lower() != "unknown":
                    lines.append(f"  {label}: {val}")
            if lines:
                perception_text = "\n".join(lines)

        messages = cognition_update_prompt(
            archetype=archetype,
            current_cognition=current_cognition,
            current_needs=current_needs,
            recent_history=history_text,
            step=step,
            streetview_perception=perception_text,
            memory_context=memory_context,
            time_of_day=time_of_day,
        )

        response = await self.llm.chat_json(messages)
        fallback = False

        if response and "mood" in response:
            new_cognition = {
                "mood": response.get("mood", current_cognition.get("mood", "neutral")),
                "curiosity": max(0.0, min(1.0, float(response.get("curiosity", 0.7)))),
                "fatigue": max(0.0, min(1.0, float(response.get("fatigue", 0.0)))),
            }
            summary = response.get("summary", "")
        else:
            # Fallback: simple rule-based update
            new_cognition = current_cognition.copy()
            new_cognition["fatigue"] = min(1.0, current_cognition.get("fatigue", 0.0) + 0.05)
            summary = "Cognition updated by rules (LLM unavailable)"
            fallback = True

        await self.memory.status.update("cognition_state", new_cognition)

        # Log to stream
        await self.memory.stream.add(
            topic="cognition",
            step=step,
            description=summary or f"Mood: {new_cognition['mood']}, "
                        f"curiosity: {new_cognition['curiosity']:.2f}, "
                        f"fatigue: {new_cognition['fatigue']:.2f}",
            metadata={"cognition": new_cognition, "llm_used": not fallback},
        )

        # Memory summary — archetype-specific interval
        mem_cfg = ARCHETYPE_MEMORY_CONFIG.get(archetype, ARCHETYPE_MEMORY_CONFIG["resident"])
        if step > 0 and step % mem_cfg["interval"] == 0:
            try:
                await self._update_memory_summary(archetype, step, mem_cfg)
            except Exception as exc:
                logger.warning("Memory summary failed at step %d: %s", step, exc)

        return BlockResult(
            action="cognition_updated",
            params={"cognition_state": new_cognition},
            reasoning=summary,
            fallback=fallback,
        )

    async def _rule_based_update(self) -> None:
        """Lightweight fatigue accumulation between LLM calls."""
        cog = await self.memory.status.get("cognition_state", {})
        cog["fatigue"] = min(1.0, cog.get("fatigue", 0.0) + 0.002)
        # Fatigue reduces curiosity slightly
        cog["curiosity"] = max(0.1, cog.get("curiosity", 0.7) - cog["fatigue"] * 0.002)
        await self.memory.status.update("cognition_state", cog)

    async def _update_memory_summary(self, archetype: str, step: int, cfg: dict) -> None:
        """Generate a narrative memory summary and store it per archetype policy."""
        recent_all = await self.memory.stream.get_recent_all(n=30)
        events_text = self.memory.stream.format_for_prompt(recent_all)
        existing = await self.memory.status.get("memory_summaries", [])

        messages = memory_summary_prompt(
            archetype=archetype,
            recent_events_text=events_text,
            previous_summaries=existing,
            focus=cfg["focus"],
        )
        response = await self.llm.chat_json(messages)
        new_summary = response.get("summary", "") if response else ""
        if not new_summary:
            return

        updated = existing + [f"[Step {step}] {new_summary}"]

        if archetype == "resident" and len(updated) > cfg["max_summaries"]:
            # Consolidate all working summaries into the unified long-term memory
            existing_unified = await self.memory.status.get("memory_summary_unified", "")
            cons_messages = memory_consolidation_prompt(
                summaries_to_merge=updated,
                existing_unified=existing_unified,
            )
            cons_response = await self.llm.chat_json(cons_messages)
            new_unified = cons_response.get("unified_summary", "") if cons_response else ""
            if new_unified:
                await self.memory.status.update("memory_summary_unified", new_unified[:2000])
            await self.memory.status.update("memory_summaries", [])
            await self.memory.stream.add(
                topic="cognition", step=step,
                description=f"Long-term memory consolidated: {new_unified[:80]}…",
                metadata={"memory_consolidated": True},
            )
        else:
            # Tourist: always replace (max=1); commuter/student: prune oldest
            updated = updated[-cfg["max_summaries"]:]
            await self.memory.status.update("memory_summaries", updated)
            await self.memory.stream.add(
                topic="cognition", step=step,
                description=f"Memory updated: {new_summary[:80]}…",
                metadata={"memory_summary": True},
            )
