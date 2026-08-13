"""Environment-backed rollout controls for Wave 1.3 Rina orchestration."""

from __future__ import annotations

import os


_TRUTHY = {"1", "true", "yes", "on"}


def _env_bool(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY


def rina_orchestration_enabled() -> bool:
    """Gate the new authority/memory/provider orchestration path.

    Default-off keeps the current production chat route unchanged until the
    dedicated cutover PR explicitly enables it.
    """

    return _env_bool("RINA_ORCHESTRATION_ENABLED", default=False)


def rina_openai_provider_enabled() -> bool:
    """Gate outbound OpenAI provider calls independently of orchestration."""

    return _env_bool("RINA_OPENAI_PROVIDER_ENABLED", default=False)


def rina_openai_model() -> str:
    # Preserve Aura's existing provider model unless deployment deliberately
    # selects another supported model.
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
