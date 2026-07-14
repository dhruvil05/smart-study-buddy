"""Application configuration: provider settings and environment handling."""
from __future__ import annotations
import os
from typing import Literal

Provider = Literal["anthropic", "gemini", "openrouter"]

# Provider → default model mapping
PROVIDER_MODELS = {
    "anthropic": "claude-haiku-4-5",
    "gemini": "gemini-2.0-flash",
    "openrouter": "openai/gpt-oss-20b",
}

# Provider → UI accent color
PROVIDER_COLORS = {
    "anthropic": "#534AB7",  # Purple
    "gemini": "#185FA5",     # Accent blue
    "openrouter": "#FF9900", # Orange
}

DEFAULT_PROVIDER: Provider = "anthropic"

# Cache TTL (seconds) for LLM responses stored in Redis.
LLM_CACHE_TTL: int = int(os.environ.get("LLM_CACHE_TTL", "300"))


def load_env() -> None:
    """Load variables from a local .env file (if present) into os.environ."""
    if os.path.exists(".env"):
        with open(".env", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def get_current_provider() -> Provider:
    """Return the provider selected via LLM_PROVIDER (falls back to default)."""
    return os.environ.get("LLM_PROVIDER", DEFAULT_PROVIDER).lower()  # type: ignore[return-value]


def is_provider_configured(provider: str) -> bool:
    """Check whether the given provider has an API key available."""
    env_map = {
        "anthropic": "ANTHROPIC_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }
    return bool(os.environ.get(env_map.get(provider, "")))
