"""
Block base class — the fundamental unit of agent reasoning.
Adapted from AgentSociety's Block pattern (without Ray/async toolbox complexity).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ..LLM.llm_client import LLMClient
    from ..Memory.memory import Memory


@dataclass
class BlockResult:
    """Standardised return type from any Block.run()."""
    action: str                        # e.g. "move_to_edge", "visit_amenity", "update_cognition"
    params: dict = field(default_factory=dict)   # action-specific parameters
    reasoning: str = ""                # LLM explanation (for logging/debugging)
    fallback: bool = False             # True if LLM failed and we used rule-based fallback


class Block:
    """
    Base class for all agent thinking blocks.
    Each subclass implements run() which may call the LLM and/or read/write memory.
    """

    def __init__(self, llm_client: "LLMClient", memory: "Memory", context: Optional[dict] = None):
        self.llm = llm_client
        self.memory = memory
        self.context = context or {}    # Shared model-level context (edge graph, etc.)

    async def run(self, **kwargs) -> BlockResult:
        """Override in subclasses. Returns a BlockResult."""
        raise NotImplementedError
