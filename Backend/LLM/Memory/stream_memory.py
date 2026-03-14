"""
StreamMemory — append-only event log for agent experiences.
Adapted from AgentSociety's StreamMemory pattern.

Each event (MemoryNode) has: topic, step, description, metadata.
Supports retrieval by topic with recency ordering.
"""
import asyncio
import copy
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class MemoryNode:
    topic: str              # "mobility" | "amenity_visit" | "cognition" | "needs"
    step: int               # simulation step number
    description: str        # human-readable event description
    metadata: dict = field(default_factory=dict)   # extra structured data
    timestamp: float = field(default_factory=time.monotonic)


class StreamMemory:
    """
    Append-only time-ordered event log, partitioned by topic.
    Max capacity per topic prevents unbounded memory growth.
    """

    def __init__(self, max_per_topic: int = 100):
        self._store: dict[str, deque[MemoryNode]] = {}
        self._max = max_per_topic
        self._lock = asyncio.Lock()

    async def add(
        self,
        topic: str,
        step: int,
        description: str,
        metadata: Optional[dict] = None,
    ) -> MemoryNode:
        """Append a new event. Returns the created MemoryNode."""
        node = MemoryNode(topic=topic, step=step, description=description, metadata=metadata or {})
        async with self._lock:
            if topic not in self._store:
                self._store[topic] = deque(maxlen=self._max)
            self._store[topic].append(node)
        return node

    async def get_recent(self, topic: str, n: int = 10) -> list[MemoryNode]:
        """Return the n most-recent events for a topic (newest last)."""
        async with self._lock:
            bucket = self._store.get(topic, deque())
            items = list(bucket)
        return items[-n:]

    async def get_all_topics(self) -> list[str]:
        async with self._lock:
            return list(self._store.keys())

    async def get_recent_all(self, n: int = 20) -> list[MemoryNode]:
        """Return n most-recent events across all topics, sorted by step."""
        async with self._lock:
            all_nodes = []
            for bucket in self._store.values():
                all_nodes.extend(list(bucket))
        all_nodes.sort(key=lambda x: x.step)
        return all_nodes[-n:]

    def format_for_prompt(self, nodes: list[MemoryNode]) -> str:
        """Format a list of MemoryNodes as a compact string for LLM prompts."""
        if not nodes:
            return "No recent history."
        lines = [f"[Step {n.step}|{n.topic}] {n.description}" for n in nodes]
        return "\n".join(lines)
