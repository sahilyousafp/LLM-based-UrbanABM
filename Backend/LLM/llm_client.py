"""
Async LLM client — provider-agnostic via OpenAI-compatible API.
Ollama exposes /v1/chat/completions, so the same client works for local and cloud.
Adapted from AgentSociety's LLMActor pattern (without Ray dependency).
"""
import asyncio
import json
import logging
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


class LLMClient:
    """
    Async LLM client with retry, token tracking, and JSON response support.
    Uses openai.AsyncOpenAI which is compatible with Ollama, OpenAI, DeepSeek, etc.
    """

    def __init__(self, config: Optional[LLMConfig] = None):
        self.config = config or LLMConfig.from_env()
        self._client: Optional[Any] = None
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_calls = 0
        self.total_latency_ms = 0.0
        self.total_errors = 0
        self.total_fallbacks = 0

    def _get_client(self) -> Any:
        if self._client is None:
            if not OPENAI_AVAILABLE:
                raise ImportError("openai package required. Run: pip install openai")
            logger.info(f"Initializing LLM client: provider={self.config.provider}, model={self.config.model}, base_url={self.config.resolved_base_url()}")
            self._client = AsyncOpenAI(
                api_key=self.config.resolved_api_key(),
                base_url=self.config.resolved_base_url(),
                timeout=self.config.timeout,
            )
        return self._client

    async def chat(
        self,
        messages: list[dict],
        response_format: Optional[str] = None,   # "json" for structured output
        tools: Optional[list] = None,
        max_retries: int = 3,
    ) -> str:
        """
        Send a chat request and return the response text.
        Retries with exponential backoff on failure.
        Falls back gracefully if LLM is unavailable.
        """
        client = self._get_client()
        last_error = None

        for attempt in range(max_retries):
            try:
                t0 = time.monotonic()
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
                latency_ms = (time.monotonic() - t0) * 1000

                # Track token usage
                if response.usage:
                    self.total_input_tokens += response.usage.prompt_tokens
                    self.total_output_tokens += response.usage.completion_tokens
                self.total_calls += 1
                self.total_latency_ms += latency_ms

                content = response.choices[0].message.content or ""
                logger.debug(
                    f"LLM call #{self.total_calls} | {latency_ms:.0f}ms | "
                    f"in={response.usage.prompt_tokens if response.usage else '?'} "
                    f"out={response.usage.completion_tokens if response.usage else '?'}"
                )
                if not content:
                    logger.warning(f"LLM returned empty content on call #{self.total_calls}")
                return content

            except Exception as e:
                last_error = e
                wait = 2 ** attempt
                logger.warning(f"LLM attempt {attempt+1}/{max_retries} failed: {e}. Retrying in {wait}s.")
                await asyncio.sleep(wait)

        logger.error(f"LLM unavailable after {max_retries} retries: {last_error}")
        self.total_errors += 1
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
            # Try to extract JSON from markdown code blocks
            import re
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass

            # Try to extract first JSON object from response (handles trailing text)
            # This regex finds the first complete {...} object, ignoring content after
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
        """Return token usage and latency statistics."""
        avg_latency = self.total_latency_ms / max(1, self.total_calls)
        return {
            "total_calls": self.total_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "avg_latency_ms": round(avg_latency, 1),
            "total_errors": self.total_errors,
            "total_fallbacks": self.total_fallbacks,
        }
