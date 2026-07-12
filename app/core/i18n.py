"""Localization, language detection, and language-aware content filtering.

Part of Phase 2.1 (Multi-Language Support). Provides:
  * SUPPORTED_LANGUAGES        — canonical code → display name map (single source of truth)
  * detect_language(text)      — best-effort detection of the input language
  * localize(key, language)    — UI / system string lookup
  * filter_content(text, lang) — language-aware safety + language checks

Detection is intentionally lightweight (no external dependency) so the project
keeps its UV-only dependency footprint. It recognises script (CJK vs Latin) and
a small set of high-frequency stopwords for the supported languages.
"""
from __future__ import annotations

# Re-used from llm.py so there is a single source of truth for supported codes.
from app.core.llm import SUPPORTED_LANGUAGES

# ── Language detection ─────────────────────────────────────────────────────────

# High-frequency function-word signals per language (low false-positive rate).
_STOPWORDS: dict[str, set[str]] = {
    "es": {"el", "la", "los", "las", "de", "que", "y", "en", "un", "una", "por", "con", "para", "como", "pero", "su", "se", "lo", "es", "son", "fue"},
    "fr": {"le", "la", "les", "de", "du", "des", "et", "un", "une", "que", "qui", "dans", "pour", "avec", "sur", "au", "aux", "est", "sont", "ce", "en"},
    "zh": set(),  # handled by script detection below
    "de": {"der", "die", "das", "und", "ist", "ein", "eine", "zu", "den", "in", "den", "mit", "für", "auf", "nicht", "von", "im", "es", "sich", "auch"},
    "en": {"the", "and", "of", "to", "a", "in", "is", "that", "for", "it", "with", "as", "was", "on", "are", "this", "be", "at"},
}


def _has_cjk(text: str) -> bool:
    """Return True if the text contains a meaningful amount of CJK characters."""
    cjk = [c for c in text if "一" <= c <= "鿿"]
    # Require at least 2 CJK chars to avoid accidental matches.
    return len(cjk) >= 2


def detect_language(text: str) -> str:
    """Best-effort language detection.

    Returns one of SUPPORTED_LANGUAGES keys, defaulting to 'en'.
    """
    if not text or not text.strip():
        return "en"

    lowered = text.lower()
    tokens = {t.strip(".,!?;:()[]\"'") for t in lowered.split() if t}

    # 1) Script-based detection first (most reliable for Mandarin).
    if _has_cjk(text):
        return "zh"

    # 2) Stopword overlap scoring for Latin-script languages.
    scores: dict[str, int] = {}
    for lang, words in _STOPWORDS.items():
        if not words:
            continue
        overlap = len(tokens & words)
        if overlap:
            scores[lang] = overlap

    if scores:
        # Highest overlap wins; ties broken by the canonical order below.
        # Threshold of 1 is safe because the per-language stopword sets are
        # distinctive (e.g. "el"/"la" are not English words), so a single strong
        # signal is enough to prefer that language over the English default.
        order = ["en", "es", "fr", "de"]
        best = max(scores, key=lambda l: (scores[l], -order.index(l) if l in order else 0))
        if scores[best] >= 1:
            return best

    return "en"


# ── UI / system string localization ───────────────────────────────────────────

# Keys used by the API + frontend chrome. Add new keys here and they become
# available in every supported language.
_STRINGS: dict[str, dict[str, str]] = {
    "welcome": {
        "en": "Welcome to Smart Study Buddy",
        "es": "Bienvenido a Smart Study Buddy",
        "fr": "Bienvenue sur Smart Study Buddy",
        "zh": "欢迎使用智能学习助手",
        "de": "Willkommen bei Smart Study Buddy",
    },
    "topic_prompt": {
        "en": "Enter a topic to study",
        "es": "Ingresa un tema para estudiar",
        "fr": "Saisissez un sujet à étudier",
        "zh": "输入要学习的主题",
        "de": "Gib ein Thema zum Lernen ein",
    },
    "generate": {
        "en": "Generate Study Materials",
        "es": "Generar material de estudio",
        "fr": "Générer du matériel d'étude",
        "zh": "生成学习材料",
        "de": "Lernmaterial erstellen",
    },
    "language_label": {
        "en": "Language",
        "es": "Idioma",
        "fr": "Langue",
        "zh": "语言",
        "de": "Sprache",
    },
    "explanation": {
        "en": "Explanation",
        "es": "Explicación",
        "fr": "Explication",
        "zh": "解释",
        "de": "Erklärung",
    },
    "quiz": {
        "en": "Quiz",
        "es": "Cuestionario",
        "fr": "Quiz",
        "zh": "测验",
        "de": "Quiz",
    },
    "flashcards": {
        "en": "Flashcards",
        "es": "Tarjetas de memoria",
        "fr": "Cartes mémoire",
        "zh": "闪卡",
        "de": "Lernkarten",
    },
    "empty_topic_error": {
        "en": "Topic cannot be empty",
        "es": "El tema no puede estar vacío",
        "fr": "Le sujet ne peut pas être vide",
        "zh": "主题不能为空",
        "de": "Das Thema darf nicht leer sein",
    },
    "rate_limit_error": {
        "en": "Rate limit exceeded",
        "es": "Límite de velocidad excedido",
        "fr": "Limite de débit dépassée",
        "zh": "超出速率限制",
        "de": "Ratenbegrenzung überschritten",
    },
}


def localize(key: str, language: str, default: str | None = None) -> str:
    """Return the localized string for `key` in `language`.

    Falls back to English, then to `default`, then to the key itself.
    """
    lang = language if language in SUPPORTED_LANGUAGES else "en"
    return _STRINGS.get(key, {}).get(lang, default or _STRINGS.get(key, {}).get("en", key))


def get_all_strings(language: str) -> dict[str, str]:
    """Return every localized UI string for `language` as a flat key→string map."""
    lang = language if language in SUPPORTED_LANGUAGES else "en"
    return {
        key: strings.get(lang, strings.get("en", key))
        for key, strings in _STRINGS.items()
    }


# ── Language-aware content filters ─────────────────────────────────────────────

# Profanity / unsafe tokens kept generic + minimal; the goal is a safety net
# that is sensitive to the active language rather than an exhaustive blocklist.
_UNSAFE_TOKENS: dict[str, set[str]] = {
    "en": {"spam", "hate", "kill", "bomb"},
    "es": {"spam", "odio", "matar", "bomba"},
    "fr": {"spam", "haine", "tuer", "bombe"},
    "zh": {"垃圾", "仇恨", "炸弹"},
    "de": {"spam", "hass", "töten", "bombe"},
}


def filter_content(text: str, language: str) -> tuple[bool, str]:
    """Language-aware content filter.

    Returns (passed, reason). `passed` is False when unsafe content is detected
    for the active language. This keeps the filter sensitive to the language
    actually in use rather than applying a single-language blocklist.
    """
    if not text:
        return True, ""
    lowered = text.lower()
    blocked = _UNSAFE_TOKENS.get(language, _UNSAFE_TOKENS["en"])
    for token in blocked:
        if token in lowered:
            return False, f"Content blocked by language-aware filter ({language})."
    return True, ""


def is_supported(language: str) -> bool:
    """True if `language` is a supported language code."""
    return language in SUPPORTED_LANGUAGES
