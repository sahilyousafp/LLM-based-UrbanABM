import logging
import random

from fastapi import APIRouter, Body
from shapely.geometry import Point, LineString

from geoparquet_recorder import get_recorder
from model import CityAgent
from state import sim

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/agents")
async def get_agents():
    features = []
    for agent in sim.city_model.city_agents:
        archetype = "unknown"
        profile = {}
        try:
            profile = await agent.memory.status.get("agent_profile", {})
            archetype = profile.get("archetype", "unknown")
        except Exception:
            pass
        gender = profile.get("gender", "unknown")
        age = profile.get("age", 0)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [agent.geometry.x, agent.geometry.y]},
            "properties": {
                "id": agent.unique_id,
                "type": agent.agent_type,
                "archetype": archetype,
                "gender": gender,
                "age": age,
                "nearby_count": len(agent.nearby_amenities)
            }
        })
    return {"type": "FeatureCollection", "features": features}


@router.post("/api/agents/respawn")
async def respawn_agents(payload: dict = Body(...)):
    count = max(1, min(100, int(payload.get("count", 15))))
    gender = str(payload.get("gender", "unknown"))
    age = payload.get("age")
    if age is not None:
        age = int(age)
    sim.reset_model(num_agents=count, agent_gender=gender, agent_age=age)
    return {
        "status": "respawned",
        "count": len(sim.city_model.city_agents),
        "step": sim.city_model.steps,
    }


@router.post("/api/agents/respawn_advanced")
async def respawn_agents_advanced(payload: dict = Body(...)):
    spawn_mode = str(payload.get("spawn_mode", "random"))
    count = max(1, min(100, int(payload.get("count", 15))))
    points = payload.get("points", []) or []
    home_points = payload.get("home_points", []) or []
    work_points = payload.get("work_points", []) or []
    mix = payload.get("archetype_mix", {}) or {}
    _age_raw = payload.get("age")
    _agent_age = int(_age_raw) if _age_raw is not None else None

    archetypes = list(CityAgent.ARCHETYPES)

    def _normalise_mix(m: dict) -> list[str]:
        if not m:
            return [archetypes[i % len(archetypes)] for i in range(count)]
        total = sum(max(0.0, float(v)) for v in m.values()) or 1.0
        seq: list[str] = []
        for arch in archetypes:
            n = int(round(count * max(0.0, float(m.get(arch, 0))) / total))
            seq.extend([arch] * n)
        while len(seq) < count:
            seq.append(archetypes[len(seq) % len(archetypes)])
        return seq[:count]

    def _reattach_recorder():
        rec = get_recorder()
        if rec and rec.is_recording:
            sim.city_model.set_recorder(rec)

    if spawn_mode == "random":
        sim.reset_model(num_agents=count, agent_gender=payload.get("gender", "unknown"), agent_age=_agent_age)
        _reattach_recorder()
        return {
            "status": "respawned",
            "spawn_mode": "random",
            "count": len(sim.city_model.city_agents),
            "step": sim.city_model.steps,
        }

    triples: list[tuple[float, float, str]] = []
    if spawn_mode == "click":
        if not points:
            return {"error": "click mode requires non-empty points list"}
        for p in points:
            arch = str(p.get("archetype") or archetypes[len(triples) % len(archetypes)])
            try:
                triples.append((float(p["lon"]), float(p["lat"]), arch))
            except (KeyError, TypeError, ValueError):
                continue
    elif spawn_mode == "home_work":
        if not home_points and not work_points:
            return {"error": "home_work mode requires non-empty home_points or work_points"}
        for p in home_points:
            try:
                triples.append((float(p["lon"]), float(p["lat"]), "resident"))
            except (KeyError, TypeError, ValueError):
                continue
        for p in work_points:
            try:
                triples.append((float(p["lon"]), float(p["lat"]), "commuter"))
            except (KeyError, TypeError, ValueError):
                continue
    elif spawn_mode == "poi":
        if not points:
            return {"error": "poi mode requires non-empty points list"}
        arch_seq = _normalise_mix(mix) if mix else None
        for idx, p in enumerate(points[:count]):
            arch = (arch_seq[idx] if arch_seq else None) or str(p.get("archetype") or archetypes[idx % len(archetypes)])
            try:
                triples.append((float(p["lon"]), float(p["lat"]), arch))
            except (KeyError, TypeError, ValueError):
                continue
    else:
        return {"error": f"Unknown spawn_mode '{spawn_mode}'"}

    if not triples:
        return {"error": "No valid spawn points could be parsed for mode '{}'".format(spawn_mode)}

    sim.reset_model(num_agents=0, agent_gender=payload.get("gender", "unknown"), agent_age=_agent_age)
    _reattach_recorder()

    placed = 0
    skipped = 0
    for lon, lat, arch in triples:
        if arch not in CityAgent.ARCHETYPES:
            arch = archetypes[placed % len(archetypes)]
        start_node = sim.city_model._find_nearest_node(lon, lat)
        if not start_node:
            skipped += 1
            continue
        edges_at_start = sim.city_model.node_to_edges.get(start_node, [])
        if not edges_at_start:
            skipped += 1
            continue
        forward = [e for e in edges_at_start if e[2] == "forward"] or edges_at_start
        edge_id, edge_geom, direction = forward[0][0], forward[0][1], forward[0][2]
        if direction == "reverse":
            edge_geom = LineString(list(edge_geom.coords)[::-1])
        start_point = Point(edge_geom.coords[0])

        target_info = None
        try:
            if arch == "resident":
                if spawn_mode == "home_work":
                    home_node = sim.city_model._find_nearest_node(lon, lat)
                    target_info = {
                        "name": "home", "amenity_type": "residential",
                        "lon": lon, "lat": lat, "target_node": home_node,
                    }
                else:
                    mc_nodes = sim.city_model.main_component_nodes
                    if mc_nodes:
                        home_key = random.choice(list(mc_nodes))
                        target_info = {
                            "name": "home", "amenity_type": "residential",
                            "lon": home_key[0], "lat": home_key[1],
                            "target_node": home_key,
                        }
            elif arch == "commuter":
                if spawn_mode == "home_work":
                    work_node = sim.city_model._find_nearest_node(lon, lat)
                    target_info = {
                        "name": "workplace", "amenity_type": "office",
                        "lon": lon, "lat": lat, "target_node": work_node,
                    }
                else:
                    target_info = sim.city_model._pick_target_for_archetype(arch)
                    if target_info:
                        tn = sim.city_model._find_nearest_node(target_info["lon"], target_info["lat"])
                        target_info["target_node"] = tn
            else:
                target_info = sim.city_model._pick_target_for_archetype(arch)
                if target_info:
                    tn = sim.city_model._find_nearest_node(target_info["lon"], target_info["lat"])
                    target_info["target_node"] = tn
        except Exception:
            target_info = None

        try:
            agent = CityAgent(
                model=sim.city_model,
                geometry=start_point,
                crs="EPSG:4326",
                edge_id=int(edge_id),
                edge_geom=edge_geom,
                archetype=arch,
                target_info=target_info,
                gender=payload.get("gender", "unknown"),
                age=_agent_age,
            )
            sim.city_model.city_agents.append(agent)
            placed += 1
        except Exception as e:
            logger.warning(f"respawn_advanced: failed to place agent at ({lon},{lat}): {e}")
            skipped += 1

    return {
        "status": "respawned",
        "spawn_mode": spawn_mode,
        "count": placed,
        "skipped": skipped,
        "step": sim.city_model.steps,
    }


@router.get("/api/agent/{agent_id}")
async def get_agent_info(agent_id: int):
    agent = next((a for a in sim.city_model.city_agents if a.unique_id == agent_id), None)
    if not agent:
        return {"error": "Agent not found"}
    profile = await agent.memory.status.get("agent_profile", {})
    return {
        "id": agent.unique_id,
        "type": agent.agent_type,
        "archetype": profile.get("archetype", "unknown"),
        "gender": profile.get("gender", "unknown"),
        "age": profile.get("age", 0),
        "location": {"lon": agent.geometry.x, "lat": agent.geometry.y},
        "nearby_amenities": agent.nearby_amenities,
        "street_perception": agent.street_perception
    }


@router.get("/api/agent/{agent_id}/summary")
async def get_agent_summary(agent_id: int):
    agent = next((a for a in sim.city_model.city_agents if a.unique_id == agent_id), None)
    if not agent:
        return {"error": "Agent not found"}
    try:
        profile = await agent.memory.status.get("agent_profile", {})
        needs = await agent.memory.status.get("needs", {})
        cognition = await agent.memory.status.get("cognition_state", {})
        perception_ctx = ""
        if hasattr(agent, 'street_perception') and agent.street_perception:
            sp = agent.street_perception
            scene_parts = []
            for key in ("scene_overview", "vegetation", "pedestrian_activity", "lighting_atmosphere"):
                val = sp.get(key, "")
                if val and val.strip().lower() != "unknown":
                    scene_parts.append(val)
            if scene_parts:
                perception_ctx = f" Street scene: {' '.join(scene_parts[:2])}"
        amenities_list = ', '.join(a.get('type', '?') for a in agent.nearby_amenities[:5]) or 'nothing notable'
        messages = [
            {"role": "system", "content": "You are narrating an urban simulation agent in Barcelona Eixample. Be concise (2-3 sentences)."},
            {"role": "user", "content": (
                f"Agent {agent_id} is a {profile.get('archetype','pedestrian')} at "
                f"lon={agent.geometry.x:.5f}, lat={agent.geometry.y:.5f}. "
                f"Needs: hunger={needs.get('hunger',0.5):.2f}, energy={needs.get('energy',1.0):.2f}, social={needs.get('social',0.5):.2f}. "
                f"Mood: {cognition.get('mood','neutral')}. "
                f"Nearby: {amenities_list}."
                f"{perception_ctx} "
                "Narrate what this agent is experiencing right now."
            )}
        ]
        summary = await sim.city_model.llm_client.chat(messages)
        return {
            "agent_id": agent.unique_id,
            "summary": summary,
            "location": {"lon": agent.geometry.x, "lat": agent.geometry.y},
            "amenity_count": len(agent.nearby_amenities),
            "archetype": profile.get("archetype", "unknown"),
            "perception_mode": getattr(sim.city_model, 'perception_mode', 'both'),
        }
    except Exception as e:
        import logging as _log
        _log.error(f"Error generating summary for agent {agent_id}: {e}")
        return {
            "agent_id": agent_id,
            "summary": f"Error generating narrative: {str(e)}",
            "location": {"lon": agent.geometry.x, "lat": agent.geometry.y},
            "amenity_count": len(agent.nearby_amenities) if agent else 0,
            "error_details": str(e),
        }


@router.get("/api/agents/summaries")
async def get_all_agent_summaries():
    import asyncio

    async def _summarize(agent):
        profile = await agent.memory.status.get("agent_profile", {})
        needs = await agent.memory.status.get("needs", {})
        messages = [
            {"role": "system", "content": "Narrate this urban simulation agent in one sentence."},
            {"role": "user", "content": (
                f"Agent {agent.unique_id} ({profile.get('archetype','pedestrian')}) at "
                f"{agent.geometry.x:.4f},{agent.geometry.y:.4f}. "
                f"Hunger={needs.get('hunger',0.5):.1f} Energy={needs.get('energy',1.0):.1f}. "
                f"Nearby: {', '.join(a.get('type','?') for a in agent.nearby_amenities[:3]) or 'none'}."
            )}
        ]
        summary = await sim.city_model.llm_client.chat(messages)
        return {
            "agent_id": agent.unique_id,
            "summary": summary,
            "location": {"lon": agent.geometry.x, "lat": agent.geometry.y},
            "archetype": profile.get("archetype", "unknown"),
        }

    sample = sim.city_model.city_agents[:10]
    summaries = await asyncio.gather(*[_summarize(a) for a in sample])
    return {"total_agents": len(sim.city_model.city_agents), "sample_size": len(summaries), "summaries": list(summaries)}


@router.get("/api/agent/{agent_id}/memory")
async def get_agent_memory(agent_id: int):
    agent = next((a for a in sim.city_model.city_agents if a.unique_id == agent_id), None)
    if not agent:
        return {"error": "Agent not found"}
    return await agent.memory.snapshot()


@router.get("/api/agent/{agent_id}/stream")
async def get_agent_stream(agent_id: int, topic: str = "", n: int = 20):
    agent = next((a for a in sim.city_model.city_agents if a.unique_id == agent_id), None)
    if not agent:
        return {"error": "Agent not found"}
    if topic:
        nodes = await agent.memory.stream.get_recent(topic, n=n)
    else:
        nodes = await agent.memory.stream.get_recent_all(n=n)
    return {
        "agent_id": agent_id,
        "topic": topic or "all",
        "events": [
            {"step": nd.step, "topic": nd.topic, "description": nd.description, "metadata": nd.metadata}
            for nd in nodes
        ],
    }


@router.get("/api/agent/{agent_id}/cognition")
async def get_agent_cognition(agent_id: int):
    agent = next((a for a in sim.city_model.city_agents if a.unique_id == agent_id), None)
    if not agent:
        return {"error": "Agent not found"}
    cognition = await agent.memory.status.get("cognition_state", {})
    needs = await agent.memory.status.get("needs", {})
    profile = await agent.memory.status.get("agent_profile", {})
    plan = await agent.memory.status.get("current_plan", {})
    satisfaction_source = await agent.memory.status.get("satisfaction_source", "none")
    satisfaction_reasoning = await agent.memory.status.get("satisfaction_reasoning", "")
    return {
        "agent_id": agent_id,
        "archetype": profile.get("archetype", "unknown"),
        "gender": profile.get("gender", "unknown"),
        "age": profile.get("age", 0),
        "cognition_state": cognition,
        "needs": needs,
        "current_plan": plan,
        "satisfaction_source": satisfaction_source,
        "satisfaction_reasoning": satisfaction_reasoning,
    }


@router.get("/api/agent/{agent_id}/perception-text")
async def get_agent_perception(agent_id: int):
    import json as json_lib
    import re
    from math import sqrt
    from paths import SV_RESULTS_DIR

    agent = next((a for a in sim.city_model.city_agents if a.unique_id == agent_id), None)
    if not agent:
        return {"error": "Agent not found", "image_url": "", "perception": {}}

    agent_lon = agent.geometry.x
    agent_lat = agent.geometry.y

    if hasattr(agent, 'street_perception') and agent.street_perception:
        return {
            "agent_id": agent_id,
            "image_url": "",
            "perception": agent.street_perception,
            "location": {"lon": agent_lon, "lat": agent_lat},
            "closest_distance_km": 0.0,
        }

    closest_file = None
    closest_distance = float('inf')
    if SV_RESULTS_DIR.is_dir():
        for json_file in SV_RESULTS_DIR.glob("*_analysis.json"):
            m = re.match(r"^(-?\d+\.\d+)_(-?\d+\.\d+)_analysis\.json$", json_file.name)
            if not m:
                continue
            sv_lat, sv_lon = float(m.group(1)), float(m.group(2))
            dist = sqrt((sv_lon - agent_lon)**2 + (sv_lat - agent_lat)**2)
            if dist < closest_distance:
                closest_distance = dist
                closest_file = json_file

    perception_data = {}
    image_url = ""
    if closest_file:
        try:
            data = json_lib.loads(closest_file.read_text(encoding="utf-8"))
            meta = data.get("metadata", {})
            scene = data.get("scene_analysis", {})
            src_img = meta.get("source_image", "")
            perception_data = scene
            image_url = f"/api/streetview_grid/image/{src_img}" if src_img else ""
        except Exception as e:
            logger.warning(f"Error loading streetview data: {e}")

    return {
        "agent_id": agent_id,
        "image_url": image_url,
        "perception": perception_data,
        "location": {"lon": agent_lon, "lat": agent_lat},
        "closest_distance_km": closest_distance if closest_distance != float('inf') else None,
    }


@router.get("/api/agent/{agent_id}/path-adherence")
async def get_path_adherence(agent_id: int):
    agent = next((a for a in sim.city_model.city_agents if a.unique_id == agent_id), None)
    if not agent:
        return {"error": "Agent not found"}
    events = await agent.memory.stream.get_recent_all(n=10000)
    mob = [e for e in events if e.topic == 'mobility']
    on_path = [e for e in mob if e.metadata.get('on_path') is True]
    return {
        "agent_id": agent_id,
        "adherence": {
            "pct_followed": round(len(on_path) / len(mob) * 100, 1) if mob else 0,
            "steps_followed": len(on_path),
            "total_steps": len(mob),
        }
    }


@router.get("/api/agent/{agent_id}/narrative-compare")
async def get_narrative_compare(agent_id: int):
    import asyncio as _asyncio
    agent = next((a for a in sim.city_model.city_agents if a.unique_id == agent_id), None)
    if not agent:
        return {"error": "Agent not found"}
    events = await agent.memory.stream.get_recent_all(n=200)
    profile = await agent.memory.status.get("agent_profile", {})
    visited = await agent.memory.status.get("visited_amenities", [])
    event_summary = "\n".join(f"[{e.topic}] {e.description}" for e in events[-50:])
    archetype = profile.get("archetype", "unknown")
    visited_str = ", ".join(a.get("name", "?") for a in (visited or [])[:10]) if visited else "none"
    generic_msgs = [{"role": "user", "content": (
        f"You are narrating a pedestrian simulation. Archetype: {archetype}.\n"
        f"Recent events:\n{event_summary}\n"
        f"Write a 2-3 sentence generic narrative of this agent's journey."
    )}]
    history_msgs = [{"role": "user", "content": (
        f"You are narrating a pedestrian simulation. Archetype: {archetype}. "
        f"Places visited: {visited_str}.\nRecent events:\n{event_summary}\n"
        f"Write a 2-3 sentence narrative referencing the agent's history and spatial memory."
    )}]
    try:
        generic, history_aware = await _asyncio.gather(
            sim.city_model.llm_client.chat(generic_msgs),
            sim.city_model.llm_client.chat(history_msgs),
        )
    except Exception as e:
        logger.warning(f"narrative-compare LLM error: {e}")
        generic = "Narrative unavailable (LLM offline or not configured)."
        history_aware = "Narrative unavailable (LLM offline or not configured)."
    return {"agent_id": agent_id, "generic": generic, "history_aware": history_aware}


@router.get("/api/agent/{agent_id}/results-summary")
async def get_results_summary(agent_id: int):
    import asyncio as _asyncio
    agent = next((a for a in sim.city_model.city_agents if a.unique_id == agent_id), None)
    if not agent:
        return {"error": "Agent not found"}
    events = await agent.memory.stream.get_recent_all(n=10000)
    profile = await agent.memory.status.get("agent_profile", {})
    visited = await agent.memory.status.get("visited_amenities", [])
    archetype = profile.get("archetype", "unknown")
    visited_str = ", ".join(a.get("name", "?") for a in (visited or [])[:10]) if visited else "none"

    def msgs_for(topic_events, instruction):
        if not topic_events:
            return None
        lines = "\n".join(f"[step {e.step}] {e.description}" for e in topic_events[-80:])
        return [{"role": "user", "content": (
            f"Agent archetype: {archetype}.\n{instruction}\n\nEvents:\n{lines}\n\n"
            f"Write a 2-3 sentence summary. Be specific and concise."
        )}]

    perc = [e for e in events if e.topic == "perception" and e.metadata.get("source") != "visual_satisfaction"]
    mob = [e for e in events if e.topic == "mobility"]
    amenity = [e for e in events if e.topic == "amenity_visit"]
    cogn = [e for e in events if e.topic == "cognition"]
    needs = [e for e in events if e.topic == "needs"]

    tasks = {
        "vision": msgs_for(perc, "Summarise what this agent observed visually in the urban environment throughout the simulation."),
        "all": msgs_for(events[-100:], f"Summarise this agent's overall journey. Places visited: {visited_str}."),
        "mobility": msgs_for(mob, "Summarise this agent's movement and navigation behaviour — where it went, how it decided, LLM vs rule-based decisions."),
        "amenity_visit": msgs_for(amenity, "Summarise the places this agent visited, which needs were satisfied, and any notable stops."),
        "cognition": msgs_for(cogn, "Summarise this agent's mental state changes, mood shifts, and decision reasoning over time."),
        "needs": msgs_for(needs, "Summarise how this agent's needs (hunger, energy, social, comfort) evolved and were satisfied."),
    }

    async def call_one(key, msgs):
        if msgs is None:
            return key, None
        try:
            return key, await sim.city_model.llm_client.chat(msgs)
        except Exception as exc:
            logger.warning(f"results-summary LLM error [{key}]: {exc}")
            return key, None

    pairs = await _asyncio.gather(*[call_one(k, v) for k, v in tasks.items()])
    return dict(pairs)
