"""
Async LLM client — provider-agnostic.
Ollama uses its native /api/chat endpoint directly (more reliable than the OpenAI compat layer).
All other providers use the OpenAI-compatible API via AsyncOpenAI.
Supports a multi-key pool via LLM_API_KEYS (comma-separated) for parallel throughput.
"""
import asyncio
import itertools
import json
import logging
import os
import time
from typing import Any, Optional

from .llm_config import LLMConfig

logger = logging.getLogger(__name__)

try:
    from openai import AsyncOpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    logger.warning("openai package not installed. Install with: pip install openai")

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False


class LLMClient:
    """
    Async LLM client with retry, token tracking, and JSON response support.
    Ollama → native /api/chat via httpx (bypasses broken OpenAI compat layer).
    All other providers → AsyncOpenAI (OpenAI-compatible API).
    """

    def __init__(self, config: Optional[LLMConfig] = None):
        self.config = config or LLMConfig.from_env()
        self._clients: list[Any] = []
        self._client_iter: Optional[Any] = None
        self._ollama_client: Optional[httpx.AsyncClient] = None
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_calls = 0
        self.total_latency_ms = 0.0
        self.total_errors = 0
        self.total_fallbacks = 0

        self._circuit_open = False
        self._circuit_failures = 0
        self._circuit_recovery_at = 0.0
        self._circuit_threshold = int(os.environ.get("LLM_CIRCUIT_BREAKER_THRESHOLD", "5"))
        self._circuit_recovery = int(os.environ.get("LLM_CIRCUIT_BREAKER_RECOVERY", "30"))

    @property
    def _is_ollama(self) -> bool:
        return self.config.provider == "ollama"

    def _get_client(self) -> Any:
        if not self._clients:
            if not OPENAI_AVAILABLE:
                raise ImportError("openai package required. Run: pip install openai")
            keys = self.config.api_keys or [self.config.resolved_api_key()]
            self._clients = [
                AsyncOpenAI(
                    api_key=key,
                    base_url=self.config.resolved_base_url(),
                    timeout=self.config.timeout,
                )
                for key in keys
            ]
            self._client_iter = itertools.cycle(self._clients)
            logger.info(
                f"LLM client pool: {len(self._clients)} key(s), "
                f"provider={self.config.provider}, model={self.config.model}, "
                f"base_url={self.config.resolved_base_url()}"
            )
        return next(self._client_iter)

    def _is_retryable(self, exc: Exception) -> bool:
        if isinstance(exc, (httpx.ConnectError, httpx.RemoteProtocolError, httpx.TimeoutException)):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code >= 500
        if hasattr(exc, "status_code"):
            return exc.status_code >= 500
        if hasattr(exc, "code") and isinstance(exc.code, int):
            return exc.code >= 500
        return True

    def _circuit_breaker(self) -> bool:
        now = time.monotonic()
        if self._circuit_open:
            if now >= self._circuit_recovery_at:
                self._circuit_open = False
                self._circuit_failures = 0
                logger.info("LLM circuit breaker: half-open, allowing one probe request")
                return False
            return True
        return False

    def _record_failure(self):
        self._circuit_failures += 1
        if self._circuit_failures >= self._circuit_threshold:
            self._circuit_open = True
            self._circuit_recovery_at = time.monotonic() + self._circuit_recovery
            logger.warning(
                f"LLM circuit breaker OPEN after {self._circuit_failures} failures, "
                f"recovering in {self._circuit_recovery}s"
            )

    # ── Ollama native API ────────────────────────────────────────────────────

    async def _ollama_chat(self, messages: list[dict]) -> str:
        """Call Ollama's native /api/chat endpoint directly."""
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx required for Ollama native API. Run: pip install httpx")

        base = self.config.base_url or "http://localhost:11434"
        base = base.rstrip("/").removesuffix("/v1")  # normalise: strip /v1 suffix if present
        url = f"{base}/api/chat"

        payload = {
            "model": self.config.model,
            "messages": messages,
            "stream": False,
            "options": {
                "num_predict": self.config.max_tokens,
                "temperature": self.config.temperature,
            },
        }

        if self._ollama_client is None:
            self._ollama_client = httpx.AsyncClient(timeout=self.config.timeout)
        resp = await self._ollama_client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()

        error_text = data.get("error")
        if error_text:
            raise RuntimeError(f"Ollama error: {error_text}")
        return data.get("message", {}).get("content", "")

    # ── OpenAI-compat API ────────────────────────────────────────────────────

    async def _openai_chat(
        self,
        messages: list[dict],
        response_format: Optional[str] = None,
        tools: Optional[list] = None,
    ) -> str:
        client = self._get_client()
        kwargs: dict = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
        }
        if response_format == "json":
            kwargs["response_format"] = {"type": "json_object"}
        if tools:
            kwargs["tools"] = tools

        response = await client.chat.completions.create(**kwargs)
        if response.usage:
            self.total_input_tokens += response.usage.prompt_tokens
            self.total_output_tokens += response.usage.completion_tokens
        return response.choices[0].message.content or ""

    # ── Public interface ─────────────────────────────────────────────────────

    async def chat(
        self,
        messages: list[dict],
        response_format: Optional[str] = None,
        tools: Optional[list] = None,
        max_retries: int = 3,
    ) -> str:
        """Send a chat request and return the response text."""
        if self._circuit_breaker():
            logger.warning("LLM circuit breaker open, skipping call")
            self.total_errors += 1
            return ""

        last_error = None

        for attempt in range(max_retries):
            try:
                t0 = time.monotonic()

                if self._is_ollama:
                    content = await self._ollama_chat(messages)
                else:
                    content = await self._openai_chat(messages, response_format, tools)

                latency_ms = (time.monotonic() - t0) * 1000
                self.total_calls += 1
                self.total_latency_ms += latency_ms
                self._circuit_failures = 0  # success resets failure count
                logger.debug(f"LLM call #{self.total_calls} | {latency_ms:.0f}ms")

                if not content:
                    logger.warning(f"LLM returned empty content on call #{self.total_calls}")
                return content

            except Exception as e:
                last_error = e
                if not self._is_retryable(e):
                    logger.warning(f"LLM non-retryable error on attempt {attempt+1}: {e}")
                    self.total_errors += 1
                    self._record_failure()
                    return ""
                wait = (2 ** attempt) * (0.5 + (hash(str(e)) % 50) / 100.0)
                logger.warning(f"LLM attempt {attempt+1}/{max_retries} failed: {e}. Retrying in {wait:.1f}s.")
                await asyncio.sleep(wait)

        logger.error(f"LLM unavailable after {max_retries} retries: {last_error}")
        self.total_errors += 1
        self._record_failure()
        return ""

    async def chat_json(self, messages: list[dict], max_retries: int = 3) -> dict:
        """Request structured JSON response. Returns empty dict on failure."""
        raw = await self.chat(messages, response_format="json", max_retries=max_retries)
        if not raw:
            self.total_fallbacks += 1
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            import re
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass
            match = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass
            logger.warning(f"Failed to parse LLM JSON response: {raw[:600]}")
            self.total_fallbacks += 1
            return {}

    def stats(self) -> dict:
        avg_latency = self.total_latency_ms / max(1, self.total_calls)
        return {
            "total_calls": self.total_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "avg_latency_ms": round(avg_latency, 1),
            "total_errors": self.total_errors,
            "total_fallbacks": self.total_fallbacks,
            "key_pool_size": len(self._clients) if self._clients else 1,
        }
