"""Runtime controls for Aura Maintenance Intelligence.

M6 introduces one deliberately narrow operational kill switch.  The switch can
stop new Maintenance Intelligence projections and automated maintenance-signal
mutations without restoring any legacy generic interval semantics or rewriting
durable history.
"""

from __future__ import annotations

import os


MAINTENANCE_INTELLIGENCE_ENV = "AURA_MAINTENANCE_INTELLIGENCE_ENABLED"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on", "enabled"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", "disabled"})


def maintenance_intelligence_enabled() -> bool:
    """Return whether production Maintenance Intelligence behavior is enabled.

    The feature is enabled by default so an unset variable preserves normal
    production behavior.  Any explicitly invalid value fails closed rather than
    accidentally enabling maintenance automation during an incident.
    """

    raw = os.getenv(MAINTENANCE_INTELLIGENCE_ENV)
    if raw is None or not raw.strip():
        return True

    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    return False
