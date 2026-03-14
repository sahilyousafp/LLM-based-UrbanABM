"""
BlockDispatcher — routes each simulation step to the appropriate Blocks.
Priority-based dispatch (no LLM cost at dispatch level):
  - NeedsBlock: always runs (cheap — rule-based with optional LLM at amenities)
  - CognitionBlock: always runs (cheap between intervals; LLM only every 10 steps)
  - MobilityBlock: runs if agent needs to select next edge (guarded by LLM_CALLS_PER_STEP)
"""
import logging
import os
from dataclasses import dataclass
from typing import Optional

from .block import BlockResult
from .blocks.mobility_block import MobilityBlock
from .blocks.needs_block import NeedsBlock
from .blocks.cognition_block import CognitionBlock
from LLM.llm_client import LLMClient
from Memory.memory import Memory

logger = logging.getLogger(__name__)

# Read from env: max number of agents that call LLM for mobility per simulation step.
# Remaining agents fall back to rule-based movement. 0 = all agents use rules.
LLM_CALLS_PER_STEP = int(os.environ.get("LLM_CALLS_PER_STEP", "50"))

# Global counter per step (reset externally by the model before each step)
_step_llm_call_count = 0
_current_tracked_step = -1


def reset_step_counter(step: int) -> None:
    """Called by CityModel before each step to reset the per-step LLM budget."""
    global _step_llm_call_count, _current_tracked_step
    _step_llm_call_count = 0
    _current_tracked_step = step


def _can_use_llm_for_mobility(step: int) -> bool:
    """Check if this agent can use LLM for mobility (within per-step budget)."""
    global _step_llm_call_count, _current_tracked_step
    if _current_tracked_step != step:
        reset_step_counter(step)
    if LLM_CALLS_PER_STEP <= 0:
        return False
    if _step_llm_call_count < LLM_CALLS_PER_STEP:
        _step_llm_call_count += 1
        return True
    return False


@dataclass
class StepResult:
    """Aggregated results from all blocks for one agent step."""
    needs: BlockResult
    cognition: BlockResult
    mobility: BlockResult


class BlockDispatcher:
    """
    Coordinates the execution of Blocks for a single agent per simulation step.
    Instantiated once per agent (holds references to blocks).
    """

    def __init__(self, llm_client: LLMClient, memory: Memory, context: Optional[dict] = None):
        self.llm = llm_client
        self.memory = memory
        ctx = context or {}
        self.needs_block = NeedsBlock(llm_client, memory, ctx)
        self.cognition_block = CognitionBlock(llm_client, memory, ctx)
        self.mobility_block = MobilityBlock(llm_client, memory, ctx)

    async def run(
        self,
        step: int,
        candidate_edges: list[dict],
        nearby_amenities: Optional[list] = None,
        street_perception: Optional[dict] = None,
        needs_new_edge: bool = True,
    ) -> StepResult:
        """
        Run all blocks for one simulation step.

        Args:
            step: current simulation step number
            candidate_edges: edges reachable from agent's current position
            nearby_amenities: POIs within range of current position
            street_perception: VLM-analysed street environment at current location
        """
        # 1. NeedsBlock — always runs (cheap)
        needs_result = await self.needs_block.run(
            step=step, nearby_amenities=nearby_amenities
        )

        # 2. CognitionBlock — always runs (cheap between intervals)
        cognition_result = await self.cognition_block.run(
            step=step, street_perception=street_perception
        )

        # 3. MobilityBlock — LLM only if within per-step budget AND we need a new edge
        if needs_new_edge:
            use_llm = _can_use_llm_for_mobility(step)
            if use_llm:
                mobility_result = await self.mobility_block.run(
                    step=step, candidate_edges=candidate_edges,
                    street_perception=street_perception,
                )
            else:
                # Rule-based fallback: prefer least-visited edge
                mobility_result = await self._rule_based_mobility(step, candidate_edges)
        else:
            # We are still traversing the current edge
            mobility_result = BlockResult(action="stay", params={}, reasoning="Traversing current edge")

        return StepResult(
            needs=needs_result,
            cognition=cognition_result,
            mobility=mobility_result,
        )

    async def _rule_based_mobility(self, step: int, candidate_edges: list[dict]) -> BlockResult:
        """Fallback: choose least-visited edge without LLM."""
        if not candidate_edges:
            return BlockResult(action="stay", params={}, reasoning="No candidates", fallback=True)

        visit_counts = await self.memory.status.get("visited_edges", {})
        sorted_edges = sorted(candidate_edges, key=lambda e: visit_counts.get(str(e["edge_id"]), 0))
        chosen = sorted_edges[0]

        visit_counts[str(chosen["edge_id"])] = visit_counts.get(str(chosen["edge_id"]), 0) + 1
        await self.memory.status.update("visited_edges", visit_counts)
        await self.memory.status.update("current_plan", {"goal": "explore", "target_edge_id": chosen["edge_id"]})

        return BlockResult(
            action="move_to_edge",
            params={
                "edge_id": chosen["edge_id"],
                "direction": chosen.get("direction", "forward"),
                "geom": chosen.get("geom"),
            },
            reasoning="Rule-based: least-visited edge (LLM budget exhausted)",
            fallback=True,
        )
