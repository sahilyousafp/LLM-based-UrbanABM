"""
Server lifecycle and data-collection helpers for benchmark notebook 05.
"""
import asyncio
import atexit
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).parent.parent.parent
SERVER_DIR = PROJECT_ROOT / "Backend" / "Agent"
SERVER_SCRIPT = SERVER_DIR / "map_server.py"

_active_procs: list[subprocess.Popen] = []


def _cleanup():
    for p in _active_procs:
        try:
            if p.poll() is None:
                p.terminate()
                p.wait(timeout=5)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass


atexit.register(_cleanup)


def port_is_free(port: int = 8000) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def start_server(
    env_overrides: dict[str, str] | None = None,
    port: int = 8000,
) -> subprocess.Popen:
    if not port_is_free(port):
        raise RuntimeError(
            f"Port {port} is in use. Stop the existing server first."
        )

    env = os.environ.copy()
    env["RELOAD"] = "false"
    env["PORT"] = str(port)
    env["HOST"] = "127.0.0.1"
    if env_overrides:
        env.update(env_overrides)

    kwargs = dict(
        env=env,
        cwd=str(SERVER_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(
        [sys.executable, str(SERVER_SCRIPT)],
        **kwargs,
    )
    _active_procs.append(proc)
    return proc


def stop_server(proc: subprocess.Popen, timeout: int = 10) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    if proc in _active_procs:
        _active_procs.remove(proc)
    time.sleep(2)


async def wait_for_server(
    base_url: str = "http://127.0.0.1:8000",
    timeout: int = 60,
) -> bool:
    deadline = time.time() + timeout
    async with httpx.AsyncClient(timeout=5) as client:
        while time.time() < deadline:
            try:
                resp = await client.get(f"{base_url}/")
                if resp.status_code == 200:
                    return True
            except (httpx.ConnectError, httpx.TimeoutException):
                pass
            await asyncio.sleep(1.0)
    return False


async def run_steps(
    n_steps: int,
    base_url: str = "http://127.0.0.1:8000",
    timeout_per_step: float = 600.0,
) -> list[dict]:
    results = []
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout_per_step) as client:
        for i in range(n_steps):
            t0 = time.perf_counter()
            resp = await client.post("/api/step")
            elapsed_ms = (time.perf_counter() - t0) * 1000
            data = resp.json()
            results.append(
                {
                    "step": data.get("step", i),
                    "latency_ms": elapsed_ms,
                    "llm_stats": data.get("llm_stats", {}),
                }
            )
    return results


async def collect_agent_data(
    agent_ids: list[int],
    base_url: str = "http://127.0.0.1:8000",
) -> dict[int, dict]:
    data = {}
    async with httpx.AsyncClient(base_url=base_url, timeout=120) as client:
        for aid in agent_ids:
            mem_resp = await client.get(f"/api/agent/{aid}/memory")
            stream_resp = await client.get(f"/api/agent/{aid}/stream", params={"n": 10000})
            mem = mem_resp.json().get("status", {})
            stream = stream_resp.json().get("events", [])
            data[aid] = {"memory": mem, "stream": stream}
    return data
