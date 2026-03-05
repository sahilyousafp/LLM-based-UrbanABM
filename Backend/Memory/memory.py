"""
Memory — composite facade combining KVMemory (status) and StreamMemory (stream).
Provides a unified interface for agent memory, mirroring AgentSociety's Memory class.
"""
from .kv_memory import KVMemory, DEFAULT_SCHEMA
from .stream_memory import StreamMemory


class Memory:
    """
    Composite agent memory.
      memory.status  → KVMemory  (structured current state)
      memory.stream  → StreamMemory (time-ordered event log)
    """

    def __init__(self, agent_id: int, schema: dict | None = None, max_stream_per_topic: int = 100):
        self.agent_id = agent_id
        self.status = KVMemory(schema=schema)
        self.stream = StreamMemory(max_per_topic=max_stream_per_topic)

    async def snapshot(self) -> dict:
        """Return a full snapshot of both memory stores for serialization."""
        status_data = await self.status.get_all()
        stream_topics = await self.stream.get_all_topics()
        stream_data = {}
        for topic in stream_topics:
            nodes = await self.stream.get_recent(topic, n=20)
            stream_data[topic] = [
                {"step": n.step, "description": n.description, "metadata": n.metadata}
                for n in nodes
            ]
        return {"agent_id": self.agent_id, "status": status_data, "stream": stream_data}
