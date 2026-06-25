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
    path_hint_direction: str | None = None,
    preferences: list | None = None,
    explore_budget: int = 1,
    free_steps_remaining: int = 0,
    plan_context: dict | None = None,
    visited_counts: dict | None = None,
    steps_to_destination: int | None = None,
    nav_mode: str = "both",
    next_waypoint: dict | None = None,
    nearby_agents: list | None = None,
    nearby_transit: list | None = None,
    memory_context: str = "",
    time_of_day: str = "",
) -> list[dict]:
    """
    Prompt asking the LLM to choose the next movement destination.
    candidates: list of {"edge_id": int, "direction": str, "amenities": list[str], "description": str}
    street_perception: optional dict with walkability, vegetation, pedestrian_activity, etc.
    destination: agent's persistent target {"name", "amenity_type", "lon", "lat"}
    path_hint_edge_id: edge_id of the Dijkstra-optimal next step toward destination
    explore_budget: total free steps per forced-Dijkstra cycle (0=commuter, 1=resident, 2=student, 3=tourist)
    free_steps_remaining: free steps left in the current exploration window after this one
    plan_context: optional dict with {"goal", "perception_preferences", "perception_avoid", "active_target"}
    visited_counts: dict mapping edge_id (str) to visit count — shown to LLM to discourage revisits
    steps_to_destination: BFS hop count from current node to target node (None if unknown/unreachable)
    nav_mode: "gps" (exact path label) | "direction_sense" (compass bearing) | "both" | "none"
    """
    vc = visited_counts or {}

    # Archetype-specific urgency thresholds in metres (Euclidean to destination).
    # Kept small so agents explore freely for most of the journey and only converge
    # in the final approach. Eixample block ≈ 113m, so 60m ≈ half a block.
    # Tourist: 3 free steps/cycle → needs a slightly wider window to converge in time.
    if archetype == "tourist":
        _almost_there_m = 60    # m — start forcing GPS convergence
        _getting_close_m = 150  # m — start suppressing novelty bias + urgency text
    elif archetype == "resident":
        _almost_there_m = 40
        _getting_close_m = 100
    elif archetype == "student":
        _almost_there_m = 50
        _getting_close_m = 120
    else:  # commuter or unknown (explore_budget=0 anyway, thresholds moot)
        _almost_there_m = 50
        _getting_close_m = 120

    def _visit_tag(edge_id, direction):
        # Pedestrian movement: direction is irrelevant — suppress visit penalty for
        # the GPS edge regardless of which direction it appears in the candidate list.
        if edge_id == path_hint_edge_id and nav_mode in ('gps', 'both'):
            return ""
        # Near destination — reaching the goal overrides exploration novelty
        # dist_m is computed in the destination block below; candidates_text is built after it
        if dist_m is not None and dist_m <= _getting_close_m:
            return ""
        n = vc.get(str(edge_id), 0)
        if n == 0:
            return " [NEW]"
        if n >= 2:
            return f" [visited {n}x — strongly avoid revisiting]"
        return f" [visited {n}x]"

    # Build scene description block from text fields
    perception_text = ""
    if street_perception:
        scene_fields = [
            ("scene",             "Scene"),
            ("spatial_character", "Spatial character"),
            ("greenery",          "Greenery"),
            ("crowdedness",       "Crowdedness"),
            ("lighting",          "Lighting"),
            ("street_amenities",  "Street amenities"),
            ("visible_text",      "Signage/text"),
        ]
        lines = []
        for key, label in scene_fields:
            val = street_perception.get(key, "")
            if val and str(val).strip().lower() != "unknown":
                lines.append(f"  {label}: {val}")
        # Add camera facing direction if available (from StreetPLM metadata)
        heading = street_perception.get("heading")
        if heading is not None:
            _compass_labels = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
            hcompass = _compass_labels[int((heading + 22.5) / 45) % 8]
            lines.append(f"  Camera facing: {hcompass} ({heading:.0f}°) — direction shown in the street view image")
        if lines:
            perception_text = "\n\nScene description at current location (from visual analysis):\n" + "\n".join(lines)

    # Always compute bearing/compass when destination exists and not yet visited.
    # After arrival, destination["visited"]=True — skip so dist_m-based visit suppression
    # and urgency text stop anchoring the agent to the old destination.
    destination_text = ""
    compass = None
    dist_m = None
    if destination and destination.get("name") and not destination.get("visited"):
        import math as _math
        dlon = destination.get('lon', 0) - current_position.get('lon', 0)
        dlat = destination.get('lat', 0) - current_position.get('lat', 0)
        dist_m = _math.sqrt(
            (dlon * 111320 * _math.cos(_math.radians(current_position.get('lat', 0)))) ** 2
            + (dlat * 110540) ** 2
        )
        bearing_deg = (_math.degrees(_math.atan2(dlon, dlat)) + 360) % 360
        compass = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][
            int((bearing_deg + 22.5) / 45) % 8
        ]
        if nav_mode in ("direction_sense", "both"):
            # In the urgency zone, the GPS label is more precise than the Euclidean
            # compass bearing (the Dijkstra path may temporarily go against the bearing
            # to route around the block). Suppress the directional suggestion so it
            # doesn't contradict the [SHORTEST PATH TO DESTINATION] label.
            in_urgency_zone = dist_m <= _getting_close_m
            if nav_mode == "both" and in_urgency_zone:
                destination_text = (
                    f"\n\nTarget Destination: {destination['name']} "
                    f"(type: {destination.get('amenity_type', 'unknown')}) "
                    f"— approximately {dist_m:.0f}m away ({compass} direction). "
                    f"This is your primary goal. Follow the [SHORTEST PATH TO DESTINATION] label."
                )
            else:
                destination_text = (
                    f"\n\nTarget Destination: {destination['name']} "
                    f"(type: {destination.get('amenity_type', 'unknown')}) "
                    f"— approximately {dist_m:.0f}m to the {compass}. "
                    f"This is your primary goal. During free steps, prefer edges heading {compass} "
                    f"unless a pressing need or genuinely interesting feature draws you elsewhere."
                )
        else:
            destination_text = (
                f"\n\nTarget Destination: {destination['name']} "
                f"(type: {destination.get('amenity_type', 'unknown')}) "
                f"— approximately {dist_m:.0f}m away. "
                f"This is your primary goal."
            )

    # Bearing to the next Dijkstra waypoint node — reflects actual walkable direction on
    # the street grid rather than the straight-line bearing to the final destination,
    # which can conflict with network topology (e.g. must go east to later reach north).
    waypoint_compass = None
    if next_waypoint and not (destination and destination.get("visited")):
        import math as _math2
        wp_dlon = next_waypoint.get('lon', 0) - current_position.get('lon', 0)
        wp_dlat = next_waypoint.get('lat', 0) - current_position.get('lat', 0)
        if wp_dlon != 0 or wp_dlat != 0:
            wp_bearing = (_math2.degrees(_math2.atan2(wp_dlon, wp_dlat)) + 360) % 360
            waypoint_compass = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][
                int((wp_bearing + 22.5) / 45) % 8
            ]

    # candidates_text built here so _visit_tag can use dist_m (computed above)
    candidates_text = "\n".join(
        f"  [{i}] edge_id={c['edge_id']} dir={c.get('direction','fwd')} "
        f"amenities=[{', '.join(c.get('amenities', [])[:3])}] "
        f"{'env=[' + c['perception'][:220] + '] ' if c.get('perception') else ''}"
        f"desc={c.get('description', '')}"
        f"{_visit_tag(c['edge_id'], c.get('direction', 'forward'))}"
        f"{' [SHORTEST PATH TO DESTINATION]' if c['edge_id'] == path_hint_edge_id and nav_mode in ('gps', 'both') else ''}"
        for i, c in enumerate(candidates)
    )

    # Urgency label — show real distance in metres (more meaningful than hop count)
    dist_label = ""
    if dist_m is not None:
        dist_label = f" ({dist_m:.0f}m to destination)"

    # Nav-mode-aware convergence hint used in urgency text.
    # When path_hint_edge_id is None (free steps), fall back to compass direction so the
    # GPS label reference in deviation_context doesn't point to a non-existent label.
    if path_hint_edge_id is not None and nav_mode in ("gps", "both"):
        convergence_hint = "[SHORTEST PATH TO DESTINATION]"
    elif compass:
        convergence_hint = f"edges heading {compass}"
    else:
        convergence_hint = "your destination"

    # Exploration context: free-step language scaled by real distance to destination
    if explore_budget > 0:
        d = dist_m  # Euclidean metres — consistent regardless of edge length

        if d is not None and d <= _almost_there_m:
            deviation_context = (
                f"\n\n** ALMOST THERE{dist_label} — you are very close to your destination. "
                f"Take {convergence_hint} now. Do not detour. **"
            )
        elif d is not None and d <= _getting_close_m:
            if free_steps_remaining > 0:
                deviation_context = (
                    f"\n\n** GETTING CLOSE{dist_label} — {free_steps_remaining} free step(s) left. "
                    f"Lean strongly toward {convergence_hint}. "
                    f"Only detour if a need is urgent (hunger > 0.7 or energy < 0.3). **"
                )
            else:
                deviation_context = (
                    f"\n\n** LAST FREE STEP — destination is close{dist_label}. "
                    f"Take {convergence_hint} unless a critical need demands otherwise. **"
                )
        elif free_steps_remaining > 0:
            _hint_dir = waypoint_compass or compass   # waypoint preferred: grid-aware; compass: straight-line fallback
            _dir_hint = (
                f" Keep broadly heading toward {_hint_dir} — "
                f"side streets and perpendicular turns are fine, "
                f"but avoid edges taking you clearly away from your destination."
            ) if _hint_dir else ""
            deviation_context = (
                f"\n\n** FREE EXPLORATION STEP{dist_label} — {free_steps_remaining} free step(s) left. "
                f"Follow your curiosity, satisfy a need, or enjoy an interesting environment.{_dir_hint} "
                f"Your destination is still your goal — don't stray so far that reaching it becomes very hard. **"
            )
        else:
            deviation_context = (
                f"\n\n** LAST FREE STEP{dist_label} before a forced destination move next turn. "
                f"Make it count — satisfy a need or see something interesting nearby. "
                f"Strongly consider {convergence_hint} to stay on track. **"
            )
    else:
        deviation_context = ""

    # Build plan context block
    plan_text = ""
    if plan_context and plan_context.get("goal"):
        goal = plan_context["goal"]
        prefs = plan_context.get("perception_preferences", [])
        avoid = plan_context.get("perception_avoid", [])
        active_target = plan_context.get("active_target")

        time_of_day = plan_context.get("time_of_day", "")
        phase_header = f"{goal} ({time_of_day})" if time_of_day else goal
        lines = [f"\n\nCurrent Plan Phase: {phase_header}"]
        if prefs:
            lines.append(f"  Prefer streets with: {', '.join(prefs)}")
        if avoid:
            avoid_strs = [
                f"{a.get('field', '?')} is {a.get('value', '?')}" if isinstance(a, dict) else str(a)
                for a in avoid
            ]
            lines.append(f"  Avoid streets with: {', '.join(avoid_strs)}")
        if active_target:
            target_name = active_target.get("name", "unknown")
            target_type = active_target.get("type", "")
            target_dist = active_target.get("dist", 0)
            lines.append(f"  Active target: {target_name} ({target_type}) — {target_dist:.0f}m away")
        plan_text = "\n".join(lines)

    # Perception-guided free-step instruction: fires only on genuine free exploration,
    # never overrides GPS or critical-need rules.
    perc_rule = ""
    if explore_budget > 0 and free_steps_remaining > 0 and street_perception:
        _arch_guidance = {
            "tourist":  "Prefer edges whose env=[...] mentions interesting architecture, street art, outdoor cafes, or lively pedestrian activity — these energise tourists. Avoid featureless corridors.",
            "resident": "Prefer edges whose env=[...] describes quiet, well-maintained residential streets with greenery or good lighting. Avoid busy, noisy, or run-down stretches.",
            "student":  "Prefer edges whose env=[...] mentions shade trees, outdoor seating, or busy social areas. Lively but comfortable environments suit students.",
            "commuter": "Prefer edges whose env=[...] appears efficient and pleasant — well-lit, direct, not congested. Avoid detours into narrow or unclear streets.",
        }
        guidance = _arch_guidance.get(archetype, "Prefer edges whose env=[...] suggests pleasant, well-maintained surroundings.")
        perc_rule = f"\nPerception-guided free step: {guidance}"

    # Build nearby agents line for the prompt
    nearby_agents_text = ""
    if nearby_agents:
        from collections import Counter
        arch_counts = Counter(a.get("archetype", "unknown") for a in nearby_agents)
        parts = [f"{count} {arch}" for arch, count in sorted(arch_counts.items())]
        nearby_agents_text = f"\n  Nearby agents: {', '.join(parts)} within 55m"

    # Build transit stops line for the prompt
    nearby_transit_text = ""
    if nearby_transit:
        stop_parts = []
        for s in nearby_transit[:3]:
            name = s.get("name") or "Transit stop"
            dist = int(s.get("dist_m", 0))
            routes = s.get("routes", "")
            routes_str = f" ({routes})" if routes else ""
            stop_parts.append(f"'{name}'{routes_str} {dist}m")
        nearby_transit_text = f"\n  Transit stops: {', '.join(stop_parts)}"

    memory_section = f"\n\nLong-term Memory:\n{memory_context}" if memory_context else ""
    time_section = f"\n  Time of day: {time_of_day}" if time_of_day else ""

    user_content = f"""Agent Profile:
  Archetype: {archetype}{time_section}
  Needs: hunger={needs.get('hunger', 0.5):.2f}, energy={needs.get('energy', 1.0):.2f}, social={needs.get('social', 0.5):.2f}, comfort={needs.get('comfort', 0.7):.2f}
  Mood: {cognition.get('mood', 'neutral')}, Curiosity: {cognition.get('curiosity', 0.7):.2f}, Fatigue: {cognition.get('fatigue', 0.0):.2f}
  Current Position: lon={current_position.get('lon', 0):.6f}, lat={current_position.get('lat', 0):.6f}{nearby_agents_text}{nearby_transit_text}{perception_text}{destination_text}{deviation_context}{plan_text}{memory_section}

Recent Movement History:
{recent_history}

Candidate Edges/Destinations:
{candidates_text}

Choose the index of the best candidate for this agent to move to next.
Your preferences: {', '.join(preferences) if preferences else 'none'}.
On free steps: explore streets and amenities that match your archetype and needs. Strongly prefer [NEW] edges over revisited ones — edges marked [visited 2x+] should almost never be chosen again.
If comfort < 0.4, prefer edges toward parks, plazas, or streets with greenery. If hunger > 0.7 or energy < 0.3, prioritise edges near relevant amenities.{perc_rule}
{"GPS RULE: If any candidate shows [SHORTEST PATH TO DESTINATION], you MUST choose it. Visit counts, amenity preferences, and archetype interests are NOT valid reasons to skip it. The only permitted exception is a critical survival need: hunger > 0.9 or energy < 0.1. Restaurants, shops, or curiosity do not qualify." if nav_mode in ('gps', 'both') else f"Prefer edges heading {compass or 'toward destination'} to stay on track — you can always explore on the next free step." if nav_mode == 'direction_sense' else "Keep your destination in mind even while exploring freely."}
{"IMPORTANT: A nearby amenity whose type matches your destination (e.g. a pharmacy near an edge when your target is a pharmacy) is NOT your destination. The amenity list shows what is close to each edge — it does not mean the edge leads to your specific named target. Only [SHORTEST PATH TO DESTINATION] points to the actual place you are trying to reach." if destination and destination.get("name") and not destination.get("visited") and nav_mode in ('gps', 'both') else ""}

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
    time_of_day: str = "",
) -> list[dict]:
    """
    Prompt to evaluate how the visual street environment affects the agent's 3 needs.
    Considers buildings, vegetation, pedestrian activity, and lighting/atmosphere.
    Modulated by archetype and current cognition state (mood, fatigue, curiosity).
    """
    # Build scene description from street perception fields
    scene_fields = [
        ("scene",             "Scene"),
        ("spatial_character", "Spatial character"),
        ("greenery",          "Greenery"),
        ("crowdedness",       "Crowdedness"),
        ("lighting",          "Lighting"),
    ]
    lines = []
    for key, label in scene_fields:
        val = street_perception.get(key, "")
        if val and str(val).strip().lower() not in ("unknown", ""):
            lines.append(f"  {label}: {val}")
    
    perception_text = "\n".join(lines) if lines else "  No detailed visual data available"
    
    time_line = f"\nTime of day: {time_of_day}" if time_of_day else ""

    user_content = f"""Agent archetype: {archetype}{time_line}
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
  - COMFORT is driven by visual environment quality. Use a SMALL calibrated scale: excellent space (greenery, good lighting, lively pedestrian activity) → +0.06 to +0.08 max; average urban street → 0.00 to +0.02; poor quality (run-down, dark, desolate, heavy traffic noise) → −0.05 to −0.08. Most streets should score near zero — only truly exceptional or notably unpleasant spaces warrant ±0.05+.

Provide deltas as small floats (positive = need satisfied, negative = need worsened).
Note: hunger_delta is typically small from visual alone; energy_delta can be positive from restoration; social_delta reflects social vibrancy; comfort_delta uses the small calibrated scale above — most streets score near 0.0.

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
    time_of_day: str = "",
) -> list[dict]:
    """
    Prompt to evaluate how much visiting this amenity satisfies agent needs.
    Now includes cognition state and surrounding street perception context.
    """
    # Build scene description from street perception fields
    perception_text = ""
    if street_perception:
        scene_fields = [
            ("scene",             "Scene"),
            ("spatial_character", "Spatial character"),
            ("greenery",          "Greenery"),
            ("street_amenities",  "Street amenities"),
            ("visible_text",      "Signage/text"),
            ("crowdedness",       "Crowdedness"),
        ]
        lines = []
        for key, label in scene_fields:
            val = street_perception.get(key, "")
            if val and str(val).strip().lower() not in ("unknown", ""):
                lines.append(f"  {label}: {val}")
        if lines:
            perception_text = "\n\nSurrounding street scene:\n" + "\n".join(lines)

    time_line = f"\nTime of day: {time_of_day}" if time_of_day else ""

    user_content = f"""Agent archetype: {archetype}{time_line}
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

COGNITION_SYSTEM = """You are writing the inner life of a pedestrian agent navigating Barcelona's Eixample district.
Your task: update their emotional state and produce a vivid first-person reflection grounded in the SPECIFIC events and places in their recent history.

Rules:
- Reference actual events, streets, amenities, or encounters from the Recent experiences list — not abstract archetypes.
- Never open with time of day. Time is context, not the story.
- Never use filler phrases like "as the day progresses", "given the time of day", "the urban environment", "I find myself", "I feel a sense of".
- The summary must read like a thought, not a status report. It should have texture: a detail noticed, a feeling provoked, a small decision made.
- Vary mood meaningfully — don't default to neutral. If hunger is rising, the agent is irritable. If they just reached a destination, they feel relief. If they've been circling the same block, they feel frustrated.
- Curiosity and fatigue must react to events — not drift slowly by default.
Respond with valid JSON only."""


def cognition_update_prompt(
    archetype: str,
    current_cognition: dict,
    current_needs: dict,
    recent_history: str,
    step: int,
    agent_profile: dict | None = None,
    streetview_perception: str = "",
    memory_context: str = "",
    time_of_day: str = "",
) -> list[dict]:
    """
    Prompt to update agent's cognitive/emotional state based on recent experiences.
    Includes full agent profile and anti-cliché instructions for richer output.
    """
    perception_section = ""
    if streetview_perception:
        perception_section = f"\nCurrent scene (from visual analysis):\n{streetview_perception}\n"

    memory_section = f"\nLong-term Memory:\n{memory_context}\n" if memory_context else ""
    time_line = f"  Time: {time_of_day}" if time_of_day else ""

    # Build a richer agent identity line from the full profile
    profile = agent_profile or {}
    age = profile.get("age", "")
    preferences = profile.get("preferences", [])
    prefs_line = ""
    if preferences:
        prefs_line = f"\n  Preferences: {', '.join(str(p) for p in preferences[:5])}"
    age_line = f", age {age}" if age else ""

    user_content = f"""Agent: {archetype}{age_line}{time_line}{prefs_line}
Needs: hunger={current_needs.get('hunger', 0.5):.2f}, energy={current_needs.get('energy', 1.0):.2f}, social={current_needs.get('social', 0.5):.2f}, comfort={current_needs.get('comfort', 0.7):.2f}
Mental state going in: mood={current_cognition.get('mood', 'neutral')}, curiosity={current_cognition.get('curiosity', 0.7):.2f}, fatigue={current_cognition.get('fatigue', 0.0):.2f}
{perception_section}{memory_section}
Recent experiences (ground your update in these specific events):
{recent_history}

Needs → mood mapping (apply the MOST EXTREME need first, then temper with others):
  hunger > 0.7 → stressed, irritable, hard to enjoy surroundings (hangry effect)
  hunger < 0.3 → relaxed, one less distraction
  energy < 0.3 → bored, low engagement, fatigue dominates
  energy > 0.8 → excited, curiosity boosted, fatigue recovers
  social > 0.7 (unmet) → bored, disengaged, low-arousal loneliness
  social < 0.3 (satisfied) → relaxed, slight energy boost
  comfort < 0.3 → stressed, physical discomfort dominates attention
  comfort > 0.8 → relaxed, absorbs environment better

When multiple needs conflict, pick the mood for the SINGLE most extreme need (furthest from 0.5). Do not default to stressed unless hunger > 0.7 or comfort < 0.3.

Events → mood mapping (check the recent history):
  - Reached a goal or destination → relaxed, brief energy burst
  - Revisited the same street 2+ times → bored or stressed, rising fatigue
  - Discovered a new street or place → excited, curiosity spike
  - Visited an amenity that matched a need → relaxed proportional to how pressing the need was
  - Nothing notable for many steps → bored, low curiosity, slow fatigue accumulation
  - Scene mentions striking greenery, architecture, or lively activity → excited, reflect it specifically

Mood options (circumplex model — pick exactly one): excited, stressed, bored, relaxed, neutral
Curiosity and fatigue: floats 0.0–1.0. Both must change meaningfully when events warrant it.

The "summary" field is 2–3 sentences in first person. Make it specific: name a place, a decision, a sensation. Do NOT start with "I feel" or time of day. Think of it as a thought the agent has while walking.

Respond with JSON:
{{"mood": "<mood>", "curiosity": <float>, "fatigue": <float>, "summary": "<2-3 sentence first-person thought>"}}"""

    return [_system(COGNITION_SYSTEM), _user(user_content)]


# ---------------------------------------------------------------------------
# MEMORY SUMMARY prompts
# ---------------------------------------------------------------------------

_MEMORY_SUMMARY_SYSTEM = (
    "You are a memory consolidation assistant for a pedestrian simulation agent. "
    "Write a concise 2-3 sentence first-person narrative summary of recent experiences, "
    "matching the agent's archetype and focus. Be specific about places and patterns, not generic."
)

_MEMORY_CONSOLIDATION_SYSTEM = (
    "You are consolidating a resident agent's episodic memory summaries into a rich, "
    "integrated long-term memory. Merge new summaries with existing long-term knowledge, "
    "noting recurring patterns and evolving familiarity. Write in first person. "
    "Be detailed but structured — this memory persists indefinitely."
)


def memory_summary_prompt(
    archetype: str,
    recent_events_text: str,
    previous_summaries: list[str],
    focus: str,
) -> list[dict]:
    prev_block = ""
    if previous_summaries:
        joined = "\n".join(previous_summaries[-2:])
        prev_block = f"\nExisting memory (integrate or contrast):\n{joined}\n"

    user_content = (
        f"Archetype: {archetype}\n"
        f"Memory focus: {focus}\n"
        f"{prev_block}\n"
        f"Recent events to consolidate:\n{recent_events_text}\n\n"
        f'Respond with JSON: {{"summary": "<2-3 sentence first-person narrative>"}}'
    )
    return [_system(_MEMORY_SUMMARY_SYSTEM), _user(user_content)]


def memory_consolidation_prompt(
    summaries_to_merge: list[str],
    existing_unified: str,
) -> list[dict]:
    prev_block = f"Existing long-term memory:\n{existing_unified}\n\n" if existing_unified else ""
    new_block = "New summaries to integrate:\n" + "\n".join(summaries_to_merge)
    user_content = (
        f"{prev_block}{new_block}\n\n"
        'Respond with JSON: {"unified_summary": "<integrated first-person narrative, up to 200 words>"}'
    )
    return [_system(_MEMORY_CONSOLIDATION_SYSTEM), _user(user_content)]
