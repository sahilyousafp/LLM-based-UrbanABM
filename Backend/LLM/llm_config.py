"""
Provider-agnostic LLM configuration.
Adapted from AgentSociety's LLMConfig pattern.
Supports Ollama (local), OpenAI, DeepSeek, and any OpenAI-compatible API.
"""
import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class LLMConfig:
    provider: str = "deepseek"         # deepseek | openrouter | groq | gemini | ollama | vllm | docker | openai | custom
    model: str = "deepseek-chat"
    api_key: str = ""
    api_keys: list = field(default_factory=list)  # multi-key pool — round-robins across keys
    base_url: str = ""                # Override endpoint (e.g. vLLM at http://localhost:8001/v1)
    timeout: int = 30
    max_tokens: int = 4096
    temperature: float = 0.7

    # Computed property: resolved base_url for each provider
    _SUPPORTED_PROVIDERS = frozenset({
        "openrouter", "groq", "ollama", "vllm", "docker", "openai", "deepseek", "gemini", "lmdeploy", "custom",
    })

    def resolved_base_url(self) -> str:
        if self.base_url:
            return self.base_url
        defaults = {
            "openrouter": "https://openrouter.ai/api/v1",
            "groq": "https://api.groq.com/openai/v1",
            "ollama": "http://localhost:11434/v1",
            "vllm": "http://localhost:8001/v1",
            "docker": "http://localhost:12434/engines/llama.cpp/v1",
            "openai": "https://api.openai.com/v1",
            "deepseek": "https://api.deepseek.com/v1",
            "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
            "lmdeploy": "http://localhost:8002/v1",
        }
        return defaults.get(self.provider)

    _NO_KEY_PROVIDERS = frozenset({"ollama", "vllm", "docker", "lmdeploy"})

    def resolved_api_key(self) -> str:
        """Return api_key or fall back to environment variable."""
        if self.api_key:
            return self.api_key
        env_map = {
            "openai": "OPENAI_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "groq": "GROQ_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "ollama": "ollama",
            "vllm": "vllm",
            "docker": "docker",
        }
        env_var = env_map.get(self.provider, "LLM_API_KEY")
        key = os.environ.get(env_var)
        if key:
            return key
        if self.provider in self._NO_KEY_PROVIDERS:
            return self.provider
        logger.warning(
            f"LLM provider '{self.provider}' requires {env_var} but it is not set. "
            f"Add it to your .env file."
        )
        return ""

    @classmethod
    def from_env(cls) -> "LLMConfig":
        """Construct from environment variables (loaded from .env or shell)."""
        raw_keys = os.environ.get("LLM_API_KEYS", "")
        api_keys = [k.strip() for k in raw_keys.split(",") if k.strip()] if raw_keys else []
        provider = os.environ.get("LLM_PROVIDER", "ollama")
        cfg = cls(
            provider=provider,
            model=os.environ.get("LLM_MODEL", "qwen2.5-coder:3b"),
            api_key=os.environ.get("LLM_API_KEY", ""),
            api_keys=api_keys,
            base_url=os.environ.get("LLM_BASE_URL", ""),
            timeout=int(os.environ.get("LLM_TIMEOUT", "30")),
            max_tokens=int(os.environ.get("LLM_MAX_TOKENS", "1024")),
            temperature=float(os.environ.get("LLM_TEMPERATURE", "0.7")),
        )
        if provider not in cls._SUPPORTED_PROVIDERS:
            logger.warning(
                f"Unknown LLM_PROVIDER '{provider}' in .env. "
                f"Supported: {', '.join(sorted(cls._SUPPORTED_PROVIDERS))}"
            )
        return cfg
