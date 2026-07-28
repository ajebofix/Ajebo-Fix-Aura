"""A.J. Rina package configuration.

`OPENAI_API_KEY` is Aura's canonical production variable. Earlier Railway
configuration used `OPEN_AI_KEY`; keep that spelling as a temporary compatibility
alias so existing deployments recover while the environment is corrected.
"""

from __future__ import annotations

import os

_CANONICAL_OPENAI_KEY = "OPENAI_API_KEY"
_LEGACY_OPENAI_KEY = "OPEN_AI_KEY"


def _normalise_openai_key() -> None:
    """Copy the configured key into the canonical variable without whitespace."""

    canonical_value = (os.getenv(_CANONICAL_OPENAI_KEY) or "").strip()
    legacy_value = (os.getenv(_LEGACY_OPENAI_KEY) or "").strip()
    resolved_value = canonical_value or legacy_value

    if resolved_value:
        os.environ[_CANONICAL_OPENAI_KEY] = resolved_value


_normalise_openai_key()
