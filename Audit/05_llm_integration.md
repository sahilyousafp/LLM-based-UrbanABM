# 05 — LLM Integration

This document covers the LLM client, provider routing, all six prompt templates, and the circuit breaker / budget guard.

**Key files:**
- `Backend/LLM/llm_config.py`
- `Backend/LLM/llm_client.py`
- `Backend/LLM/Thinking/prompts.py`

---

## Provider Abstraction

The system supports nine LLM providers through a single `AsyncOpenAI`-compatible client. Switching providers requires only `.env` changes — no code changes.

### LLMConfig

`Backend/LLM/llm_config.py`:

```python
@dataclass
class LLMConfig:
    provider:    str    # from LLM_PROVIDER
    model:       str    # from LLM_MODEL
    api_key:     str    # from LLM_API_KEY or provider-specific env
    base_url:    str    # LLM_BASE_URL override (for vLLM, local servers)
    timeout:     int    # LLM_TIMEOUT (default 60s)
    max_tokens:  int    # LLM_MAX_TOKENS (default 256)
    temperature: float  # LLM_TEMPERATURE (default 0.7)

    @classmethod
    def from_env(cls) -> "LLMConfig":
        # Resolves base_url and api_key per provider
        ...
```

### Provider Routing Table

| `LLM_PROVIDER` | `LLM_MODEL` example | base_url | Notes |
|----------------|---------------------|----------|-------|
| `ollama` | `llama3.1` | `http://localhost:11434/v1` | Free, local, no GPU needed |
| `openai` | `gpt-4o-mini` | (default) | Requires `OPENAI_API_KEY` |
| `deepseek` | `deepseek-chat` | `https://api.deepseek.com/v1` | Cheap, good reasoning |
| `gemini` | `gemini-2.0-flash-lite` | `https://generativelanguage.googleapis.com/v1beta/openai/` | Requires `GEMINI_API_KEY` |
| `groq` | `llama-3.1-8b-instant` | `https://api.groq.com/openai/v1` | Fast cloud inference |
| `vllm` | `Qwen/Qwen2.5-7B-Instruct` | `LLM_BASE_URL` | Local GPU, custom endpoint |
| `lmdeploy` | `Qwen2.5-7B-Instruct` | Docker container | Local GPU, Docker |
| `openrouter` | `meta-llama/llama-3.1-8b-instruct` | `https://openrouter.ai/api/v1` | Provider aggregator |
| `docker` | any | `LLM_BASE_URL` | Generic OpenAI-compat container |

---

## LLMClient

`Backend/LLM/llm_client.py`:

```python
class LLMClient:
    def __init__(self, config: LLMConfig):
        # For Ollama: prefer native /api/chat (more reliable than v1/chat/completions)
        # For others: use AsyncOpenAI with base_url + api_key
        ...

    async def chat_json(self, messages: list[dict]) -> dict | None:
        """
        Send a chat completion request, parse JSON response.
        Returns dict on success, None on failure (triggers fallback).
        """
        try:
            response = await client.chat.completions.create(
                model=config.model,
                messages=messages,
                max_tokens=config.max_tokens,
                temperature=config.temperature,
                response_format={"type": "json_object"},
            )
            return json.loads(response.choices[0].message.content)
        except Exception:
            self._record_failure()
            return None

    def stats(self) -> dict:
        return {
            "total_calls":         self._total_calls,
            "total_input_tokens":  self._total_input_tokens,
            "total_output_tokens": self._total_output_tokens,
            "total_latency_ms":    self._total_latency_ms,
            "total_errors":        self._total_errors,
            "total_fallbacks":     self._total_fallbacks,
        }
```

All prompt functions return `list[dict]` (OpenAI messages format). The client does not know anything about what the messages contain.

### Circuit Breaker

```
After LLM_CIRCUIT_BREAKER_THRESHOLD (default 5) consecutive failures:
    → Circuit opens: all LLM calls return None immediately
    → Agents fall back to rule-based movement
    → After LLM_CIRCUIT_BREAKER_RECOVERY (default 30s): circuit closes
    → LLM calls resume
```

This prevents a broken LLM endpoint from stalling the simulation.

---

## How Agents Access External Data

A common question: *do agents call tools / functions to fetch data, or how do they "know" about amenities, weather, transit, and street scenes?*

**Answer: agents do NOT use tool-use / function-calling.** All external data is pre-fetched by the Python simulation layer each step and injected as plain text into the LLM prompt. The LLM is a **reasoning engine over a fixed spatial snapshot** — it never queries anything itself.

### The Model: Context Injection, not Tool-Use

```
Every agent step — agent._async_step()  (Backend/Agent/model/agent.py)
  │
  │  Python runs the spatial queries:
  ├─ model.get_nearby_amenities(geom)                    → DuckDB (amenities)
  ├─ model.get_nearby_perception(geom)                   → perception.duckdb
  ├─ model.get_nearby_external_data(geom, "ext_transit_stops")  → DuckDB
  ├─ _get_candidate_edges()   → each candidate pre-annotated with amenities + perception
  │
  ▼
  dispatcher.run(candidate_edges, nearby_amenities,
                 street_perception, nearby_transit, time_of_day, …)
  │
  ▼
  MobilityBlock → mobility_decision_prompt(…)
  │
  │  ALL data is already embedded as text in the prompt
  ▼
  LLM  ──►  returns {"choice": <int>, "reasoning": "<one sentence>"}
```

The LLM call is a single round-trip: prompt in → JSON decision out. There is no second call, no tool invocation, no follow-up query.

### Per-Source: How Each One Reaches the LLM

| Data | Source | Query method (in `city_model.py`) | How it appears to the LLM |
|------|--------|-----------------------------------|---------------------------|
| **Amenities** | `amenities` table | `get_nearby_amenities()` | Annotated on each candidate edge: `amenities=[cafe, park]` (≤3 types) |
| **Street perception** | `perception.duckdb` | `get_nearby_perception()` | Multi-line scene block: scene_overview, buildings, vegetation, pedestrian_activity, lighting + the agent's archetype-specific view (e.g. `as_tourist`) |
| **Transit stops** | `ext_transit_stops` | `get_nearby_external_data()` | One line in prompt: `Transit stops: 'Gran Via' (L2) 45m away` |
| **Weather** | `ext_weather` | `_load_weather_snapshot()` (once at init) | **NOT shown to the LLM.** Only adjusts rule-based need-decay multipliers in `NeedsBlock` (rain → faster comfort decay, heat → faster energy decay) |
| **Walk network** | `walk_edges` | loaded into memory at init | Each candidate edge: `[N] edge_id=X dir=Y desc=Carrer de Provença` |

### The Key Constraint

The LLM sees **only** what Python pre-fetched for the current position. If an agent's reasoning would benefit from knowing what is 500 m ahead, but Python only queried a 100 m radius, the LLM cannot ask for more — it must choose from the snapshot it was handed. Fetch radii are fixed in the query methods (amenities ~111 m, perception ~168 m, transit ~80 m).

This is a deliberate trade-off: bounded, predictable token cost and latency (one LLM call per decision) at the price of the LLM not being able to self-direct its data gathering.

### Tool-Use Status & Future Extension

The infrastructure is *half* present. `Backend/LLM/llm_client.py`'s `chat()` method accepts a `tools=` parameter that passes through to the OpenAI API:

```python
async def chat(self, messages, tools=None, ...):
    if tools:
        kwargs["tools"] = tools   # scaffold — no caller ever populates this
```

But it is unused: no tool definitions exist, `chat_json()` (the method every decision block actually calls) does not expose `tools=`, and no `tool_calls` are parsed from responses. **True function-calling is not implemented.**

To add it as future work would require: (1) tool definitions as JSON schema (e.g. `query_nearby_amenities(lon, lat, radius_m)`, `get_weather()`); (2) a `chat_with_tools()` loop in `llm_client.py` that executes returned `tool_calls`, appends their results, and re-calls the LLM until it produces a final decision; and (3) wiring `MobilityBlock` to use it. The cost is 2–4 LLM calls per step instead of one, in exchange for agents that dynamically decide what to look at.

### Thesis Framing

> At each simulation step, spatial queries for amenities, street-level perception, and transit proximity are resolved by the Python simulation layer and formatted as structured natural-language context injected into the LLM prompt. The LLM functions as a reasoning engine over this pre-computed spatial snapshot rather than as a dynamic query agent. This design bounds per-step token cost and latency while preserving archetype-specific contextual reasoning — consistent with the context-construction approach used in LLM-driven agent simulations (cf. Park et al. 2023, *Generative Agents*).

---

## Prompt Templates

All prompts live in `Backend/LLM/Thinking/prompts.py`. Every function:
- Takes typed parameters
- Returns `list[dict]` (system + user messages)
- Includes only non-null context (empty fields are omitted)

### 1. `mobility_decision_prompt()`

**Used by:** MobilityBlock  
**Purpose:** Choose the next street edge  
**Output:** `{"choice": int, "reasoning": "one sentence"}`

Key parameters and their purpose:

| Parameter | Type | Purpose |
|-----------|------|---------|
| `archetype` | str | Shapes decision priorities (tourist explores, commuter optimises) |
| `needs` | dict | High hunger → prefer edges near restaurants |
| `cognition` | dict | Low energy → prefer comfortable/short routes |
| `time_of_day` | str | Morning vs evening context |
| `candidates` | list | Available edges with amenity + perception annotations |
| `street_perception` | dict | VLM scene description at current location |
| `destination` | dict | Target POI (name, type, coordinates) |
| `path_hint_edge_id` | int | Dijkstra-optimal next step (shown as `[SHORTEST PATH TO DESTINATION]`) |
| `nav_mode` | str | `"gps"` = must follow GPS label; `"direction_sense"` = compass bearing; `"both"` |
| `explore_budget` / `free_steps_remaining` | int | How many free steps remain |
| `visited_counts` | dict | `{edge_id: count}` — tagged as `[visited Nx]` or `[NEW]` |
| `nearby_agents` | list | Archetype counts within 55m (social context) |
| `nearby_transit` | list | GTFS stops within 80m |
| `plan_context` | dict | Current phase goal + time_of_day + perception preferences |
| `memory_context` | str | Consolidated memory summaries |

**GPS enforcement rule** (in prompt text):
> "If any candidate shows [SHORTEST PATH TO DESTINATION], you MUST choose it. Visit counts, amenity preferences, and archetype interests are NOT valid reasons to skip it."

### 2. `cognition_update_prompt()`

**Used by:** CognitionBlock (every 10 steps)  
**Purpose:** Update mood / curiosity / fatigue  
**Output:** `{"mood": str, "curiosity": float, "fatigue": float, "summary": "2-3 sentence first-person thought"}`

Inputs: archetype, full agent_profile (age, preferences), current needs, current cognition, 15 recent events, street perception, memory context, time_of_day.

The system prompt enforces grounded, specific output:
- Must reference actual events/places from the recent history
- Never open with time of day; never use filler phrases ("I find myself", "as the day progresses")
- Summary reads like an internal thought, not a status report
- Mood vocabulary: 14 options (happy, neutral, tired, curious, bored, energised, social, focused, **irritable, content, restless, anxious, relieved, absorbed**)
- All 4 needs have explicit bi-directional mood mappings (e.g. comfort < 0.3 → anxious; energy > 0.8 → curiosity boosted)

### 3. `needs_evaluation_prompt()`

**Used by:** NeedsBlock (when agent is at an amenity)  
**Purpose:** How much does this visit satisfy needs?  
**Output:** `{"hunger_delta": float, "energy_delta": float, "social_delta": float, "comfort_delta": float, "activity": "what agent does there"}`

### 4. `visual_satisfaction_prompt()`

**Used by:** NeedsBlock (every 5 steps, if perception available)  
**Purpose:** How does the physical environment affect needs?  
**Output:** same delta format as needs_evaluation_prompt  

Calibration rule in prompt: "Most streets should score near 0.0. Only truly exceptional or notably unpleasant spaces warrant ±0.05+."

### 5. `memory_summary_prompt()`

**Used by:** CognitionBlock (archetype-specific interval)  
**Purpose:** Compress recent stream events into a narrative summary  
**Output:** `{"summary": "short narrative paragraph"}`

### 6. `memory_consolidation_prompt()`

**Used by:** CognitionBlock (residents only, every 60 steps)  
**Purpose:** Merge multiple episode summaries into a unified long-term memory  
**Output:** `{"unified_memory": "consolidated narrative"}`

---

## Prompt Structure Example

```python
# mobility_decision_prompt produces:
[
    {
        "role": "system",
        "content": "You are the decision-making core of a pedestrian agent in Barcelona's Eixample district..."
    },
    {
        "role": "user",
        "content": """Agent Profile:
  Archetype: tourist
  Time of day: morning
  Needs: hunger=0.45, energy=0.82, social=0.31, comfort=0.70
  Mood: curious, Curiosity: 0.85, Fatigue: 0.12
  Current Position: lon=2.162144, lat=41.386266
  
[SHORTEST PATH →] Edge 1234 (forward) [NEW]   Carrer de Provença
                    env=[lively pedestrian activity, modernist buildings]
Edge 5678 (forward) [visited 2x — strongly avoid revisiting]  Carrer d'Enric Granados
...

Choose the index of the best candidate...
Respond with JSON: {"choice": <int>, "reasoning": "<one sentence why>"}"""
    }
]
```

---

## Hot-Swap LLM Config

The FastAPI endpoint `POST /api/config/llm` lets you change the LLM provider **mid-simulation** without restarting:

```json
// Request
{"provider": "deepseek", "model": "deepseek-chat", "api_key": "sk-..."}

// Response
{"status": "ok", "provider": "deepseek", "model": "deepseek-chat"}
```

This rebuilds `city_model.llm_client` in-place. Useful for benchmarking different providers on the same simulation state.

---

## Token Budget Reference

| Config | Tokens/step | Use case |
|--------|------------|---------|
| `LLM_CALLS_PER_STEP=0` | 0 | Pure rule-based, fastest |
| `LLM_CALLS_PER_STEP=10` | ~600 | Light LLM flavour |
| `LLM_CALLS_PER_STEP=50` | ~3,000 | **Default** — balanced |
| `LLM_CALLS_PER_STEP=500` | ~30,000 | Full LLM (GPU recommended) |

Calculation: 50 agents × ~60 tokens/call (mobility) = ~3,000 tokens/step.  
Cognition adds ~100 tokens every 10 steps for each of 50 agents = +500 tokens amortised.

---

## External References

| Resource | URL |
|----------|-----|
| Ollama | https://ollama.com/download |
| Ollama model library | https://ollama.com/library |
| OpenAI Python SDK | https://github.com/openai/openai-python |
| OpenAI API reference | https://platform.openai.com/docs/api-reference |
| DeepSeek API | https://platform.deepseek.com/api-docs |
| vLLM documentation | https://docs.vllm.ai/ |
| Groq console | https://console.groq.com/ |
| OpenRouter | https://openrouter.ai/docs |
| Qwen2.5 model card | https://huggingface.co/Qwen/Qwen2.5-7B-Instruct |
| Gemini API | https://ai.google.dev/gemini-api/docs |

---

**Next:** [`06_spatial_reasoning.md`](06_spatial_reasoning.md) — how agents navigate the street graph and what VLM perception adds.
