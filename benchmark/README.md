# Benchmark Module

This folder contains Jupyter notebooks that benchmark all major technology choices
in the LLM-Based UrbanABM system, evaluated against two primary metrics:

1. **Agent response latency** — time to produce an agent decision (ms/step)
2. **Humanistic accuracy** — how closely agent behaviour matches realistic human patterns

## Notebooks

| Notebook | Compares | Key Metrics |
|---|---|---|
| `01_database_comparison.ipynb` | DuckDB vs SQLite+SpatiaLite vs PostgreSQL+PostGIS | Query latency, throughput, completeness |
| `02_llm_provider_comparison.ipynb` | vLLM vs Ollama vs GPT-4o-mini vs GPT-4o | Decision latency, archetype accuracy, token cost |
| `03_map_data_comparison.ipynb` | Overture Maps vs OpenStreetMap | POI coverage, network completeness, query speed |
| `04_vlm_perception_comparison.ipynb` | Qwen2.5-VL-7B vs Qwen3-VL-8B-Instruct | JSON schema compliance, OCR IoU, object detection IoU, keypoint distance |
| `05_system_integration_benchmark.ipynb` | LLM-driven vs rule-based agents | End-to-end latency, humanistic scoring |

## Setup

```bash
pip install jupyter matplotlib seaborn folium plotly psycopg2-binary duckdb openai
jupyter notebook
```

## Test Data

- `data/sample_agents.json` — 20 test agent profiles (archetype + location + needs)
- `data/sample_queries.sql` — Reference spatial queries for database benchmarks

## Humanistic Scoring Rubric

End-to-end benchmark scores agents on 5 dimensions (0-1 each):
- **Diversity** — unique edges visited / total steps
- **Archetype consistency** — does agent behaviour match declared archetype?
- **Amenity plausibility** — does amenity visit type match agent needs?
- **Spatial realism** — trajectory entropy vs expected pedestrian patterns
- **Decision coherence** — LLM reasoning consistency across steps
