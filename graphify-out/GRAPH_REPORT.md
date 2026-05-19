# Graph Report - D:\IaaC\2ND_YEAR\THESIS\LLM_Based_UrbanABM  (2026-05-19)

## Corpus Check
- 64 files · ~129,042 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 646 nodes · 1571 edges · 17 communities detected
- Extraction: 56% EXTRACTED · 44% INFERRED · 0% AMBIGUOUS · INFERRED: 698 edges (avg confidence: 0.63)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Agent Lab Server|Agent Lab Server]]
- [[_COMMUNITY_Thinking Block System|Thinking Block System]]
- [[_COMMUNITY_Project Documentation|Project Documentation]]
- [[_COMMUNITY_Recording & Tracking|Recording & Tracking]]
- [[_COMMUNITY_Spatial Data Access|Spatial Data Access]]
- [[_COMMUNITY_Frontend Monitoring UI|Frontend Monitoring UI]]
- [[_COMMUNITY_Network Comparison|Network Comparison]]
- [[_COMMUNITY_LLM Memory System|LLM Memory System]]
- [[_COMMUNITY_Perceptual Comfort Metrics|Perceptual Comfort Metrics]]
- [[_COMMUNITY_Street View PLM Pipeline|Street View PLM Pipeline]]
- [[_COMMUNITY_Network Connector|Network Connector]]
- [[_COMMUNITY_Overture to DuckDB|Overture to DuckDB]]
- [[_COMMUNITY_LLM Client Service|LLM Client Service]]
- [[_COMMUNITY_Thinking Prompts|Thinking Prompts]]
- [[_COMMUNITY_Graph Infrastructure|Graph Infrastructure]]
- [[_COMMUNITY_Street PLM Job Launcher|Street PLM Job Launcher]]
- [[_COMMUNITY_LLM Config Rationale|LLM Config Rationale]]

## God Nodes (most connected - your core abstractions)
1. `LLMClient` - 79 edges
2. `CityModel` - 71 edges
3. `LLMConfig` - 66 edges
4. `AgentTracker` - 49 edges
5. `Memory` - 43 edges
6. `CityAgent` - 36 edges
7. `BlockDispatcher` - 35 edges
8. `BlockResult` - 29 edges
9. `MemoryNode` - 27 edges
10. `PerceptionDiary` - 27 edges

## Surprising Connections (you probably didn't know these)
- `Agent Archetypes Resident Commuter Tourist Student` --semantically_similar_to--> `Walker Perspectives (Resident/Commuter/Tourist/Student)`  [INFERRED] [semantically similar]
  README.md → scripts/streetview_analysis/viewer.html
- `Humanistic Dimensions (archetype consistency, diversity, decision coherence, spatial realism, amenity plausibility)` --conceptually_related_to--> `Agent Archetypes Resident Commuter Tourist Student`  [INFERRED]
  benchmark/results_05_system_benchmark.png → README.md
- `GeoParquet Behavior Recording System` --semantically_similar_to--> `Recording Session Feature`  [INFERRED] [semantically similar]
  Documentation/RECORDING_GUIDE.md → Frontend/mapbox.html
- `Perception Mode (both/perception/amenities/rule_based)` --conceptually_related_to--> `LLM-Driven Simulation Mode`  [INFERRED]
  test/README.md → benchmark/results_05_system_benchmark.png
- `DuckDB Database Engine` --shares_data_with--> `Backend REST API`  [INFERRED]
  benchmark/results_01_db_latency.png → Frontend/mapbox.html

## Communities

### Community 0 - "Agent Lab Server"
Cohesion: 0.04
Nodes (74): configure_single_agent(), _find_agent(), FixedAgentTracker, get_agent_cognition(), get_agent_info(), get_agent_memory(), get_agent_narrative(), get_agent_perception_text() (+66 more)

### Community 1 - "Thinking Block System"
Cohesion: 0.07
Nodes (64): Block, Block, BlockResult, Block base class — the fundamental unit of agent reasoning. Adapted from AgentSo, Standardised return type from any Block.run()., Base class for all agent thinking blocks.     Each subclass implements run() whi, Override in subclasses. Returns a BlockResult., CognitionBlock (+56 more)

### Community 2 - "Project Documentation"
Cohesion: 0.1
Nodes (63): Mesa-Geo Agents README, Backend API README, Configuration Guide, Crash-Safe Recording Guide, DuckDB Inspection Guide, Environment Spatial Database README, GCP BigQuery Access Guide, Backend LLM Providers README (+55 more)

### Community 3 - "Recording & Tracking"
Cohesion: 0.05
Nodes (42): get_llm_stats(), get_recording_status(), start_recording(), stop_recording(), Ensure all data is written to disk., AgentRecord, clear_recorder(), create_recorder() (+34 more)

### Community 4 - "Spatial Data Access"
Cohesion: 0.05
Nodes (53): get_amenities(), get_buildings(), get_db_connection(), get_walk_network(), Close the database connection., download_recording(), get_agent_cognition(), get_agent_info() (+45 more)

### Community 5 - "Frontend Monitoring UI"
Cohesion: 0.07
Nodes (50): agent_lab.html - Spatial Cognition Lab Interactive UI, Agent Lab 4-Tab Interface (Movement/Spatial Experience/Agent Mind/Narrative Lab), Agent Monitoring Panel, Amber Secondary Color (#ffbf00), Backend REST API, Barcelona Urban Context (El Raval/Eixample), results_01_db_latency.png - Database Latency Comparison, results_03_map_comparison.png - POI Data Source Comparison (+42 more)

### Community 6 - "Network Comparison"
Cohesion: 0.07
Nodes (25): Agent Movement and Decision Tracker  This module stores agent movements and deci, build_graph(), find_nearest_node(), main(), Compare Original vs Updated Walk Network Database  Tests: 1. Edge count and type, Test network connectivity., Test geographic coverage., Build NetworkX graph from walk_edges. (+17 more)

### Community 7 - "LLM Memory System"
Cohesion: 0.08
Nodes (18): KVMemory, KVMemory — async key-value memory store with locking. Adapted from AgentSociety', Thread-safe async key-value store for agent state., Return full snapshot of memory (deepcopy)., Increment a numeric value inside a nested dict key., Clamp a numeric subkey value to [lo, hi]., get_agent_memory(), get_agent_stream() (+10 more)

### Community 8 - "Perceptual Comfort Metrics"
Cohesion: 0.11
Nodes (28): Accessibility Metric, Cleanliness Metric, Crowding Metric, Enclosure/Exposure Metric, GeoPandas + Shapely Spatial Libraries, Google Cloud BigQuery, Greenery Metric, Hugging Face Hub (+20 more)

### Community 9 - "Street View PLM Pipeline"
Cohesion: 0.14
Nodes (25): BaseModel, analyze_image(), _coerce(), fetch_sv(), _infer_scene(), load_model(), main(), _model_echoed_template() (+17 more)

### Community 10 - "Network Connector"
Cohesion: 0.13
Nodes (21): analyze_components(), backup_database(), bridge_gaps(), build_graph_from_edges(), distance_degrees_to_meters(), insert_bridges(), main(), merge_pedestrian_roads() (+13 more)

### Community 11 - "Overture to DuckDB"
Cohesion: 0.13
Nodes (21): _aggregate_quadrant_field(), create_spatial_indexes(), extract_buildings(), extract_places(), extract_transportation(), load_streetview_perception(), main(), Overture Maps to DuckDB Pipeline (GCP BigQuery) -------------------------------- (+13 more)

### Community 12 - "LLM Client Service"
Cohesion: 0.11
Nodes (13): Async LLM client — provider-agnostic via OpenAI-compatible API. Ollama exposes /, from_env(), Provider-agnostic LLM configuration. Adapted from AgentSociety's LLMConfig patte, get_llm_service(), LLMService, LLM Service for Agent Perspective Summarization Uses Ollama with local Llama 3.1, Service to interact with Ollama for generating agent perspective summaries, Fallback summary when LLM is not available (+5 more)

### Community 13 - "Thinking Prompts"
Cohesion: 0.29
Nodes (11): cognition_update_prompt(), mobility_decision_prompt(), needs_evaluation_prompt(), Prompt templates for agent thinking blocks. Each template is a callable that fil, Prompt to evaluate how the visual street environment affects the agent's 3 needs, Prompt to evaluate how much visiting this amenity satisfies agent needs.     Now, Prompt to update agent's cognitive/emotional state based on recent experiences., Prompt asking the LLM to choose the next movement destination.     candidates: l (+3 more)

### Community 14 - "Graph Infrastructure"
Cohesion: 0.53
Nodes (6): Community Detection (103 communities), graph.html - Knowledge Graph Vis-Network Visualization, GRAPH_REPORT.md - Knowledge Graph Extraction Report, Knowledge Graph Structure, Tree-sitter AST Extraction, Vis-Network Graph Visualization Library

### Community 15 - "Street PLM Job Launcher"
Cohesion: 0.67
Nodes (3): main(), _parse_args(), launch_job.py ============= Submit street_plm_job.py as a Lightning AI Job on an

### Community 16 - "LLM Config Rationale"
Cohesion: 1.0
Nodes (1): Construct from environment variables (loaded from .env or shell).

## Knowledge Gaps
- **128 isolated node(s):** `Agent Movement and Decision Tracker  This module stores agent movements and deci`, `Tracks agent movements and decisions in a DuckDB database with spatial indexing.`, `Initialize the agent tracker.                  Args:             db_path: Path t`, `Initialize database connection and create tables with spatial indexing.`, `Log an agent's movement to the database.          Args:             agent_id: Un` (+123 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `LLM Config Rationale`** (1 nodes): `Construct from environment variables (loaded from .env or shell).`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `DuckDB Database Engine` connect `Network Comparison` to `Agent Lab Server`, `Spatial Data Access`, `Frontend Monitoring UI`, `Network Connector`, `Overture to DuckDB`?**
  _High betweenness centrality (0.223) - this node is a cross-community bridge._
- **Why does `CityModel` connect `Agent Lab Server` to `Thinking Block System`, `Recording & Tracking`, `Spatial Data Access`, `LLM Memory System`, `LLM Client Service`?**
  _High betweenness centrality (0.142) - this node is a cross-community bridge._
- **Why does `Backend REST API` connect `Frontend Monitoring UI` to `Network Comparison`?**
  _High betweenness centrality (0.137) - this node is a cross-community bridge._
- **Are the 72 inferred relationships involving `LLMClient` (e.g. with `Get DuckDB connection` and `API root - health check`) actually correct?**
  _`LLMClient` has 72 INFERRED edges - model-reasoned connections that need verification._
- **Are the 55 inferred relationships involving `CityModel` (e.g. with `Get DuckDB connection` and `API root - health check`) actually correct?**
  _`CityModel` has 55 INFERRED edges - model-reasoned connections that need verification._
- **Are the 63 inferred relationships involving `LLMConfig` (e.g. with `Get DuckDB connection` and `API root - health check`) actually correct?**
  _`LLMConfig` has 63 INFERRED edges - model-reasoned connections that need verification._
- **Are the 39 inferred relationships involving `AgentTracker` (e.g. with `CityAgent` and `CityModel`) actually correct?**
  _`AgentTracker` has 39 INFERRED edges - model-reasoned connections that need verification._