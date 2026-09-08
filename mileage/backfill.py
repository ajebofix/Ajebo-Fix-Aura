"""Idempotent backfill helpers for legacy service mileage evidence.

This module deliberately uses SQLAlchemy Core instead of application ORM models so
it can be called safely from an Alembic migration. It never changes
``cars.current_mileage``. Existing service events are projected into
``mileage_observations`` with stable source references, and existing mileage
observations are left untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json

import sqlalchemy as sa


_SERVICE_VERIFICATION_MAP = {
    "unverified": "unverified",
    "document_reviewed": "document_reviewed",
    "advisor_confirmed": "advisor_verified",
}


@dataclass(frozen=True)
class LegacyServiceMileageBackfillResult:
    scanned: int
    inserted: int
    skipped_existing: int
    skipped_invalid: int


def _event_metadata(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def backfill_legacy_service_mileage_observations(bind) -> LegacyServiceMileageBackfillResult:
    """Project existing service-event odometer snapshots into mileage history.

    Rules:
    - only non-deleted service events with a valid non-negative mileage are used;
    - the service event fingerprint becomes the stable source reference;
    - an existing ``service_record`` observation with that source reference wins;
    - explicit historical/current service metadata is preserved;
    - legacy records without verification metadata use ``legacy_unknown`` rather
      than inventing a verification state;
    - legacy readings below the car's latest odometer are historical;
    - an older equal reading is also historical when a newer current observation
      already exists for that car;
    - ``cars.current_mileage`` is never updated by this backfill.
    """

    metadata = sa.MetaData()
    events = sa.Table("vehicle_events", metadata, autoload_with=bind)
    cars = sa.Table("cars", metadata, autoload_with=bind)
    observations = sa.Table("mileage_observations", metadata, autoload_with=bind)

    latest_current_rows = bind.execute(
        sa.select(
            observations.c.car_id,
            sa.func.max(observations.c.observed_at).label("latest_observed_at"),
        )
        .where(observations.c.is_historical.is_(False))
        .group_by(observations.c.car_id)
    ).mappings()
    latest_current_by_car = {
        row["car_id"]: row["latest_observed_at"] for row in latest_current_rows
    }

    existing_rows = bind.execute(
        sa.select(observations.c.car_id, observations.c.source_reference).where(
            observations.c.source == "service_record",
            observations.c.source_reference.is_not(None),
        )
    )
    existing_refs = {(row[0], row[1]) for row in existing_rows}

    is_deleted_column = events.c.get("is_deleted")
    event_data_column = events.c.get("data")
    event_source_column = events.c.get("source")
    created_by_column = events.c.get("created_by")
    ownership_column = events.c.get("ownership_id")
    fingerprint_column = events.c.get("fingerprint")

    columns = [
        events.c.id.label("event_id"),
        events.c.car_id,
        events.c.mileage,
        events.c.created_at.label("service_date"),
        cars.c.current_mileage.label("car_current_mileage"),
    ]
    if ownership_column is not None:
        columns.append(ownership_column)
    if created_by_column is not None:
        columns.append(created_by_column)
    if event_source_column is not None:
        columns.append(event_source_column)
    if event_data_column is not None:
        columns.append(event_data_column)
    if fingerprint_column is not None:
        columns.append(fingerprint_column)

    query = (
        sa.select(*columns)
        .select_from(events.join(cars, events.c.car_id == cars.c.id))
        .where(events.c.event_type == "service", events.c.mileage.is_not(None))
        .order_by(events.c.id.asc())
    )
    if is_deleted_column is not None:
        query = query.where(is_deleted_column.is_(False))

    scanned = 0
    inserted = 0
    skipped_existing = 0
    skipped_invalid = 0

    for row in bind.execute(query).mappings():
        scanned += 1

        try:
            reading = int(row["mileage"])
        except (TypeError, ValueError):
            skipped_invalid += 1
            continue
        if reading < 0 or reading > 5_000_000 or row["service_date"] is None:
            skipped_invalid += 1
            continue

        source_reference = row.get("fingerprint") or f"legacy-service-event:{row['event_id']}"
        ref_key = (row["car_id"], source_reference)
        if ref_key in existing_refs:
            skipped_existing += 1
            continue

        event_metadata = _event_metadata(row.get("data"))
        record_mode = event_metadata.get("record_mode")
        latest_current_at = latest_current_by_car.get(row["car_id"])

        if record_mode == "historical":
            is_historical = True
        elif record_mode == "current":
            is_historical = False
        else:
            current_mileage = row["car_current_mileage"]
            is_historical = bool(
                (current_mileage is not None and reading < current_mileage)
                or (
                    latest_current_at is not None
                    and row["service_date"] < latest_current_at
                )
            )

        if record_mode is None:
            verification_status = "legacy_unknown"
        elif is_historical:
            verification_status = _SERVICE_VERIFICATION_MAP.get(
                event_metadata.get("verification_status"),
                "legacy_unknown",
            )
        else:
            event_source = (row.get("source") or "").lower()
            verification_status = (
                "advisor_verified" if event_source.startswith("admin") else "client_reported"
            )

        bind.execute(
            observations.insert().values(
                car_id=row["car_id"],
                ownership_id=row.get("ownership_id"),
                odometer_km=reading,
                observed_at=row["service_date"],
                recorded_at=_utcnow_naive(),
                source="service_record",
                verification_status=verification_status,
                recorded_by_user_id=row.get("created_by"),
                is_historical=is_historical,
                source_reference=source_reference,
                evidence_reference=None,
                note=(
                    "Backfilled from an existing service record during the mileage "
                    "observation foundation rollout."
                ),
            )
        )
        existing_refs.add(ref_key)
        inserted += 1

    return LegacyServiceMileageBackfillResult(
        scanned=scanned,
        inserted=inserted,
        skipped_existing=skipped_existing,
        skipped_invalid=skipped_invalid,
    )
