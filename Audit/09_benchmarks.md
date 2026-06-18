# 09 — Benchmarks & Evaluation

Five Jupyter notebooks in `benchmark/` establish the empirical basis for all major technology choices in this project. Each notebook tests one dimension of the system stack.

**Key files:**
- `benchmark/01_database_comparison.ipynb`
- `benchmark/02_llm_provider_comparison.ipynb`
- `benchmark/03_map_data_comparison.ipynb`
- `benchmark/04_vlm_perception_comparison.ipynb`
- `benchmark/05_system_integration_benchmark.ipynb`
- `benchmark/README.md` — setup + humanistic scoring rubric

---

## Benchmark 01 — Database Comparison

**Question:** Which spatial database is best for this simulation's query patterns?

**Contestants:**
- DuckDB + spatial extension
- SQLite + SpatiaLite extension
- PostgreSQL + PostGIS

**Queries tested:**
- Nearest-neighbour amenity lookup (agents' most frequent query)
- Bounding-box building filter
- Walk edge adjacency graph traversal
- Full walk network load into memory

**Key findings:**
- DuckDB was fastest across all queries at the Eixample scale (~8 MB database)
- Sub-millisecond amenity queries with no server process
- Single-file portability — no installation, version pinning, or Docker needed
- SpatiaLite was competitive but DuckDB's httpfs extension also enables direct S3 reads (used in Overture pipeline)

**→ Result: DuckDB selected as primary database.**

---

## Benchmark 02 — LLM Provider Comparison

**Question:** Which LLM provider gives the best quality/cost/speed trade-off for agent decision prompts?

**Contestants:**
- Ollama (local, free) — llama3.1:8b, Qwen2.5:7B
- vLLM (local GPU)
- GPT-4o-mini (OpenAI cloud)
- GPT-4o (OpenAI cloud)

**Metrics:**
- Decision latency (ms/call)
- JSON schema compliance (% of responses parseable)
- Archetype consistency (does tourist pick novel edges more than commuter?)
- Token cost (per 1,000 steps)

**Key findings:**
- GPT-4o-mini: best JSON compliance, fastest cloud option, affordable
- Ollama (llama3.1): good compliance, zero cost, 2–5× slower than cloud
- vLLM: near-GPT quality with quantised models, requires GPU
- GPT-4o: no significant improvement over GPT-4o-mini for pedestrian decisions

**→ Result: Ollama recommended for development; GPT-4o-mini or DeepSeek for production quality.**

---

## Benchmark 03 — Map Data Comparison

**Question:** Overture Maps or OpenStreetMap — which has better coverage for Barcelona Eixample?

**Dimensions tested:**
- POI (amenity) coverage: count of restaurants, cafes, pharmacies, parks
- Network completeness: footway coverage, pedestrian-only paths
- Data freshness: last update dates for major streets
- Query performance in DuckDB

**Key findings:**
- Overture Maps: 40% more POIs in the Eixample bbox (better for agent destination planning)
- OSM: slightly better footway tagging in some areas (pedestrian-only streets)
- Overture release cadence: quarterly snapshots; OSM: real-time but inconsistent schema
- DuckDB performance: identical (both stored as same schema after import)

**→ Result: Overture Maps as primary source; `OVERTURE_RELEASE` env var for snapshot control.**

---

## Benchmark 04 — VLM Perception Comparison

**Question:** Which vision-language model produces the best structured scene descriptions for street images?

**Contestants:**
- Qwen2.5-VL-3B-Instruct (small, fast, local)
- Qwen2.5-VL-7B-Instruct (larger, better quality)
- Qwen3-VL-8B-Instruct (newest, best reasoning)

**Metrics:**
- **JSON schema compliance** — % of outputs with all 13 required fields correctly populated
- **OCR IoU** — text detection accuracy vs ground truth bounding boxes
- **Object bbox IoU** — building / person / vehicle detection accuracy
- **Keypoint distance** — landmark localization precision
- **Archetype-perspective quality** — do `as_resident` and `as_tourist` fields differ meaningfully?

**Key findings:**
- Qwen3-VL-8B: highest schema compliance, most differentiated archetype perspectives
- Qwen2.5-VL-3B: fastest (fits in 8 GB VRAM), compliance slightly lower
- All Qwen models outperformed GPT-4o-Vision on structured JSON output for this use case
- `as_resident` and `as_tourist` fields diverge meaningfully for rich urban scenes (cafes, landmarks) but are nearly identical for featureless residential stretches

**→ Result: Qwen2.5-VL-3B for speed; Qwen3-VL-8B for quality. Both supported via `POST /api/streetview/analyze`.**

---

## Benchmark 05 — System Integration

**Question:** Does LLM-driven movement produce more realistic pedestrian behaviour than rule-based movement?

**Setup:**
- 50 agents, 200 simulation steps
- Condition A: `LLM_CALLS_PER_STEP=50` (LLM-driven)
- Condition B: `LLM_CALLS_PER_STEP=0` (rule-based, least-visited edge)
- Same starting positions, same DuckDB data

### Humanistic Scoring Rubric

Five dimensions, each 0.0–1.0:

| Dimension | What it measures | How scored |
|-----------|-----------------|------------|
| **Diversity** | Spatial exploration breadth | `unique_edges_visited / total_steps` |
| **Archetype Consistency** | Does destination match declared archetype? | Tourist visited attraction? Commuter took direct route? |
| **Amenity Plausibility** | Does amenity visited match current needs? | Hungry agent visited restaurant → 1.0; visited gym → 0.0 |
| **Spatial Realism** | Trajectory entropy vs pedestrian movement norms | Compare to real GPS traces from literature |
| **Decision Coherence** | Is LLM reasoning internally consistent? | NLP consistency score on reasoning strings |

**Key findings:**
- LLM-driven agents scored +0.18 average on diversity (more varied routes)
- Archetype consistency: LLM +0.24 (tourists took more detours; commuters more direct)
- Amenity plausibility: LLM +0.31 (needs-driven visits more contextually appropriate)
- Spatial realism: roughly equal (rule-based least-visited also produces non-trivial exploration)
- Decision coherence: LLM +0.41 (rule-based has no reasoning to score)

**→ Result: LLM-driven movement is measurably more humanistic across 4/5 dimensions.**

---

## Running the Benchmarks

```bash
cd benchmark
pip install jupyter matplotlib seaborn plotly
jupyter notebook

# Open any notebook and Run All
```

Large notebooks (04, 05) require either:
- A GPU (for VLM inference), or
- Pre-computed `vlm_results.json` (provided in `benchmark/`)

---

## Benchmark Results Viewer

```bash
# Open in browser directly (no server needed):
benchmark/vlm_benchmark_viewer.html
benchmark/road_classes.html
```

---

## External References

| Resource | URL |
|----------|-----|
| DuckDB spatial benchmarks | https://duckdb.org/2023/04/14/spatial.html |
| Overture Maps coverage analysis | https://docs.overturemaps.org/ |
| Qwen2.5-VL paper | https://arxiv.org/abs/2502.13923 |
| Mesa benchmarking guide | https://mesa.readthedocs.io/en/stable/performance.html |
| Agent-based modelling evaluation literature | Railsback & Grimm, "Agent-Based and Individual-Based Modeling" (Princeton UP) |

---

**Next:** [`10_configuration.md`](10_configuration.md) — how to configure and run the full system.
