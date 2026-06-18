# 03 — Memory System

Every agent maintains two independent memory stores. Understanding them is essential before reading the decision blocks, which read from and write to memory constantly.

**Key files:**
- `Backend/LLM/Memory/kv_memory.py`
- `Backend/LLM/Memory/stream_memory.py`
- `Backend/LLM/Memory/memory.py` — the facade that combines both

---

## Two-Layer Design

```
Memory (facade)
├── status: KVMemory       ← structured, keyed, mutable state
│                             "What does the agent know right now?"
└── stream: StreamMemory   ← append-only event log
                              "What has the agent experienced?"
```

**KVMemory** is like a JSON document: you read and write named keys. It holds the agent's current position, needs, emotional state, destination, and plan.

**StreamMemory** is like a diary: events are appended by topic. LLM prompts pull the last N events from the log as context.

---

## KVMemory — Full Schema

Defined in `DEFAULT_SCHEMA` (`kv_memory.py`). Every key is initialised to its default before use.

```python
{
    # Spatial state
    "position": {
        "lon": float,           # current WGS84 longitude
        "lat": float,           # current WGS84 latitude
        "edge_id": int,         # current walk_edge id
        "current_node": tuple,  # (lon, lat) of edge end node
    },

    # Needs (0.0 = fully satisfied / low, 1.0 = urgent / high)
    "needs": {
        "hunger":  float,       # 0 = full, 1 = starving
        "energy":  float,       # 0 = exhausted, 1 = energised
        "social":  float,       # 0 = satisfied, 1 = lonely
        "comfort": float,       # 0 = uncomfortable, 1 = comfortable
    },

    # Exploration history
    "visited_edges":     dict,  # {edge_id_str: visit_count}
    "visited_amenities": list,  # [{"name", "type", "lon", "lat"}, ...]

    # Agent identity
    "agent_profile": {
        "archetype":   str,     # "resident"|"commuter"|"tourist"|"student"
        "age":         int,
        "gender":      str,
        "preferences": list,    # archetype-specific preference strings
    },

    # Emotional state (updated by CognitionBlock every 10 steps)
    "cognition_state": {
        "mood":      str,       # "happy"|"neutral"|"tired"|"curious"|
                                # "bored"|"energised"|"social"|"focused"
        "curiosity": float,     # 0.0–1.0
        "fatigue":   float,     # 0.0–1.0
    },

    # Navigation
    "destination": {
        "name":        str,     # e.g. "Café de l'Acadèmia"
        "amenity_type": str,    # e.g. "cafe"
        "lon":         float,
        "lat":         float,
        "target_node": tuple,   # nearest walk_node to destination
        "visited":     bool,    # True once agent has arrived
        "source":      str,     # "plan"|"user_configured"
    },

    # Daily plan (managed by PlanBlock)
    "plan": {
        "phases":             list,   # all plan phases for this archetype
        "current_phase_index": int,
        "current_phase":      dict,   # active phase dict
        "completed_phases":   list,
        "target_override":    None,
        "status": str,               # "active"|"completed"|"blocked"
    },

    # Memory consolidation (managed by CognitionBlock)
    "memory_summaries":        list,  # working set of recent episode summaries
    "memory_summary_unified":  str,   # residents only: never-pruned long-term memory

    # Navigation path (Dijkstra)
    "proposed_path": {
        "nodes":          list,   # ordered (lon,lat) node sequence
        "total_distance": float,  # metres
        "created_at_step": int,
    },

    # Internal counters
    "explore_steps":          int,   # steps taken on current free-explore window
    "_last_scene_key":        str,   # dedup key for perception logging
    "satisfaction_source":    str,   # "none"|"amenity"|"visual"|"crowd"
    "satisfaction_reasoning": str,
}
```

### KVMemory Interface

```python
# Read (returns deep copy — safe to mutate)
value = await memory.status.get("needs", {})

# Write (replaces entire value)
await memory.status.update("cognition_state", {"mood": "curious", ...})

# Merge (deep merge into existing dict)
await memory.status.update("needs", {"hunger": 0.8}, mode="merge")

# Numeric increment
await memory.status.increment("needs", "hunger", delta=0.003)

# Clamp a float to range
await memory.status.clamp("needs", "energy", lo=0.0, hi=1.0)
```

All operations use per-key asyncio locks — safe for concurrent block access.

---

## StreamMemory — Event Log

### MemoryNode

```python
@dataclass
class MemoryNode:
    topic:       str    # which aspect of experience
    step:        int    # simulation step number
    description: str    # human-readable (goes into LLM prompts)
    metadata:    dict   # structured data for analysis
    timestamp:   float  # monotonic (for ordering within a step)
```

### Topics

| Topic | Written by | Content |
|-------|-----------|---------|
| `mobility` | MobilityBlock | Edge chosen, reasoning, on_path flag, fallback flag |
| `amenity_visit` | NeedsBlock | Amenity name/type visited, need deltas |
| `perception` | NeedsBlock | Scene description at current location |
| `needs` | NeedsBlock | Periodic need state snapshot |
| `cognition` | CognitionBlock | Mood summary, fatigue/curiosity values |

### Interface

```python
# Append an event
await memory.stream.add(
    topic="mobility",
    step=step,
    description="Moved to Carrer de Provença toward Passeig de Gràcia",
    metadata={"edge_id": 1234, "fallback": False, "on_path": True}
)

# Get last N events from one topic
recent_moves = await memory.stream.get_recent("mobility", n=5)

# Get last N events across ALL topics (for cognition prompts)
all_recent = await memory.stream.get_recent_all(n=15)

# Format for LLM prompt (compact string)
history_text = memory.stream.format_for_prompt(recent_moves)
# → "Step 42 [mobility]: Moved to edge 1234. Reason: interesting café nearby."
```

Each topic has a fixed-size deque (FIFO overflow). This caps stream memory at ~100 entries without manual pruning.

---

## Memory Consolidation

`CognitionBlock` periodically compresses the stream into text summaries — this prevents LLM prompts from growing unbounded and gives agents a sense of **episodic memory**.

### Archetype-Specific Intervals

| Archetype | Summary interval | Max summaries | Focus |
|-----------|-----------------|---------------|-------|
| Tourist | 30 steps | 1 | Places seen, sights, fleeting impressions |
| Commuter | 45 steps | 2 | Routes, efficiency patterns, time-of-day habits |
| Student | 45 steps | 3 | Social interactions, study spots, local discoveries |
| Resident | 60 steps | 5 | Neighbourhood familiarity, routine patterns, known places |

**Residents** additionally maintain a `memory_summary_unified` — a long-term narrative that is never pruned, only appended-to and rewritten. It captures the agent's developing relationship with their neighbourhood over hundreds of steps.

### Memory Flow

```
StreamMemory events (last 15)
        │
        │  LLM: memory_summary_prompt()
        ▼
memory_summaries list (working set, max N per archetype)
        │
        │  LLM: memory_consolidation_prompt()   ← residents only
        ▼
memory_summary_unified (never pruned)
```

Both summaries are injected into mobility and cognition prompts via `memory_context` parameter.

---

## How Memory Flows Through Decision Blocks

```
                    KVMemory (status)
                         │
         ┌───────────────┼───────────────┐
         │               │               │
   NeedsBlock     CognitionBlock   MobilityBlock
   reads: needs   reads: cognition  reads: position
   writes: needs  writes: cognition         needs
                                           cognition
                                           destination
                                           visited_edges
                                           explore_steps
                         │               │
                         └───────────────┘
                    StreamMemory (stream)
                    (all blocks write events)
```

---

**Next:** [`04_decision_pipeline.md`](04_decision_pipeline.md) — the four thinking blocks and how they execute each step.
