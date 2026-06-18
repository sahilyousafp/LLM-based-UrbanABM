# 02 — Simulation Core

This document covers the Mesa ABM model structure — how `CityModel` and `CityAgent` are built, how the step loop works, and how agents move along street edges.

**Key files:**
- `Backend/Agent/model/city_model.py` — `CityModel` class, network graph, Dijkstra, agent spawning, module-level constants
- `Backend/Agent/model/agent.py` — `CityAgent` class, async step logic, edge traversal, memory initialisation
- `Backend/Agent/model/__init__.py` — re-exports both so `from model import CityModel, CityAgent` still works

---

## Mesa ABM Pattern

[Mesa](https://mesa.readthedocs.io/) follows a simple pattern:

```
Model
  └── owns a list of Agents
       └── each Agent has a step() method
            └── Model.step() calls agent.step() for all agents
```

This project uses **mesa-geo** — an extension that makes agents spatially aware. Every `CityAgent` is a `mesa_geo.GeoAgent`, meaning it carries a `geometry` attribute (a Shapely `Point` in WGS84 lon/lat coordinates) and can be exported as GeoJSON.

```
mesa.Model  →  CityModel
    └── city_agents: list[CityAgent]

mesa_geo.GeoAgent  →  CityAgent
    └── geometry: shapely.Point (lon, lat)
```

---

## CityModel Initialisation Sequence

`CityModel.__init__()` runs once when the FastAPI server starts. It performs twelve major steps:

```
1.  Load .env config (NUM_AGENTS, DATABASE_PATH, PERCEPTION_MODE, …)
2.  Set random seed if SPAWN_SEED provided (reproducible runs)
3.  Connect to main DuckDB (read_write, 8-attempt retry with 2s back-off)
        self.con = duckdb.connect(str(DB_PATH))   ← eixample_overture.duckdb
        self.con.install_extension("spatial")
        — All spatial queries (buildings, amenities, walk_edges) use this connection.
        — db.py routes every router read through a _SharedConProxy to this same
          connection, so only ONE file handle ever exists on Windows.
4.  Connect to perception DuckDB (separate file, read_write)
        self.perception_con = duckdb.connect(str(PERCEPTION_DB_PATH))  ← perception.duckdb
        load_streetview_perception(self.perception_con)   ← loads from output/results/*.json
        — Completely independent from self.con — no Windows file-lock conflict.
        — All perception queries in get_nearby_perception() use this connection.
5.  Load all walk_edges into memory (bidirectional):
        self.edges: dict[int, LineString]         ← edge_id → geometry
        self.node_to_edges: dict[tuple, list]     ← (lon,lat) → [(edge_id, geom, direction)]
        self.node_to_ped_edges: dict[tuple, list] ← footway/path/steps only
6.  Planarize graph with shapely.unary_union
        — Splits crossing edges at intersection points (Overture edges cross without
          sharing nodes). Creates proper junctions so agents can turn at every crossing.
7.  Snap-merge fragment nodes (within 15m of main component)
        — Isolated subgraph nodes too close to main network are remapped, connecting
          fragments without adding new edges.
8.  Build NetworkX graphs for Dijkstra:
        self._nx_graph (all edges) + self._nx_ped_graph (ped-only)
9.  Initialise shared LLMClient from LLMConfig.from_env()
10. Initialise AgentTracker (writes to Documentation/tracking_data/agent_tracking.duckdb)
11. Load plugins (weather snapshot, GTFS transit stops)
12. Spawn N agents (round-robin archetype assignment):
        for i in range(N):
            archetype = archetypes[i % 4]
            start_edge = random.choice(all_edges)
            target = _resolve_starting_destination(archetype)
            agent = CityAgent(model=self, edge=start_edge, archetype=archetype, …)
            self.city_agents.append(agent)
```

After init, the model is held in the FastAPI process memory. Each `/api/step` call advances it by one step.

---

## CityAgent Attributes

```python
class CityAgent(mesa_geo.GeoAgent):
    unique_id: int                  # sequential 0..N-1
    geometry: shapely.Point         # current position (lon, lat)
    
    # Movement state
    current_edge_id: int            # which walk_edge the agent is on
    current_edge_geom: LineString   # geometry of that edge
    position_along_edge: float      # 0.0 = start, 1.0 = end
    move_speed: float               # 0.10–0.20 (fraction of edge per step)
    previous_edge_id: int           # for anti-backtrack filter
    
    # Cognition
    memory: Memory                  # KVMemory (status) + StreamMemory (events)
    dispatcher: BlockDispatcher     # 4 decision blocks
    
    # Environment cache (refreshed each step)
    nearby_amenities: list[dict]
    street_perception: dict | None
```

---

## The async_step Loop

`CityModel.async_step()` is what `/api/step` calls. The key design is **full concurrency**: all agents run in parallel via `asyncio.gather`.

```python
async def async_step(self):
    self.steps += 1
    random.shuffle(self.city_agents)            # prevent ordering bias
    
    # Snapshot all positions BEFORE stepping
    agent_snapshot = [(a.unique_id, a.geometry, a.get_archetype())
                      for a in self.city_agents]
    
    # Run all agents concurrently
    await asyncio.gather(
        *[agent._async_step(agent_snapshot) for agent in self.city_agents],
        return_exceptions=True   # one failed agent doesn't crash the rest
    )
    
    if self.steps % 10 == 0:
        self.tracker.flush()     # write tracking buffer to DuckDB
```

**Why async?** With 500 agents and 50 LLM calls/step (each ~100–500ms), sequential execution would take 5–25 seconds per step. Async reduces this to the latency of the single slowest LLM call — typically 0.5–2 seconds.

---

## CityAgent._async_step() in Detail

Each agent's step follows this sequence:

```
1.  Query nearby amenities
        DuckDB: SELECT name, amenity, lon, lat,
                       ST_Distance(point, geometry) * 111320 AS dist_m
                FROM amenities
                WHERE dist_m < 100
                ORDER BY dist_m

2.  Query street perception
        perception.duckdb: SELECT * FROM streetview_perception
                           ORDER BY ST_Distance(point, geometry)
                           LIMIT 1
        (nearest analysed point within ~150m — uses self.perception_con)

3.  Update memory position:
        await memory.status.update("position", {
            "lon": lon, "lat": lat,
            "edge_id": current_edge_id,
            "current_node": current_edge_end_node
        })

4.  Determine needs_new_edge:
        needs_new_edge = (position_along_edge >= 1.0)
        (agent has reached the end of its current edge)

5.  Get candidate_edges:
        all edges connected to current_end_node
        MINUS current_edge_id         ← no U-turn
        MINUS previous_edge_id        ← anti-backtrack
        Each candidate annotated with: amenities nearby, perception snippet

6.  Get nearby_agents (within ~55m from pre-step snapshot)
7.  Get nearby_transit (GTFS stops within ~80m)

8.  dispatcher.run(
            step, candidate_edges,
            nearby_amenities, street_perception,
            needs_new_edge, nearby_agents,
            nearby_transit, time_of_day
        )  →  StepResult

9.  If needs_new_edge and mobility.action == "move_to_edge":
        _apply_mobility(params)    ← switch to new edge
        if deviated from proposed_path: recompute it
        log to AgentTracker

10. _advance_along_edge():
        position_along_edge += move_speed   # 0.10–0.20 per step
        geometry = interpolate_point(edge_geom, position_along_edge)
        if position_along_edge >= 1.0: mark edge complete
```

---

## Position-Along-Edge Movement Model

Agents do **not** teleport between intersections. They travel continuously along an edge over several steps.

```
Edge A→B  (say, 50m long)
move_speed = 0.15 (i.e., 15% of edge per step ≈ 7.5m/step ≈ walking pace)

Step 0:  position = 0.00  (at start of edge)
Step 1:  position = 0.15  (geometry interpolated 15% along)
Step 2:  position = 0.30
...
Step 7:  position = 1.05  → clipped to 1.0, edge complete
Step 8:  needs_new_edge = True → dispatcher picks next edge
```

Geometry is interpolated with Shapely's `edge_geom.interpolate(t, normalized=True)`, giving a smooth `Point(lon, lat)` at each step.

---

## Time of Day

`CityModel` exposes a `time_of_day` property:

```python
_TIME_PHASES = ["morning", "afternoon", "evening", "night"]
_STEPS_PER_PHASE = 24   # full day = 96 steps

@property
def time_of_day(self) -> str:
    idx = (self.steps // _STEPS_PER_PHASE) % len(_TIME_PHASES)
    return _TIME_PHASES[idx]
```

| Steps | Phase |
|-------|-------|
| 0–23 | morning |
| 24–47 | afternoon |
| 48–71 | evening |
| 72–95 | night |
| 96–119 | morning (repeats) |

This value is passed to all decision blocks and included in LLM prompts.

---

## External References

| Resource | URL |
|----------|-----|
| Mesa ABM documentation | https://mesa.readthedocs.io/ |
| mesa-geo spatial agents | https://mesa-geo.readthedocs.io/ |
| Python asyncio | https://docs.python.org/3/library/asyncio.html |
| Shapely geometry | https://shapely.readthedocs.io/ |
| GeoAgent (mesa-geo) | https://mesa-geo.readthedocs.io/en/stable/api/geo_agents.html |

---

**Next:** [`03_memory_system.md`](03_memory_system.md) — how agents store and recall state across steps.
