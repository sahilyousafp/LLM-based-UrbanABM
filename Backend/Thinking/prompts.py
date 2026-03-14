"""
Prompt templates for agent thinking blocks.
Each template is a callable that fills named placeholders and returns a messages list.
"""
from typing import Any


def _system(content: str) -> dict:
    return {"role": "system", "content": content}


def _user(content: str) -> dict:
    return {"role": "user", "content": content}


# ---------------------------------------------------------------------------
# MOBILITY BLOCK prompts
# ---------------------------------------------------------------------------

MOBILITY_SYSTEM = """You are the decision-making core of a pedestrian agent in Barcelona's Eixample district.
Your role is to choose which street edge or amenity to move to next, based on the agent's profile, needs, and recent history.
Always respond with valid JSON only. No explanations outside the JSON object."""

def mobility_decision_prompt(
    archetype: str,
    needs: dict,
    cognition: dict,
    recent_history: str,
    current_position: dict,
    candidates: list[dict],
    street_perception: dict | None = None,
) -> list[dict]:
    """
    Prompt asking the LLM to choose the next movement destination.
    candidates: list of {"edge_id": int, "direction": str, "amenities": list[str], "description": str}
    street_perception: optional dict with walkability, vegetation, pedestrian_activity, etc.
    """
    candidates_text = "\n".join(
        f"  [{i}] edge_id={c['edge_id']} dir={c.get('direction','fwd')} "
        f"amenities=[{', '.join(c.get('amenities', [])[:3])}] "
        f"{'env=[' + c['perception'] + '] ' if c.get('perception') else ''}"
        f"desc={c.get('description', '')}"
        for i, c in enumerate(candidates)
    )

    # Build scene description block from text fields
    perception_text = ""
    if street_perception:
        scene_fields = [
            ("scene_overview",      "Scene"),
            ("buildings",           "Buildings"),
            ("vegetation",          "Vegetation"),
            ("pedestrian_activity", "Pedestrian activity"),
            ("lighting_atmosphere", "Lighting/atmosphere"),
            ("as_resident",         "Resident perspective"),
            ("as_commuter",         "Commuter perspective"),
            ("as_tourist",          "Tourist perspective"),
            ("as_student",          "Student perspective"),
        ]
        lines = []
        for key, label in scene_fields:
            val = street_perception.get(key, "")
            if val and val.strip().lower() != "unknown":
                lines.append(f"  {label}: {val}")
        if lines:
            perception_text = "\n\nScene description at current location (from visual analysis):\n" + "\n".join(lines)

    user_content = f"""Agent Profile:
  Archetype: {archetype}
  Needs: hunger={needs.get('hunger', 0.5):.2f}, energy={needs.get('energy', 1.0):.2f}, social={needs.get('social', 0.5):.2f}
  Mood: {cognition.get('mood', 'neutral')}, Curiosity: {cognition.get('curiosity', 0.7):.2f}, Fatigue: {cognition.get('fatigue', 0.0):.2f}
  Current Position: lon={current_position.get('lon', 0):.6f}, lat={current_position.get('lat', 0):.6f}{perception_text}

Recent Movement History:
{recent_history}

Candidate Edges/Destinations:
{candidates_text}

Choose the index of the best candidate for this agent to move to next.
Consider the agent's archetype behaviour:
  - resident: prefers familiar streets, grocery/pharmacy/home areas
  - commuter: moves efficiently, prefers direct routes with less revisiting
  - tourist: prefers new/unvisited streets, cafes, attractions, interesting areas
  - student: social, prefers cafes, parks, libraries, lively streets
Also consider the street environment: agents respond to the scene around them. Tourists are drawn to lively, green, interesting areas; tired agents prefer quieter, less stimulating streets; residents seek familiar comfortable surroundings.

Respond with JSON:
{{"choice": <index 0-{len(candidates)-1}>, "reasoning": "<one sentence why>"}}"""

    return [_system(MOBILITY_SYSTEM), _user(user_content)]


# ---------------------------------------------------------------------------
# NEEDS BLOCK prompts
# ---------------------------------------------------------------------------

NEEDS_SYSTEM = """You are evaluating whether a pedestrian agent's visit to an amenity satisfies their needs.
Respond with valid JSON only."""

def needs_evaluation_prompt(
    archetype: str,
    needs: dict,
    amenity_name: str,
    amenity_type: str,
) -> list[dict]:
    """Prompt to evaluate how much visiting this amenity satisfies agent needs."""
    user_content = f"""Agent archetype: {archetype}
Current needs: hunger={needs.get('hunger', 0.5):.2f}, energy={needs.get('energy', 1.0):.2f}, social={needs.get('social', 0.5):.2f}
Visited amenity: "{amenity_name}" (type: {amenity_type})

How much does this visit satisfy each need? Provide values 0.0-1.0 (0=no satisfaction, 1=fully satisfied).
Also give a brief description of what the agent does there.

Respond with JSON:
{{"hunger_delta": <float>, "energy_delta": <float>, "social_delta": <float>, "activity": "<what agent does>"}}"""

    return [_system(NEEDS_SYSTEM), _user(user_content)]


# ---------------------------------------------------------------------------
# COGNITION BLOCK prompts
# ---------------------------------------------------------------------------

COGNITION_SYSTEM = """You are updating the internal mental state of a pedestrian agent based on their recent experiences.
Respond with valid JSON only."""

def cognition_update_prompt(
    archetype: str,
    current_cognition: dict,
    recent_history: str,
    step: int,
    streetview_perception: str = "",
) -> list[dict]:
    """Prompt to update agent's cognitive/emotional state based on recent experiences."""
    perception_section = ""
    if streetview_perception:
        perception_section = f"""
Scene description at current location (from visual analysis):
{streetview_perception}
"""

    user_content = f"""Agent archetype: {archetype}
Simulation step: {step}
Current mental state:
  mood: {current_cognition.get('mood', 'neutral')}
  curiosity: {current_cognition.get('curiosity', 0.7):.2f}
  fatigue: {current_cognition.get('fatigue', 0.0):.2f}
{perception_section}
Recent experiences:
{recent_history}

Update the agent's mental state based on these experiences and the surrounding environment.
A lively, green, well-maintained scene may improve mood and curiosity; an empty, run-down, or monotonous environment may increase boredom or fatigue.
Different archetypes respond differently: tourists are energised by interesting architecture and activity; residents find comfort in familiar, quiet streets; students seek social, lively areas.
Mood options: happy, neutral, tired, curious, bored, energised, social, focused
Curiosity and fatigue are floats 0.0-1.0.

Respond with JSON:
{{"mood": "<mood>", "curiosity": <float>, "fatigue": <float>, "summary": "<one sentence narrative>"}}"""

    return [_system(COGNITION_SYSTEM), _user(user_content)]
