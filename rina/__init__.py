"""A.J. Rina package configuration.

`OPENAI_API_KEY` is Aura's canonical production variable. Earlier Railway
configuration used `OPEN_AI_KEY`; keep that spelling as a temporary compatibility
alias so existing deployments recover while the environment is corrected.
"""

from __future__ import annotations

import os

_CANONICAL_OPENAI_KEY = "OPENAI_API_KEY"
_LEGACY_OPENAI_KEY = "OPEN_AI_KEY"

if not os.getenv(_CANONICAL_OPENAI_KEY) and os.getenv(_LEGACY_OPENAI_KEY):
    os.environ[_CANONICAL_OPENAI_KEY] = os.environ[_LEGACY_OPENAI_KEY]
