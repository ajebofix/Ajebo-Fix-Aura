"""Environment-backed rollout controls for Wave 1.3 Rina orchestration."""

from __future__ import annotations

import os


_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def _optional_env_bool(name: str) -> bool | None:
    raw = os.getenv(name)
    if raw is None:
        return None
    normalized = raw.strip().lower()
    if normalized in _TRUTHY:
        return True
    if normalized in _FALSY:
        return False
    return None


def _openai_key_present() -> bool:
    """Recognise the canonical key plus Aura's documented Railway alias.

    ``OPENAI_API_KEY`` remains the production source of truth. ``OPEN_AI_KEY``
    is accepted only as a temporary compatibility path so deployments using the
    earlier variable spelling do not silently disable Rina's provider.
    """

    canonical = (os.getenv("OPENAI_API_KEY") or "").strip()
    legacy = (os.getenv("OPEN_AI_KEY") or "").strip()
    return bool(canonical or legacy)


def rina_orchestration_enabled() -> bool:
    """Gate the authority-first Rina orchestration path.

    Wave 1.3 chat cutover makes the new path the application default. A
    deployment can still disable it explicitly with
    ``RINA_ORCHESTRATION_ENABLED=false`` for a compatible emergency fallback.
    """

    configured = _optional_env_bool("RINA_ORCHESTRATION_ENABLED")
    return True if configured is None else configured


def rina_openai_provider_enabled() -> bool:
    """Gate outbound OpenAI calls without requiring a new deployment secret.

    An explicit ``RINA_OPENAI_PROVIDER_ENABLED`` setting wins. Otherwise Aura
    enables the provider when either the canonical ``OPENAI_API_KEY`` or the
    temporary documented ``OPEN_AI_KEY`` compatibility alias is present.
    """

    configured = _optional_env_bool("RINA_OPENAI_PROVIDER_ENABLED")
    if configured is not None:
        return configured
    return _openai_key_present()


def rina_openai_model() -> str:
    return (os.getenv("RINA_OPENAI_MODEL") or "gpt-4o-mini").strip()


def rina_openai_timeout_seconds() -> float:
    raw = (os.getenv("RINA_OPENAI_TIMEOUT_SECONDS") or "8").strip()
    try:
        value = float(raw)
    except ValueError:
        return 8.0
    return min(max(value, 2.0), 30.0)


def rina_openai_max_retries() -> int:
    raw = (os.getenv("RINA_OPENAI_MAX_RETRIES") or "1").strip()
    try:
        value = int(raw)
    except ValueError:
        return 1
    return min(max(value, 0), 2)
