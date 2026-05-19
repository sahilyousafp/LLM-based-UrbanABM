"""
Prompt templates for agent thinking blocks.
Each template is a callable that fills named placeholders and returns a messages list.
"""
from typing import Any, Optional


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
    destination: dict | None = None,
    path_hint_edge_id: int | None = None,
    preferences: list | None = None,
    adherence: float = 0.5,
    adherence_failed: bool = False,
) -> list[dict]:
    """
    Prompt asking the LLM to choose the next movement destination.
    candidates: list of {"edge_id": int, "direction": str, "amenities": list[str], "description": str}
    street_perception: optional dict with walkability, vegetation, pedestrian_activity, etc.
    destination: agent's persistent target {"name", "amenity_type", "lon", "lat"}
    path_hint_edge_id: edge_id of the Dijkstra-optimal next step toward destination
    adherence_failed: True means the agent rolled to deviate — it is now free to explore
    """
    candidates_text = "\n".join(
        f"  [{i}] edge_id={c['edge_id']} dir={c.get('direction','fwd')} "
        f"amenities=[{', '.join(c.get('amenities', [])[:3])}] "
        f"{'env=[' + c['perception'][:100] + '] ' if c.get('perception') else ''}"
        f"desc={c.get('description', '')}"
        f"{' [SHORTEST PATH TO DESTINATION]' if c['edge_id'] == path_hint_edge_id else ''}"
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

    destination_text = ""
    if destination and destination.get("name"):
        destination_text = (
            f"\n\nTarget Destination: {destination['name']} "
            f"(type: {destination.get('amenity_type', 'unknown')}) "
            f"at lon={destination.get('lon', 0):.6f}, lat={destination.get('lat', 0):.6f}. "
            f"This is your primary goal — navigate toward it."
        )

    # Deviation context: explain WHY the agent is free to choose
    deviation_context = ""
    if adherence_failed:
        deviation_context = (
            f"\n\n** You rolled to deviate from your proposed path (adherence={adherence:.1f}). "
            f"You are now free to choose based on your current needs, nearby amenities, and preferences. "
            f"Consider: Do you have low energy or hunger that a nearby amenity could satisfy? "
            f"Does a candidate edge lead through an environment that matches your archetype's preferences? "
            f"The [SHORTEST PATH TO DESTINATION] candidate is still available if you prefer to stay on route. **"
        )

    user_content = f"""Agent Profile:
  Archetype: {archetype}
  Needs: hunger={needs.get('hunger', 0.5):.2f}, energy={needs.get('energy', 1.0):.2f}, social={needs.get('social', 0.5):.2f}, comfort={needs.get('comfort', 0.7):.2f}
  Mood: {cognition.get('mood', 'neutral')}, Curiosity: {cognition.get('curiosity', 0.7):.2f}, Fatigue: {cognition.get('fatigue', 0.0):.2f}
  Current Position: lon={current_position.get('lon', 0):.6f}, lat={current_position.get('lat', 0):.6f}{perception_text}{destination_text}{deviation_context}

Recent Movement History:
{recent_history}

Candidate Edges/Destinations:
{candidates_text}

Choose the index of the best candidate for this agent to move to next.
Your path adherence weight is {adherence:.1f} — this determines how often you follow the [SHORTEST PATH TO DESTINATION].
Your preferences: {', '.join(preferences) if preferences else 'none'}.
Deviate from the shortest path when nearby streets or amenities match your preferences; otherwise stay on route.
Also consider the street environment and how it suits your archetype.

Respond with JSON:
{{"choice": <index 0-{len(candidates)-1}>, "reasoning": "<one sentence why>"}}"""

    return [_system(MOBILITY_SYSTEM), _user(user_content)]


# ---------------------------------------------------------------------------
# NEEDS BLOCK prompts
# ---------------------------------------------------------------------------

NEEDS_SYSTEM = """You are evaluating how a pedestrian agent's needs are affected by their environment.
Respond with valid JSON only."""

VISUAL_SATISFACTION_SYSTEM = """You are evaluating how the visual street environment affects a pedestrian agent's needs.
Respond with valid JSON only."""

def visual_satisfaction_prompt(
    archetype: str,
    needs: dict,
    cognition: dict,
    street_perception: dict,
) -> list[dict]:
    """
    Prompt to evaluate how the visual street environment affects the agent's 3 needs.
    Considers buildings, vegetation, pedestrian activity, and lighting/atmosphere.
    Modulated by archetype and current cognition state (mood, fatigue, curiosity).
    """
    # Build scene description from street perception fields
    scene_fields = [
        ("scene_overview",      "Scene"),
        ("buildings",           "Buildings"),
        ("vegetation",          "Vegetation"),
        ("pedestrian_activity", "Pedestrian activity"),
        ("lighting_atmosphere", "Lighting/atmosphere"),
    ]
    lines = []
    for key, label in scene_fields:
        val = street_perception.get(key, "")
        if val and val.strip().lower() not in ("unknown", ""):
            lines.append(f"  {label}: {val}")
    
    perception_text = "\n".join(lines) if lines else "  No detailed visual data available"
    
    user_content = f"""Agent archetype: {archetype}
Current needs: hunger={needs.get('hunger', 0.5):.2f}, energy={needs.get('energy', 1.0):.2f}, social={needs.get('social', 0.5):.2f}, comfort={needs.get('comfort', 0.7):.2f}
Current mental state: mood={cognition.get('mood', 'neutral')}, curiosity={cognition.get('curiosity', 0.7):.2f}, fatigue={cognition.get('fatigue', 0.0):.2f}

Street environment visible:
{perception_text}

How does being in this physical space affect the agent's 4 needs?
Consider:
  - Beautiful/interesting architecture energizes curious agents
  - Green spaces restore energy and improve mood
  - Lively pedestrian areas satisfy social needs
  - Poor lighting/run-down areas increase fatigue, drain energy
  - A tired agent loses energy faster in unstimulating environments
  - A social-mood agent gains more from busy streets
  - Different archetypes respond differently: tourists love interesting buildings, students seek lively areas, residents prefer familiar comfort, commuters value efficient pleasant routes
  - COMFORT is primarily driven by visual environment quality: well-maintained buildings, greenery, good lighting, pleasant pedestrian activity → comfort_delta positive (up to +0.4); run-down areas, dark streets, empty/desolate spaces, visual noise → comfort_delta negative (down to -0.3)

Provide deltas 0.0-1.0 (positive = need satisfied/reduced, negative = need increased/worsened).
Note: hunger_delta is typically small from visual alone (distraction effect); energy_delta can be positive from restoration; social_delta reflects social vibrancy; comfort_delta is the primary metric here — rate the visual environment quality honestly.

Respond with JSON:
{{"hunger_delta": <float>, "energy_delta": <float>, "social_delta": <float>, "comfort_delta": <float>, "reasoning": "<one sentence: why this space affects needs this way>"}}"""

    return [_system(VISUAL_SATISFACTION_SYSTEM), _user(user_content)]


def needs_evaluation_prompt(
    archetype: str,
    needs: dict,
    cognition: dict,
    amenity_name: str,
    amenity_type: str,
    street_perception: Optional[dict] = None,
) -> list[dict]:
    """
    Prompt to evaluate how much visiting this amenity satisfies agent needs.
    Now includes cognition state and surrounding street perception context.
    """
    # Build scene description from street perception fields
    perception_text = ""
    if street_perception:
        scene_fields = [
            ("scene_overview",      "Scene"),
            ("buildings",           "Buildings"),
            ("vegetation",          "Vegetation"),
            ("street_furniture",    "Street furniture"),
            ("signage",             "Signage"),
            ("pedestrian_activity", "Pedestrian activity"),
        ]
        lines = []
        for key, label in scene_fields:
            val = street_perception.get(key, "")
            if val and val.strip().lower() not in ("unknown", ""):
                lines.append(f"  {label}: {val}")
        if lines:
            perception_text = "\n\nSurrounding street scene:\n" + "\n".join(lines)

    user_content = f"""Agent archetype: {archetype}
Current needs: hunger={needs.get('hunger', 0.5):.2f}, energy={needs.get('energy', 1.0):.2f}, social={needs.get('social', 0.5):.2f}, comfort={needs.get('comfort', 0.7):.2f}
Current mental state: mood={cognition.get('mood', 'neutral')}, curiosity={cognition.get('curiosity', 0.7):.2f}, fatigue={cognition.get('fatigue', 0.0):.2f}
Visited amenity: "{amenity_name}" (type: {amenity_type}){perception_text}

How much does this visit satisfy each need? Provide values 0.0-1.0 (0=no satisfaction, 1=fully satisfied).
Consider:
  - The amenity type and what it typically provides
  - The surrounding street environment context
  - The agent's current mental state (mood affects satisfaction)
  - Archetype preferences (e.g., students love cafes, tourists love attractions)
  - comfort_delta is small here (amenities are secondary to visual environment for comfort)

Respond with JSON:
{{"hunger_delta": <float>, "energy_delta": <float>, "social_delta": <float>, "comfort_delta": <float>, "activity": "<what agent does there>"}}"""

    return [_system(NEEDS_SYSTEM), _user(user_content)]


# ---------------------------------------------------------------------------
# COGNITION BLOCK prompts
# ---------------------------------------------------------------------------

COGNITION_SYSTEM = """You are updating the internal mental state of a pedestrian agent based on their recent experiences.
Respond with valid JSON only."""

def cognition_update_prompt(
    archetype: str,
    current_cognition: dict,
    current_needs: dict,
    recent_history: str,
    step: int,
    streetview_perception: str = "",
) -> list[dict]:
    """
    Prompt to update agent's cognitive/emotional state based on recent experiences.
    Now includes current needs state to model need-mood interactions.
    """
    perception_section = ""
    if streetview_perception:
        perception_section = f"""
Scene description at current location (from visual analysis):
{streetview_perception}
"""

    user_content = f"""Agent archetype: {archetype}
Simulation step: {step}
Current needs: hunger={current_needs.get('hunger', 0.5):.2f}, energy={current_needs.get('energy', 1.0):.2f}, social={current_needs.get('social', 0.5):.2f}, comfort={current_needs.get('comfort', 0.7):.2f}
Current mental state:
  mood: {current_cognition.get('mood', 'neutral')}
  curiosity: {current_cognition.get('curiosity', 0.7):.2f}
  fatigue: {current_cognition.get('fatigue', 0.0):.2f}
{perception_section}
Recent experiences:
{recent_history}

Update the agent's mental state based on these experiences, physical needs, and the surrounding environment.
Consider how physical needs affect psychology:
  - High hunger can cause irritability (worse mood, higher fatigue)
  - Low energy increases fatigue and reduces curiosity
  - High social need with no outlet causes frustration
  - Well-satisfied needs improve mood and curiosity
Environmental effects:
  - A lively, green, well-maintained scene may improve mood and curiosity
  - An empty, run-down, or monotonous environment may increase boredom or fatigue
  - Beautiful architecture energizes curious agents
  - Green spaces restore energy and improve mood
  - Lively pedestrian areas satisfy social needs
Archetype responses:
  - Tourists are energised by interesting architecture and activity
  - Residents find comfort in familiar, quiet streets
  - Students seek social, lively areas
  - Commuters value efficient, pleasant routes
Mood options: happy, neutral, tired, curious, bored, energised, social, focused
Curiosity and fatigue are floats 0.0-1.0.

Respond with JSON:
{{"mood": "<mood>", "curiosity": <float>, "fatigue": <float>, "summary": "<one sentence narrative>"}}"""

    return [_system(COGNITION_SYSTEM), _user(user_content)]
