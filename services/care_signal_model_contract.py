"""Align SQLAlchemy metadata with the Wave 2.4C PostgreSQL contract.

Aura's legacy ``models.py`` is intentionally not rewritten wholesale in this
slice. The production migration removes the historical four-column alert
uniqueness constraint and permits system-authored VehicleEvents with no fake
``created_by`` user. This metadata extension keeps ``db.create_all()`` test
schemas and future model inspection consistent with that deployed contract.
"""

from __future__ import annotations

from sqlalchemy import Index, UniqueConstraint

from models import VehicleEvent, VehicleHealthAlert


_alert_table = VehicleHealthAlert.__table__

for _constraint in list(_alert_table.constraints):
    if (
        isinstance(_constraint, UniqueConstraint)
        and _constraint.name == "uq_active_health_alert"
    ):
        _alert_table.constraints.remove(_constraint)

if "uq_vehicle_health_alert_active_occurrence" not in {
    index.name for index in _alert_table.indexes
}:
    Index(
        "uq_vehicle_health_alert_active_occurrence",
        _alert_table.c.car_id,
        _alert_table.c.ownership_id,
        _alert_table.c.alert_type,
        unique=True,
        sqlite_where=_alert_table.c.is_active.is_(True),
        postgresql_where=_alert_table.c.is_active.is_(True),
    )

# System care-signal events deliberately have no human creator. The database
# migration makes this column nullable; mirror that fact in ORM metadata.
VehicleEvent.__table__.c.created_by.nullable = True
