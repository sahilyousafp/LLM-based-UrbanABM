"""EQ-Bench V2 benchmark router — run, poll, persist results."""
import json
import logging
import math
import os
import re
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Body

from state import sim

logger = logging.getLogger(__name__)
router = APIRouter()

_QUESTIONS_PATH = Path(__file__).parent.parent.parent.parent / "benchmark" / "data" / "eq_bench_v2_questions_171.json"
_RESULTS_PATH = Path(__file__).parent.parent.parent.parent / "benchmark" / "results" / "custom_benchmarks.json"

# ---------------------------------------------------------------------------
# EQ-Bench scoring (Paech, 2024) — ported from benchmark/02_llm_provider_comparison.ipynb
# ---------------------------------------------------------------------------

def _eqbench_score(reference: dict, predicted: dict) -> float:
    difference_tally = 0
    matched = 0
    for emotion, user_score in predicted.items():
        for i in range(1, 5):
            ref_emotion = reference[f"emotion{i}"]
            if emotion.lower() == ref_emotion.lower():
                ref_score = float(reference[f"emotion{i}_score"])
                d = abs(float(user_score) - ref_score)
                if d == 0:
                    scaled = 0
                elif d <= 5:
                    scaled = 6.5 * (1 / (1 + math.e ** (-1.2 * (d - 4))))
                else:
                    scaled = d
                difference_tally += scaled
                matched += 1
    if matched == 0:
        return 0.0
    return 10 - (difference_tally * 0.7477)


def _parse_eqbench_response(text: str) -> tuple[dict, dict]:
    first_pass: dict = {}
    revised: dict = {}
    text_lower = text.lower()
    revised_start = text_lower.rfind("revised score")
    if revised_start == -1:
        revised_start = text_lower.rfind("revised")
    score_pattern = re.compile(r"([A-Za-z\s\-]+):\s*(\d+(?:\.\d+)?)")
    skip = {"first pass scores", "critique", "revised scores"}
    if revised_start > 0:
        for match in score_pattern.finditer(text[:revised_start]):
            label = match.group(1).strip()
            if label.lower() not in skip:
                first_pass[label] = float(match.group(2))
        for match in score_pattern.finditer(text[revised_start:]):
            label = match.group(1).strip()
            if label.lower() not in skip:
                revised[label] = float(match.group(2))
    else:
        for match in score_pattern.finditer(text):
            label = match.group(1).strip()
            if label.lower() not in skip:
                first_pass[label] = float(match.group(2))
    return first_pass, revised


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _load_results() -> list[dict]:
    if not _RESULTS_PATH.exists():
        return []
    try:
        data = json.loads(_RESULTS_PATH.read_text(encoding="utf-8"))
        return data.get("results", [])
    except Exception:
        return []


def _save_results(results: list[dict]) -> None:
    _RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _RESULTS_PATH.write_text(json.dumps({"results": results}, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/api/benchmark/results")
async def get_benchmark_results():
    return {"results": _load_results()}


@router.delete("/api/benchmark/results/{label}")
async def delete_benchmark_result(label: str):
    results = _load_results()
    results = [r for r in results if r.get("label") != label]
    _save_results(results)
    return {"ok": True}


@router.post("/api/benchmark/eqbench")
async def start_eqbench(body: dict = Body(...)):
    provider = body.get("provider", "ollama")
    model = body.get("model", "")
    base_url = body.get("base_url", "")
    api_key = body.get("api_key", "")
    max_questions = min(int(body.get("max_questions", 20)), 171)

    if not model:
        return {"error": "model is required"}

    if not _QUESTIONS_PATH.exists():
        return {"error": "EQ-Bench question file not found"}

    questions = json.loads(_QUESTIONS_PATH.read_text(encoding="utf-8"))
    q_ids = list(questions.keys())[:max_questions]

    job_id = str(uuid.uuid4())[:8]
    progress = {
        "status": "running",
        "provider": provider,
        "model": model,
        "scored": 0,
        "failures": 0,
        "total": len(q_ids),
        "pct": 0.0,
        "running_avg": 0.0,
        "score": None,
        "median_latency_ms": None,
    }
    sim.benchmark_jobs[job_id] = progress

    def _resolve_base_url(prov: str, url: str) -> str:
        if url:
            return url
        defaults = {
            "ollama": "http://localhost:11434/v1",
            "openai": "https://api.openai.com/v1",
            "deepseek": "https://api.deepseek.com/v1",
            "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
            "groq": "https://api.groq.com/openai/v1",
            "huggingface": "https://api-inference.huggingface.co/v1",
        }
        return defaults.get(prov, "http://localhost:11434/v1")

    def _run():
        try:
            from openai import OpenAI
        except ImportError:
            progress["status"] = "error"
            progress["error"] = "openai package not installed"
            return

        resolved_url = _resolve_base_url(provider, base_url)
        resolved_key = api_key or os.getenv("LLM_API_KEY", "") or "ollama"
        client = OpenAI(api_key=resolved_key, base_url=resolved_url)

        try:
            client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Reply: hello"}],
                max_tokens=10, timeout=15,
            )
        except Exception as e:
            progress["status"] = "error"
            progress["error"] = f"Cannot reach {provider}/{model}: {e}"
            return

        scores: list[float] = []
        latencies: list[float] = []

        for i, qid in enumerate(q_ids):
            if progress.get("cancelled"):
                progress["status"] = "cancelled"
                return
            q = questions[qid]
            t0 = time.perf_counter()
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": q["prompt"]}],
                    max_tokens=600, temperature=0.0,
                )
                elapsed = (time.perf_counter() - t0) * 1000
                latencies.append(elapsed)
                content = resp.choices[0].message.content or ""
                first_pass, revised = _parse_eqbench_response(content)
                answers = revised if revised else first_pass
                if len(answers) >= 2:
                    ref = q["reference_answer_fullscale"]
                    s = _eqbench_score(ref, answers)
                    scores.append(s)
                else:
                    progress["failures"] += 1
            except Exception:
                progress["failures"] += 1

            progress["scored"] = len(scores)
            progress["pct"] = round((i + 1) / len(q_ids) * 100, 1)
            if scores:
                progress["running_avg"] = round(sum(scores) / len(scores) * 10, 2)

        if not scores:
            progress["status"] = "error"
            progress["error"] = "No valid scores obtained"
            return

        final_score = round(sum(scores) / len(scores) * 10, 2)
        median_lat = round(sorted(latencies)[len(latencies) // 2], 0) if latencies else 0

        progress["status"] = "done"
        progress["score"] = final_score
        progress["median_latency_ms"] = median_lat

        label = f"{model}"
        result_entry = {
            "provider": provider,
            "model": model,
            "label": label,
            "score": final_score,
            "questions": len(scores),
            "failures": progress["failures"],
            "median_latency_ms": median_lat,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        existing = _load_results()
        existing = [r for r in existing if r.get("label") != label]
        existing.append(result_entry)
        _save_results(existing)
        logger.info(f"EQ-Bench complete: {label} = {final_score}/100 ({len(scores)} scored, {progress['failures']} failures)")

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id, "total_questions": len(q_ids)}


@router.get("/api/benchmark/eqbench/status/{job_id}")
async def get_eqbench_status(job_id: str):
    if job_id not in sim.benchmark_jobs:
        return {"error": "Unknown job"}
    return sim.benchmark_jobs[job_id]
