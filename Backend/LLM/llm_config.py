"""
Provider-agnostic LLM configuration.
Adapted from AgentSociety's LLMConfig pattern.
Supports Ollama (local), OpenAI, DeepSeek, and any OpenAI-compatible API.
"""
import os
from dataclasses import dataclass, field


@dataclass
class LLMConfig:
    provider: str = "gemini"          # gemini | ollama | vllm | docker | openai | deepseek | custom
    model: str = "gemini-2.0-flash-lite"  # Updated: gemini-2.0-flash was deprecated
    api_key: str = ""
    base_url: str = ""                # Override endpoint (e.g. vLLM at http://localhost:8001/v1)
    timeout: int = 30
    max_tokens: int = 4096
    temperature: float = 0.7

    # Computed property: resolved base_url for each provider
    def resolved_base_url(self) -> str:
        if self.base_url:
            return self.base_url
        defaults = {
            "ollama": "http://localhost:11434/v1",
            "vllm": "http://localhost:8001/v1",
            "docker": "http://localhost:12434/engines/llama.cpp/v1",
            "openai": "https://api.openai.com/v1",
            "deepseek": "https://api.deepseek.com/v1",
            "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
        }
        return defaults.get(self.provider, "http://localhost:11434/v1")

    def resolved_api_key(self) -> str:
        """Return api_key or fall back to environment variable."""
        if self.api_key:
            return self.api_key
        env_map = {
            "openai": "OPENAI_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "ollama": "ollama",  # Ollama doesn't need a real key
            "vllm": "vllm",     # vLLM accepts any non-empty key sentinel
            "docker": "docker", # Docker Model Runner needs no key; use sentinel
        }
        env_var = env_map.get(self.provider, "LLM_API_KEY")
        return os.environ.get(env_var, "ollama")

    @classmethod
    def from_env(cls) -> "LLMConfig":
        """Construct from environment variables (loaded from .env or shell)."""
        return cls(
            provider=os.environ.get("LLM_PROVIDER", "ollama"),
            model=os.environ.get("LLM_MODEL", "hf.co/unsloth/Qwen3.5-9B-GGUF:Q4_K_M"),
            api_key=os.environ.get("LLM_API_KEY", ""),
            base_url=os.environ.get("LLM_BASE_URL", ""),
            timeout=int(os.environ.get("LLM_TIMEOUT", "30")),
            max_tokens=int(os.environ.get("LLM_MAX_TOKENS", "256")),
            temperature=float(os.environ.get("LLM_TEMPERATURE", "0.7")),
        )
