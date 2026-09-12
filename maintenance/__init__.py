"""Aura Maintenance Intelligence domain.

The domain keeps verified maintenance knowledge separate from vehicle service
history and from deterministic maintenance-state projections.
"""

from maintenance.models import MaintenanceKnowledgeRule

__all__ = ["MaintenanceKnowledgeRule"]
