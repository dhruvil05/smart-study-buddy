"""Unified LLM provider layer.

Supports Anthropic, Google Gemini, and OpenRouter through a single `llm()`
interface. Provider selection is dynamic and can be switched at runtime via
`set_provider()` (used by the /api/provider endpoint).
"""
from __future__ import annotations
import os
from typing import Literal

from app.core.config import PROVIDER_MODELS, Provider

# Runtime provider state (mutated by set_provider)
_current_provider: Provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()  # type: ignore[assignment]
_anthropic_client = None
_gemini_client = None
_openrouter_client = None


# ── Client factories (lazy, cached) ───────────────────────────────────────────

def _get_anthropic():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        _anthropic_client = anthropic.Anthropic(api_key=key)
    return _anthropic_client


def _get_gemini():
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        key = os.environ.get("GEMINI_API_KEY", "")
        if not key:
            raise RuntimeError("GEMINI_API_KEY not set")
        _gemini_client = genai.Client(api_key=key)
    return _gemini_client


def _get_openrouter():
    global _openrouter_client
    if _openrouter_client is None:
        import openai
        key = os.environ.get("OPENROUTER_API_KEY", "")
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY not set")
        _openrouter_client = openai.OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=key,
        )
    return _openrouter_client


# ── Provider-specific calls ───────────────────────────────────────────────────

def _llm_anthropic(prompt: str, system: str) -> str:
    try:
        resp = _get_anthropic().messages.create(
            model=PROVIDER_MODELS["anthropic"],
            max_tokens=1024,
            system=system or "You are a helpful study assistant.",
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:  # noqa: BLE001 - surface as recoverable string
        return f"[Anthropic error: {e}]"


def _llm_gemini(prompt: str, system: str) -> str:
    try:
        from google.genai import types
        full = f"{system}\n\n{prompt}" if system else prompt
        resp = _get_gemini().models.generate_content(
            model=PROVIDER_MODELS["gemini"],
            contents=full,
            config=types.GenerateContentConfig(max_output_tokens=1024),
        )
        return resp.text.strip()
    except Exception as e:  # noqa: BLE001
        return f"[Gemini error: {e}]"


def _llm_openrouter(prompt: str, system: str) -> str:
    try:
        client = _get_openrouter()
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        resp = client.chat.completions.create(
            model=PROVIDER_MODELS["openrouter"],
            max_tokens=1024,
            messages=[{"role": "user", "content": full_prompt}],
            temperature=0.7,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:  # noqa: BLE001
        return f"[OpenRouter error: {e}]"


def llm(prompt: str, system: str = "") -> str:
    """Route a prompt to the currently selected provider."""
    if _current_provider == "openrouter":
        return _llm_openrouter(prompt, system)
    elif _current_provider == "gemini":
        return _llm_gemini(prompt, system)
    return _llm_anthropic(prompt, system)


def set_provider(provider: str) -> Provider:
    """Switch the active provider, initializing its client lazily.

    Raises RuntimeError if the provider lacks a configured API key.
    """
    global _current_provider, _anthropic_client, _gemini_client, _openrouter_client
    p = provider.lower()
    if p not in ("anthropic", "gemini", "openrouter"):
        raise ValueError("Provider must be 'anthropic', 'gemini', or 'openrouter'")

    if p == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("Anthropic API key required")
        import anthropic as _ac
        _anthropic_client = _ac.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    elif p == "gemini":
        if not os.environ.get("GEMINI_API_KEY"):
            raise RuntimeError("Gemini API key required")
        from google import genai as _gc
        _gemini_client = _gc.Client(api_key=os.environ["GEMINI_API_KEY"])
    else:  # openrouter
        if not os.environ.get("OPENROUTER_API_KEY"):
            raise RuntimeError("OpenRouter API key required")
        import openai as _oa
        _openrouter_client = _oa.OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
        )

    _current_provider = p  # type: ignore[assignment]
    return _current_provider


def current_provider() -> Provider:
    return _current_provider  # type: ignore[return-value]


def has_provider(provider: str) -> bool:
    env_map = {
        "anthropic": "ANTHROPIC_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }
    return bool(os.environ.get(env_map.get(provider, "")))
