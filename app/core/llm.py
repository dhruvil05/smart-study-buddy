"""Unified LLM provider layer.

Supports Anthropic, Google Gemini, and OpenRouter through a single `llm()`
interface. Provider selection is dynamic and can be switched at runtime via
`set_provider()` (used by the /api/provider endpoint).
"""
from __future__ import annotations
import os
import logging
from typing import Literal

from app.core.config import PROVIDER_MODELS, Provider, LLM_CACHE_TTL
from app.core.retry import retry, get_breaker

logger = logging.getLogger(__name__)

# Supported languages for localization
SUPPORTED_LANGUAGES = {
    "en": "English",      # English (default)
    "es": "Spanish",      # Spanish
    "fr": "French",       # French
    "zh": "Mandarin",     # Mandarin Chinese
    "de": "German",       # German
}

# Runtime provider state (mutated by set_provider)
_current_provider: Provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()  # type: ignore[assignment]
_current_language: str = os.environ.get("LLM_LANGUAGE", "en").lower()  # Default to English
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


def get_language_prompt_template(language: str) -> str:
    """Return a language-specific prompt template for localization."""
    templates = {
        "en": "Please explain the topic for English learners.",
        "es": "Por favor, explica el tema para estudiantes de español.",
        "fr": "S'il vous plaît, expliquez ce sujet pour des apprenants francophones.",
        "zh": "请为中文学习者解释这个主题。",
        "de": "Bitte erklären Sie das Thema für Deutschlerner.",
    }
    return templates.get(language, templates["en"])


def _add_language_context(prompt: str, language: str) -> str:
    """Add language context to the prompt for better localization."""
    lang_instruction = get_language_prompt_template(language)
    return f"{lang_instruction}\n\n{prompt}"


def _with_retry_and_breaker(provider: str, fn, *args, **kwargs) -> str:
    """Execute *fn* (the raw provider network call) with retry + circuit breaker.

    Returns the provider text on success.  On repeated failure (or an open
    breaker) returns an error sentinel that the rest of the pipeline treats as
    "no data" (see ``_is_error_response`` in ``app/api/main.py``).
    """
    breaker = get_breaker(provider)
    if breaker.is_open:
        return f"[CircuitBreaker error: {provider} unavailable]"
    try:
        text = retry(max_attempts=3)(fn)(*args, **kwargs)
        breaker.record_success()
        return text
    except Exception as e:  # noqa: BLE001
        breaker.record_failure()
        return f"[{provider.capitalize()} error: {e}]"


def _llm_anthropic(prompt: str, system: str, language: str = "en") -> str:
    def _call():
        full_system = f"{system}\n\n{get_language_prompt_template(language)}" if system else get_language_prompt_template(language)
        resp = _get_anthropic().messages.create(
            model=PROVIDER_MODELS["anthropic"],
            max_tokens=1024,
            system=full_system,
            messages=[{"role": "user", "content": _add_language_context(prompt, language)}],
        )
        return resp.content[0].text.strip()

    return _with_retry_and_breaker("anthropic", _call)


def _llm_gemini(prompt: str, system: str, language: str = "en") -> str:
    def _call():
        from google.genai import types
        full_system = f"{system}\n\n{get_language_prompt_template(language)}" if system else get_language_prompt_template(language)
        resp = _get_gemini().models.generate_content(
            model=PROVIDER_MODELS["gemini"],
            contents=f"{full_system}\n\n{_add_language_context(prompt, language)}",
            config=types.GenerateContentConfig(max_output_tokens=1024),
        )
        return resp.text.strip()

    return _with_retry_and_breaker("gemini", _call)


def _llm_openrouter(prompt: str, system: str, language: str = "en") -> str:
    def _call():
        client = _get_openrouter()
        full_system = f"{system}\n\n{get_language_prompt_template(language)}" if system else get_language_prompt_template(language)
        resp = client.chat.completions.create(
            model=PROVIDER_MODELS["openrouter"],
            max_tokens=1024,
            messages=[{"role": "user", "content": f"{full_system}\n\n{_add_language_context(prompt, language)}"}],
            temperature=0.7,
        )
        return resp.choices[0].message.content.strip()

    return _with_retry_and_breaker("openrouter", _call)


from app.core import cache as cache_mod

def llm(prompt: str, system: str = "", language: str = None) -> str:
    """Route a prompt to the currently selected provider with optional language parameter.

    Responses are cached in Redis (if available) using a deterministic key based on
    the provider, prompt, system message and language.  A cache hit returns the
    stored value directly, avoiding an external API call.
    """
    if language is None:
        language = get_current_language()

    # Try cache first – only cache successful (non‑error) responses.
    cached = cache_mod.get(_current_provider, prompt, system, language)
    if cached is not None:
        logger.info("LLM cache HIT for provider=%s lang=%s", _current_provider, language)
        return cached

    logger.info("LLM cache MISS for provider=%s lang=%s", _current_provider, language)

    if _current_provider == "openrouter":
        response = _llm_openrouter(prompt, system, language)
    elif _current_provider == "gemini":
        response = _llm_gemini(prompt, system, language)
    else:
        response = _llm_anthropic(prompt, system, language)

    # Store in cache if the call succeeded (i.e., not an error sentinel).
    is_error = response.lower().startswith("[") and "error" in response.lower()
    if not is_error:
        cache_mod.set(_current_provider, prompt, response, system, language, ttl=LLM_CACHE_TTL)
    return response


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


def current_provider() -> str:
    """Return the currently active provider (e.g. 'anthropic', 'gemini')."""
    return _current_provider


def has_provider(name: str) -> bool:
    """Return True if the given provider has a configured API key."""
    env_key = {
        "anthropic": "ANTHROPIC_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }
    key = env_key.get(name.lower())
    return bool(key and os.environ.get(key))


def get_current_language() -> str:
    """Return the language selected via LLM_LANGUAGE (falls back to default)."""
    return _current_language


def set_language(language: str) -> str:
    """Switch the active language.

    Args:
        language: Language code (e.g., 'en', 'es', 'fr', 'zh')

    Returns:
        The set language code

    Raises:
        ValueError: If language is not supported
    """
    global _current_language
    lang = language.lower()
    if lang not in SUPPORTED_LANGUAGES:
        raise ValueError(f"Language '{language}' not supported. Supported languages: {list(SUPPORTED_LANGUAGES.keys())}")
    _current_language = lang
    return _current_language


def has_language(language: str) -> bool:
    """Check if the given language is supported."""
    return language.lower() in SUPPORTED_LANGUAGES
