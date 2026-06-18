"""
BlockDispatcher — routes each simulation step to the appropriate Blocks.
Priority-based dispatch (no LLM cost at dispatch level):
  - NeedsBlock: always runs (cheap — rule-based with optional LLM at amenities)
  - CognitionBlock: always runs (cheap between intervals; LLM only every 10 steps)
  - PlanBlock: always runs (updates plan state, resolves targets, filters edges)
  - MobilityBlock: runs if agent needs to select next edge (always uses LLM)
"""
import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from .block import BlockResult
from .blocks.mobility_block import MobilityBlock
from .blocks.needs_block import NeedsBlock
from .blocks.cognition_block import CognitionBlock
from .blocks.plan_block import PlanBlock
from LLM.llm_client import LLMClient
from LLM.Memory.memory import Memory

logger = logging.getLogger(__name__)


@dataclass
class StepResult:
    """Aggregated results from all blocks for one agent step."""
    needs: BlockResult
    cognition: BlockResult
    plan: BlockResult
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
        self.context = ctx
        self.needs_block = NeedsBlock(llm_client, memory, ctx)
        self.cognition_block = CognitionBlock(llm_client, memory, ctx)
        self.plan_block = PlanBlock(llm_client, memory, ctx)
        self.mobility_block = MobilityBlock(llm_client, memory, ctx)

    async def run(
        self,
        step: int,
        candidate_edges: list[dict],
        nearby_amenities: Optional[list] = None,
        street_perception: Optional[dict] = None,
        needs_new_edge: bool = True,
        nearby_agents: Optional[list] = None,
        nearby_transit: Optional[list] = None,
        time_of_day: str = "",
    ) -> StepResult:
        """
        Run all blocks for one simulation step.

        Args:
            step: current simulation step number
            candidate_edges: edges reachable from agent's current position
            nearby_amenities: POIs within range of current position
            street_perception: VLM-analysed street environment at current location
            nearby_agents: other agents within ~55m (pre-step snapshot)
            nearby_transit: transit stops within ~80m from ext_transit_stops
        """
        # 1-3. Run needs, cognition, and plan in parallel — they are mutually independent.
        #      Mobility must come after because it reads the updated cognition state.
        #      return_exceptions=True: one crashed block must not kill the others.
        _results = await asyncio.gather(
            self.needs_block.run(
                step=step, nearby_amenities=nearby_amenities, street_perception=street_perception,
                nearby_agents=nearby_agents, time_of_day=time_of_day,
            ),
            self.cognition_block.run(
                step=step, street_perception=street_perception, time_of_day=time_of_day,
            ),
            self.plan_block.run(
                step=step,
                nearby_amenities=nearby_amenities,
                street_perception=street_perception,
                candidate_edges=candidate_edges,
            ),
            return_exceptions=True,
        )
        _names = ("NeedsBlock", "CognitionBlock", "PlanBlock")
        needs_result, cognition_result, plan_result = (
            self._safe_result(_results[i], _names[i], step) for i in range(3)
        )

        # 4. MobilityBlock — always uses LLM when a new edge is needed
        if needs_new_edge:
            mobility_result = await self.mobility_block.run(
                step=step, candidate_edges=candidate_edges,
                street_perception=street_perception,
                nearby_agents=nearby_agents,
                nearby_transit=nearby_transit,
                time_of_day=time_of_day,
            )
        else:
            # We are still traversing the current edge
            mobility_result = BlockResult(action="stay", params={}, reasoning="Traversing current edge")

        return StepResult(
            needs=needs_result,
            cognition=cognition_result,
            plan=plan_result,
            mobility=mobility_result,
        )

    @staticmethod
    def _safe_result(result, block_name: str, step: int) -> BlockResult:
        if isinstance(result, Exception):
            logger.error(f"{block_name} crashed at step {step}: {result}", exc_info=result)
            return BlockResult(
                action=f"{block_name.lower()}_error",
                params={},
                reasoning=f"{block_name} failed: {type(result).__name__}: {result}",
                fallback=True,
            )
        return result
