# CitySim Realism Enhancements — Implementation Plan

## Context
This plan adds five CitySim-inspired realism features to the LLM-based UrbanABM system.
**Core constraint:** existing agent movement, LLM budget, async patterns, and REST API must behave identically. All additions are purely additive.

## Invariants — Do Not Change
- `DECAY_RATES`, `AMENITY_NEED_MAP` in `needs_block.py`
- `COGNITION_INTERVAL = 10` logic in `cognition_block.py`
- `LLM_CALLS_PER_STEP` budget guard and `_can_use_llm_for_mobility()` in `dispatcher.py`
- `_advance_along_edge()`, `_apply_mobility()`, `_simple_move()` in `model.py`
- All FastAPI endpoints in `map_server.py`
- All DuckDB queries in `model.py`
- `StreamMemory`, `Memory`, `Block`, `BlockResult` — no changes

---

## Change 1 — Big Five Personality + Demographics

### 1a. `Backend/LLM/Memory/kv_memory.py`

**Find this exact string:**
```python
    "agent_profile": {"archetype": "resident", "age": 30, "preferences": []},
```

**Replace with:**
```python
    "agent_profile": {
        "archetype": "resident",
        "age": 30,
        "preferences": [],
        "personality": {
            "openness": 2,
            "conscientiousness": 2,
            "extraversion": 2,
            "agreeableness": 2,
            "neuroticism": 2,
        },
        "income": "medium",
        "occupation": "general",
    },
```

Also add two new top-level schema keys. Find:
```python
    "cognition_state": {"mood": "neutral", "curiosity": 0.7, "fatigue": 0.0},
}
```

Replace with:
```python
    "cognition_state": {"mood": "neutral", "curiosity": 0.7, "fatigue": 0.0},
    "poi_beliefs": {},
    "daily_plan": {"wake_hour": 8, "activities": [], "current_index": 0},
    "reflections": [],
}
```

---

### 1b. `Backend/Agent/model.py` — update `_init_memory_sync`

**Find this exact string:**
```python
        self.memory.status._data["agent_profile"] = {
            "archetype": archetype,
            "age": random.randint(18, 70),
            "preferences": self._archetype_preferences(archetype),
        }
```

**Replace with:**
```python
        self.memory.status._data["agent_profile"] = {
            "archetype": archetype,
            "age": random.randint(18, 70),
            "preferences": self._archetype_preferences(archetype),
            "personality": CityAgent._sample_personality(archetype),
            "income": CityAgent._sample_income(archetype),
            "occupation": CityAgent._sample_occupation(archetype),
        }
```

---

### 1c. `Backend/Agent/model.py` — update `_init_memory` (async version)

**Find this exact string:**
```python
        await self.memory.status.update("agent_profile", {
            "archetype": archetype,
            "age": random.randint(18, 70),
            "preferences": self._archetype_preferences(archetype),
        })
```

**Replace with:**
```python
        await self.memory.status.update("agent_profile", {
            "archetype": archetype,
            "age": random.randint(18, 70),
            "preferences": self._archetype_preferences(archetype),
            "personality": CityAgent._sample_personality(archetype),
            "income": CityAgent._sample_income(archetype),
            "occupation": CityAgent._sample_occupation(archetype),
        })
```

---

### 1d. `Backend/Agent/model.py` — add three static methods

**Find this exact string:**
```python
        return prefs.get(archetype, [])
```

**Insert AFTER that line (keep existing `_archetype_preferences` intact):**
```python

    @staticmethod
    def _sample_personality(archetype: str) -> dict:
        base = {"openness": 2, "conscientiousness": 2, "extraversion": 2, "agreeableness": 2, "neuroticism": 2}
        biases = {
            "tourist":  {"openness": 1, "extraversion": 1},
            "student":  {"openness": 1, "extraversion": 1, "neuroticism": 1},
            "commuter": {"conscientiousness": 1, "neuroticism": -1},
            "resident": {"agreeableness": 1, "openness": -1},
        }
        result = {}
        for trait, base_val in base.items():
            bias = biases.get(archetype, {}).get(trait, 0)
            result[trait] = max(1, min(3, base_val + random.choice([-1, 0, 0, 1]) + bias))
        return result

    @staticmethod
    def _sample_income(archetype: str) -> str:
        weights = {
            "tourist":  ["low", "medium", "high"],
            "commuter": ["medium", "medium", "high"],
            "student":  ["low", "low", "medium"],
            "resident": ["low", "medium", "medium"],
        }
        return random.choice(weights.get(archetype, ["low", "medium", "high"]))

    @staticmethod
    def _sample_occupation(archetype: str) -> str:
        options = {
            "tourist":  ["tourist"],
            "commuter": ["office", "service", "retail"],
            "student":  ["student"],
            "resident": ["office", "retired", "service", "home"],
        }
        return random.choice(options.get(archetype, ["general"]))
```

---

### 1e. `Backend/Agent/OSM_model.py` — same fix as 1b and 1c

Apply identical edits to `_init_memory_sync` and `_init_memory` in `OSM_model.py`. The code blocks are identical to model.py. Apply the same find-and-replace from steps 1b and 1c.

---

## Change 2 — POI Belief System (Kalman Filter)

No new LLM calls. Piggybacks on the existing `needs_evaluation_prompt` call by requesting one extra field in the same JSON response.

### 2a. `Backend/LLM/Thinking/prompts.py` — extend `needs_evaluation_prompt`

**Find this exact string:**
```python
How much does this visit satisfy each need? Provide values 0.0-1.0 (0=no satisfaction, 1=fully satisfied).
Also give a brief description of what the agent does there.

Respond with JSON:
{{"hunger_delta": <float>, "energy_delta": <float>, "social_delta": <float>, "activity": "<what agent does>"}}"""
```

**Replace with:**
```python
How much does this visit satisfy each need? Provide values 0.0-1.0 (0=no satisfaction, 1=fully satisfied).
Also give a brief description of what the agent does there.
Also rate overall satisfaction with this specific place (0.0=terrible, 0.5=neutral, 1.0=excellent).

Respond with JSON:
{{"hunger_delta": <float>, "energy_delta": <float>, "social_delta": <float>, "activity": "<what agent does>", "satisfaction_rating": <float 0.0-1.0>}}"""
```

---

### 2b. `Backend/LLM/Thinking/blocks/needs_block.py` — add Kalman update after existing LLM response handling

**Find this exact string:**
```python
                llm_used = True
            else:
```

**Replace with:**
```python
                llm_used = True
                # Update POI belief with Kalman-style filter (no extra LLM call)
                sat_rating = float(response.get("satisfaction_rating", 0.5))
                poi_beliefs = await self.memory.status.get("poi_beliefs", {})
                prior = poi_beliefs.get(amenity_name, 0.5)
                poi_beliefs[amenity_name] = round(prior + 0.3 * (sat_rating - prior), 3)
                await self.memory.status.update("poi_beliefs", poi_beliefs)
            else:
```

---

### 2c. `Backend/LLM/Thinking/prompts.py` — expose poi_beliefs in mobility prompt

**Find the function signature:**
```python
def mobility_decision_prompt(
    archetype: str,
    needs: dict,
    cognition: dict,
    recent_history: str,
    current_position: dict,
    candidates: list[dict],
    street_perception: dict | None = None,
) -> list[dict]:
```

**Replace with:**
```python
def mobility_decision_prompt(
    archetype: str,
    needs: dict,
    cognition: dict,
    recent_history: str,
    current_position: dict,
    candidates: list[dict],
    street_perception: dict | None = None,
    profile: dict | None = None,
    poi_beliefs: dict | None = None,
    urgent_need: str | None = None,
    current_goal: str = "",
) -> list[dict]:
```

**Find this exact string in `mobility_decision_prompt`:**
```python
    user_content = f"""Agent Profile:
  Archetype: {archetype}
  Needs: hunger={needs.get('hunger', 0.5):.2f}, energy={needs.get('energy', 1.0):.2f}, social={needs.get('social', 0.5):.2f}
  Mood: {cognition.get('mood', 'neutral')}, Curiosity: {cognition.get('curiosity', 0.7):.2f}, Fatigue: {cognition.get('fatigue', 0.0):.2f}
  Current Position: lon={current_position.get('lon', 0):.6f}, lat={current_position.get('lat', 0):.6f}{perception_text}
```

**Replace with:**
```python
    # Build optional context sections
    personality = (profile or {}).get("personality", {})
    personality_text = (
        f"openness={personality.get('openness',2)}/3, "
        f"extraversion={personality.get('extraversion',2)}/3, "
        f"conscientiousness={personality.get('conscientiousness',2)}/3"
    ) if personality else ""

    top_places = ""
    if poi_beliefs:
        favourites = sorted(poi_beliefs.items(), key=lambda x: x[1], reverse=True)[:3]
        if favourites:
            top_places = "  Favourite places: " + ", ".join(f"{n}({v:.2f})" for n, v in favourites)

    urgency_text = ""
    if urgent_need == "hunger":
        urgency_text = "\nURGENT: Agent is very hungry — prioritise edges near restaurants, cafes, or supermarkets."
    elif urgent_need == "energy":
        urgency_text = "\nURGENT: Agent is exhausted — prioritise edges near parks, benches, or quiet areas."
    elif urgent_need == "social":
        urgency_text = "\nURGENT: Agent craves social contact — prioritise edges near cafes, bars, or lively streets."

    goal_text = f"\n  Current goal: {current_goal}" if current_goal else ""

    user_content = f"""Agent Profile:
  Archetype: {archetype}{goal_text}
  Personality: {personality_text}
  Needs: hunger={needs.get('hunger', 0.5):.2f}, energy={needs.get('energy', 1.0):.2f}, social={needs.get('social', 0.5):.2f}
  Mood: {cognition.get('mood', 'neutral')}, Curiosity: {cognition.get('curiosity', 0.7):.2f}, Fatigue: {cognition.get('fatigue', 0.0):.2f}
{top_places}
  Current Position: lon={current_position.get('lon', 0):.6f}, lat={current_position.get('lat', 0):.6f}{perception_text}{urgency_text}
```

---

### 2d. `Backend/LLM/Thinking/blocks/mobility_block.py` — pass new context to prompt

**Find this exact string:**
```python
        messages = mobility_decision_prompt(
            archetype=archetype,
            needs=needs,
            cognition=cognition,
            recent_history=history_text,
            current_position=position,
            candidates=prompt_cands,
            street_perception=street_perception,
        )
```

**Replace with:**
```python
        poi_beliefs = await self.memory.status.get("poi_beliefs", {})
        daily_plan = await self.memory.status.get("daily_plan", {})
        activities = daily_plan.get("activities", [])
        current_index = daily_plan.get("current_index", 0)
        current_goal = activities[current_index] if activities and current_index < len(activities) else ""
        urgent_need = kwargs.get("urgent_need")

        messages = mobility_decision_prompt(
            archetype=archetype,
            needs=needs,
            cognition=cognition,
            recent_history=history_text,
            current_position=position,
            candidates=prompt_cands,
            street_perception=street_perception,
            profile=profile,
            poi_beliefs=poi_beliefs,
            urgent_need=urgent_need,
            current_goal=current_goal,
        )
```

---

## Change 3 — Needs-Threshold Plan Interruption

No new LLM calls. Reads already-updated needs from memory and injects urgency into the existing mobility prompt.

### 3a. `Backend/LLM/Thinking/dispatcher.py` — add threshold check before MobilityBlock

**Find this exact string:**
```python
        # 3. MobilityBlock — LLM only if within per-step budget AND we need a new edge
        if needs_new_edge:
```

**Replace with:**
```python
        # Check for urgent needs — injected into mobility prompt (no extra LLM call)
        _needs = await self.memory.status.get("needs", {})
        urgent_need = None
        if _needs.get("hunger", 0.0) > 0.85:
            urgent_need = "hunger"
        elif _needs.get("energy", 1.0) < 0.15:
            urgent_need = "energy"
        elif _needs.get("social", 0.0) > 0.85:
            urgent_need = "social"

        # 3. MobilityBlock — LLM only if within per-step budget AND we need a new edge
        if needs_new_edge:
```

**Find this exact string:**
```python
            if use_llm:
                mobility_result = await self.mobility_block.run(
                    step=step, candidate_edges=candidate_edges,
                    street_perception=street_perception,
                )
```

**Replace with:**
```python
            if use_llm:
                mobility_result = await self.mobility_block.run(
                    step=step, candidate_edges=candidate_edges,
                    street_perception=street_perception,
                    urgent_need=urgent_need,
                )
```

---

## Change 4 — Daily Planning Block (once per agent)

### 4a. Create new file `Backend/LLM/Thinking/blocks/planning_block.py`

```python
"""
PlanningBlock — generates a one-shot daily activity plan for the agent.
Fires exactly once per agent instance (first dispatcher.run() call).
Stores the plan in KVMemory under "daily_plan".
"""
import logging

from LLM.Thinking.block import Block, BlockResult
from LLM.Thinking.prompts import daily_plan_prompt

logger = logging.getLogger(__name__)


class PlanningBlock(Block):
    """Generates a daily activity schedule via LLM. Runs once per agent lifetime."""

    async def run(self, step: int, **kwargs) -> BlockResult:
        profile = await self.memory.status.get("agent_profile", {})
        archetype = profile.get("archetype", "resident")
        needs = await self.memory.status.get("needs", {})

        messages = daily_plan_prompt(
            archetype=archetype,
            age=profile.get("age", 30),
            personality=profile.get("personality", {}),
            income=profile.get("income", "medium"),
            occupation=profile.get("occupation", "general"),
            needs=needs,
        )
        response = await self.llm.chat_json(messages)
        fallback = False

        if response and "activities" in response and isinstance(response["activities"], list):
            plan = {
                "wake_hour": int(response.get("wake_hour", 8)),
                "activities": [str(a) for a in response["activities"][:7]],
                "current_index": 0,
            }
        else:
            plan = {
                "wake_hour": 8,
                "activities": [
                    "explore neighbourhood", "find food", "rest",
                    "socialise", "explore more", "dinner", "evening walk",
                ],
                "current_index": 0,
            }
            fallback = True

        await self.memory.status.update("daily_plan", plan)

        logger.debug("Agent daily plan: %s", plan["activities"])
        return BlockResult(
            action="plan_created",
            params={"daily_plan": plan},
            reasoning=f"Daily plan for {archetype}: {plan['activities']}",
            fallback=fallback,
        )
```

---

### 4b. `Backend/LLM/Thinking/prompts.py` — add `daily_plan_prompt`

Append at the end of `prompts.py`:

```python

# ---------------------------------------------------------------------------
# PLANNING BLOCK prompts
# ---------------------------------------------------------------------------

PLANNING_SYSTEM = """You are generating a realistic daily activity plan for a pedestrian agent in Barcelona's Eixample district.
Respond with valid JSON only. No explanations outside the JSON object."""


def daily_plan_prompt(
    archetype: str,
    age: int,
    personality: dict,
    income: str,
    occupation: str,
    needs: dict,
) -> list[dict]:
    """Prompt to generate a one-shot daily schedule for the agent."""
    personality_text = (
        ", ".join(f"{k}={v}/3" for k, v in personality.items())
        if personality else "average across all traits"
    )
    user_content = f"""Agent profile:
  Archetype: {archetype}, Age: {age}, Income: {income}, Occupation: {occupation}
  Personality (1=low 2=medium 3=high): {personality_text}
  Starting needs: hunger={needs.get('hunger', 0.5):.2f}, energy={needs.get('energy', 1.0):.2f}, social={needs.get('social', 0.5):.2f}

Generate a realistic daily activity plan for this agent walking around Barcelona's Eixample district.
Provide exactly 7 short activity descriptions (5–8 words each), ordered chronologically.
Reflect their personality and role. Examples: "morning coffee at corner cafe", "browse supermarket on Gran Via", "sit in Jardins de la Universitat", "walk along Passeig de Gracia", "lunch at local restaurant".

Respond with JSON:
{{"wake_hour": <int 6-10>, "activities": ["<activity 1>", "<activity 2>", "<activity 3>", "<activity 4>", "<activity 5>", "<activity 6>", "<activity 7>"]}}"""
    return [_system(PLANNING_SYSTEM), _user(user_content)]
```

---

### 4c. `Backend/LLM/Thinking/dispatcher.py` — wire PlanningBlock in

**Find this exact import block:**
```python
from .block import BlockResult
from .blocks.mobility_block import MobilityBlock
from .blocks.needs_block import NeedsBlock
from .blocks.cognition_block import CognitionBlock
```

**Replace with:**
```python
from .block import BlockResult
from .blocks.mobility_block import MobilityBlock
from .blocks.needs_block import NeedsBlock
from .blocks.cognition_block import CognitionBlock
from .blocks.planning_block import PlanningBlock
```

**Find this exact string in `BlockDispatcher.__init__`:**
```python
        self.needs_block = NeedsBlock(llm_client, memory, ctx)
        self.cognition_block = CognitionBlock(llm_client, memory, ctx)
        self.mobility_block = MobilityBlock(llm_client, memory, ctx)
```

**Replace with:**
```python
        self.planning_block = PlanningBlock(llm_client, memory, ctx)
        self.needs_block = NeedsBlock(llm_client, memory, ctx)
        self.cognition_block = CognitionBlock(llm_client, memory, ctx)
        self.mobility_block = MobilityBlock(llm_client, memory, ctx)
        self._has_planned = False
```

**Find this exact string in `BlockDispatcher.run()`:**
```python
        # 1. NeedsBlock — always runs (cheap)
        needs_result = await self.needs_block.run(
```

**Replace with:**
```python
        # 0. PlanningBlock — runs exactly once per agent instance (not subject to LLM budget)
        if not self._has_planned:
            await self.planning_block.run(step=step)
            self._has_planned = True

        # 1. NeedsBlock — always runs (cheap)
        needs_result = await self.needs_block.run(
```

---

## Change 5 — Memory Reflection & Decay

No new LLM calls. Reuses the `summary` field already returned by the existing cognition LLM call.

### 5a. `Backend/LLM/Thinking/prompts.py` — extend `cognition_update_prompt`

**Find this function signature:**
```python
def cognition_update_prompt(
    archetype: str,
    current_cognition: dict,
    recent_history: str,
    step: int,
    streetview_perception: str = "",
) -> list[dict]:
```

**Replace with:**
```python
def cognition_update_prompt(
    archetype: str,
    current_cognition: dict,
    recent_history: str,
    step: int,
    streetview_perception: str = "",
    past_reflections: list | None = None,
) -> list[dict]:
```

**Find this exact string inside `cognition_update_prompt`:**
```python
    user_content = f"""Agent archetype: {archetype}
Simulation step: {step}
Current mental state:
```

**Replace with:**
```python
    reflections_text = ""
    if past_reflections:
        summaries = [r.get("summary", "") for r in past_reflections[-3:] if r.get("summary")]
        if summaries:
            reflections_text = "\nPast reflections (oldest to newest):\n" + "\n".join(f"  - {s}" for s in summaries) + "\n"

    user_content = f"""Agent archetype: {archetype}
Simulation step: {step}
Current mental state:
```

**Find this exact string inside `cognition_update_prompt`:**
```python
Recent experiences:
{recent_history}
```

**Replace with:**
```python
{reflections_text}Recent experiences:
{recent_history}
```

---

### 5b. `Backend/LLM/Thinking/blocks/cognition_block.py` — read reflections, pass to prompt, append summary

**Find this exact string:**
```python
        profile = await self.memory.status.get("agent_profile", {})
        asyncetype = profile.get("archetype", "resident")
        current_cognition = await self.memory.status.get("cognition_state", {})
```

**Replace with:**
```python
        profile = await self.memory.status.get("agent_profile", {})
        archetype = profile.get("archetype", "resident")
        current_cognition = await self.memory.status.get("cognition_state", {})
        past_reflections = await self.memory.status.get("reflections", [])
```

Note: this also fixes the existing `asyncetype` typo — the variable was named `asyncetype` on the original line but `archetype` everywhere else. Rename it to `archetype` here.

**Find this exact string:**
```python
        messages = cognition_update_prompt(
            archetype=archetype,
            current_cognition=current_cognition,
            recent_history=history_text,
            step=step,
            streetview_perception=perception_text,
        )
```

**Replace with:**
```python
        messages = cognition_update_prompt(
            archetype=archetype,
            current_cognition=current_cognition,
            recent_history=history_text,
            step=step,
            streetview_perception=perception_text,
            past_reflections=past_reflections,
        )
```

**Find this exact string:**
```python
        await self.memory.status.update("cognition_state", new_cognition)

        # Log to stream
```

**Replace with:**
```python
        await self.memory.status.update("cognition_state", new_cognition)

        # Append LLM summary as a reflection; decay to last 5 (no extra LLM call)
        if summary:
            reflections = await self.memory.status.get("reflections", [])
            reflections.append({"step": step, "summary": summary})
            if len(reflections) > 5:
                reflections = reflections[-5:]
            await self.memory.status.update("reflections", reflections)

        # Log to stream
```

---

## Execution Order

Apply changes in this exact order to avoid import errors:

1. `kv_memory.py` (schema additions — no imports needed)
2. `prompts.py` (extend 3 existing functions + add 1 new function)
3. Create `planning_block.py` (new file — imports from prompts.py)
4. `dispatcher.py` (imports planning_block.py)
5. `needs_block.py` (Kalman update — no new imports)
6. `cognition_block.py` (reflection logic — no new imports)
7. `mobility_block.py` (pass new kwargs — no new imports)
8. `model.py` (add 3 static methods, update _init_memory_sync and _init_memory)
9. `OSM_model.py` (same edits as step 8)

---

## Verification Steps

After implementing all changes, verify in order:

**Step 1 — Schema check (no server needed):**
```python
from Backend.LLM.Memory.kv_memory import DEFAULT_SCHEMA
assert "poi_beliefs" in DEFAULT_SCHEMA
assert "daily_plan" in DEFAULT_SCHEMA
assert "reflections" in DEFAULT_SCHEMA
assert "personality" in DEFAULT_SCHEMA["agent_profile"]
```

**Step 2 — Import check:**
```bash
cd Backend
python -c "from LLM.Thinking.dispatcher import BlockDispatcher; print('OK')"
python -c "from LLM.Thinking.blocks.planning_block import PlanningBlock; print('OK')"
python -c "from Agent.model import CityAgent; print('OK')"
```

**Step 3 — Agent init check (no LLM needed, set LLM_CALLS_PER_STEP=0):**
```bash
LLM_CALLS_PER_STEP=0 python -c "
from Agent.model import CityModel
m = CityModel(num_agents=2)
agent = m.city_agents[0]
profile = agent.memory.status._data['agent_profile']
assert 'personality' in profile, 'personality missing'
assert 'income' in profile, 'income missing'
assert 'poi_beliefs' in agent.memory.status._data, 'poi_beliefs missing'
assert 'daily_plan' in agent.memory.status._data, 'daily_plan missing'
assert 'reflections' in agent.memory.status._data, 'reflections missing'
print('All schema keys present:', list(profile.keys()))
"
```

**Step 4 — Single step smoke test (rule-based mode, no LLM):**
```bash
LLM_CALLS_PER_STEP=0 python -c "
import asyncio
from Agent.model import CityModel
m = CityModel(num_agents=5)
asyncio.run(m.async_step())
print('Step completed. Agents still alive:', len(m.city_agents))
"
```

**Step 5 — LLM integration test (requires running LLM):**
Start the server normally and verify:
- `GET /api/agents` still returns all agents with `location` field intact
- `GET /api/agents/{id}/memory` now includes `personality`, `poi_beliefs`, `daily_plan`, `reflections` keys
- `GET /api/llm/stats` shows token counts incrementing as expected

**Step 6 — Regression check:**
After 10 steps with LLM enabled, confirm:
- `dispatcher._has_planned == True` for every agent (plan fired exactly once)
- `poi_beliefs` is non-empty for agents that visited amenities
- `reflections` list is non-empty (grows every COGNITION_INTERVAL steps up to 5)
- Existing `visited_edges`, `needs`, `cognition_state` keys still update normally