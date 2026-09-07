"""Mileage observation and freshness semantics for Aura.

Aura never invents distance travelled. ``Car.current_mileage`` is the latest
known cumulative main-odometer projection, while ``MileageObservation`` keeps
where a reading came from, when it was observed and how strongly it was
verified.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from extensions import db
from mileage.models import MileageObservation
from models import Car, CarOwnership


MAX_ODOMETER_KM = 5_000_000
FRESH_DAYS = 30
REVIEW_DAYS = 90

SOURCE_LABELS = {
    "advisor_observation": "Advisor-observed odometer",
    "service_record": "Service record",
    "assessment": "Vehicle assessment",
    "client_report": "Client-reported odometer",
    "driver_report": "Driver-reported odometer",
    "ownership_transfer": "Ownership / onboarding record",
    "oem": "OEM / connected vehicle",
    "telematics": "Telematics",
    "legacy": "Legacy vehicle record",
}

VERIFICATION_LABELS = {
    "advisor_verified": "Advisor verified",
    "document_reviewed": "Supporting document reviewed",
    "client_reported": "Client reported",
    "driver_reported": "Driver reported",
    "system_verified": "System verified",
    "unverified": "Unverified",
    "legacy_unknown": "Provenance not yet recorded",
}

_SERVICE_VERIFICATION_MAP = {
    "unverified": "unverified",
    "document_reviewed": "document_reviewed",
    "advisor_confirmed": "advisor_verified",
}


class MileageObservationError(ValueError):
    """Raised when a mileage observation violates Aura's odometer rules."""


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalise_datetime(value: datetime | None) -> datetime:
    if value is None:
        return _utcnow_naive()
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _freshness(observed_at: datetime | None) -> tuple[str, str, int | None]:
    if observed_at is None:
        return "unknown", "Mileage update required", None

    age_days = max(0, (_utcnow_naive().date() - observed_at.date()).days)
    if age_days <= FRESH_DAYS:
        return "fresh", "Current", age_days
    if age_days <= REVIEW_DAYS:
        return "review_due", "Update recommended", age_days
    return "stale", "Stale — update required", age_days


@dataclass(frozen=True)
class MileageSnapshot:
    odometer_km: int | None
    observed_at: datetime | None
    source: str
    source_label: str
    verification_status: str
    verification_label: str
    freshness_status: str
    freshness_label: str
    age_days: int | None
    provenance_available: bool

    @property
    def needs_update(self) -> bool:
        return self.freshness_status in {"unknown", "review_due", "stale"}


class MileageObservationService:
    """Authoritative mileage observation rules and history queries."""

    @staticmethod
    def latest_current_observation(car_id: int) -> MileageObservation | None:
        return (
            MileageObservation.query.filter_by(
                car_id=car_id,
                is_historical=False,
            )
            .order_by(
                MileageObservation.observed_at.desc(),
                MileageObservation.id.desc(),
            )
            .first()
        )

    @staticmethod
    def record(
        *,
        car: Car,
        odometer_km: int,
        source: str,
        verification_status: str,
        observed_at: datetime | None = None,
        recorded_by_user_id: int | None = None,
        ownership_id: int | None = None,
        is_historical: bool = False,
        source_reference: str | None = None,
        evidence_reference: str | None = None,
        note: str | None = None,
        commit: bool = True,
    ) -> MileageObservation:
        if car.id is None:
            raise MileageObservationError(
                "Vehicle must be persisted before mileage is recorded."
            )

        try:
            reading = int(odometer_km)
        except (TypeError, ValueError) as exc:
            raise MileageObservationError(
                "Odometer reading must be a whole number."
            ) from exc

        if reading < 0 or reading > MAX_ODOMETER_KM:
            raise MileageObservationError(
                "Odometer reading must be between 0 and 5,000,000 km."
            )

        observed = _normalise_datetime(observed_at)
        if observed > _utcnow_naive() + timedelta(minutes=5):
            raise MileageObservationError("Odometer observation cannot be in the future.")

        current = car.current_mileage
        latest = MileageObservationService.latest_current_observation(car.id)

        if is_historical:
            if current is not None and reading > current:
                raise MileageObservationError(
                    "Historical odometer evidence cannot exceed the latest recorded odometer."
                )
        else:
            if current is not None and reading < current:
                raise MileageObservationError(
                    "A current odometer observation cannot move the vehicle mileage backwards."
                )
            if latest is not None and observed < latest.observed_at:
                raise MileageObservationError(
                    "A current odometer observation cannot predate the latest current observation. "
                    "Record older evidence as historical instead."
                )

        if source_reference:
            existing = MileageObservation.query.filter_by(
                car_id=car.id,
                source=source,
                source_reference=source_reference,
            ).first()
            if existing is not None:
                if (
                    existing.odometer_km != reading
                    or existing.observed_at != observed
                    or existing.is_historical != is_historical
                ):
                    raise MileageObservationError(
                        "Mileage source reference already exists with different data."
                    )
                return existing

        observation = MileageObservation(
            car_id=car.id,
            ownership_id=ownership_id,
            odometer_km=reading,
            observed_at=observed,
            source=source,
            verification_status=verification_status,
            recorded_by_user_id=recorded_by_user_id,
            is_historical=is_historical,
            source_reference=source_reference,
            evidence_reference=(evidence_reference or "").strip() or None,
            note=(note or "").strip() or None,
        )
        db.session.add(observation)

        # Car.current_mileage remains the compatibility projection consumed by
        # health, Rina and reporting. Only a current observation may advance it.
        if not is_historical and (current is None or reading > current):
            car.current_mileage = reading

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return observation

    @staticmethod
    def record_advisor_observation(
        *,
        car: Car,
        odometer_km: int,
        advisor_user_id: int,
        ownership: CarOwnership | None = None,
        observed_at: datetime | None = None,
        evidence_reference: str | None = None,
        note: str | None = None,
    ) -> MileageObservation:
        return MileageObservationService.record(
            car=car,
            odometer_km=odometer_km,
            source="advisor_observation",
            verification_status="advisor_verified",
            observed_at=observed_at,
            recorded_by_user_id=advisor_user_id,
            ownership_id=ownership.id if ownership else None,
            is_historical=False,
            evidence_reference=evidence_reference,
            note=note,
        )

    @staticmethod
    def record_service_snapshot(
        *,
        car: Car,
        ownership: CarOwnership,
        odometer_km: int,
        service_date: str,
        performed_by: int,
        event_metadata: dict | None,
        source_reference: str,
        entry_source: str,
    ) -> MileageObservation:
        metadata = event_metadata or {}
        record_mode = metadata.get("record_mode", "current")
        is_historical = record_mode == "historical"

        if is_historical:
            verification = _SERVICE_VERIFICATION_MAP.get(
                metadata.get("verification_status"),
                "unverified",
            )
        elif entry_source.startswith("admin"):
            verification = "advisor_verified"
        else:
            verification = "client_reported"

        return MileageObservationService.record(
            car=car,
            odometer_km=odometer_km,
            source="service_record",
            verification_status=verification,
            observed_at=datetime.fromisoformat(service_date),
            recorded_by_user_id=performed_by,
            ownership_id=ownership.id,
            is_historical=is_historical,
            source_reference=source_reference,
            note="Main-odometer snapshot attached to a service record.",
        )

    @staticmethod
    def snapshot(car: Car) -> MileageSnapshot:
        latest = MileageObservationService.latest_current_observation(car.id)

        # A legacy path may have advanced Car.current_mileage without writing an
        # observation. Never attribute that newer number to an older observation.
        if latest is None or (
            car.current_mileage is not None
            and latest.odometer_km != car.current_mileage
        ):
            freshness_status, freshness_label, age_days = _freshness(None)
            return MileageSnapshot(
                odometer_km=car.current_mileage,
                observed_at=None,
                source="legacy",
                source_label=SOURCE_LABELS["legacy"],
                verification_status="legacy_unknown",
                verification_label=VERIFICATION_LABELS["legacy_unknown"],
                freshness_status=freshness_status,
                freshness_label=freshness_label,
                age_days=age_days,
                provenance_available=False,
            )

        freshness_status, freshness_label, age_days = _freshness(latest.observed_at)
        return MileageSnapshot(
            odometer_km=latest.odometer_km,
            observed_at=latest.observed_at,
            source=latest.source,
            source_label=SOURCE_LABELS.get(
                latest.source,
                latest.source.replace("_", " ").title(),
            ),
            verification_status=latest.verification_status,
            verification_label=VERIFICATION_LABELS.get(
                latest.verification_status,
                latest.verification_status.replace("_", " ").title(),
            ),
            freshness_status=freshness_status,
            freshness_label=freshness_label,
            age_days=age_days,
            provenance_available=True,
        )

    @staticmethod
    def history(car_id: int, *, limit: int = 20) -> list[MileageObservation]:
        return (
            MileageObservation.query.filter_by(car_id=car_id)
            .order_by(
                MileageObservation.observed_at.desc(),
                MileageObservation.id.desc(),
            )
            .limit(limit)
            .all()
        )


def mileage_source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, source.replace("_", " ").title())


def mileage_verification_label(status: str) -> str:
    return VERIFICATION_LABELS.get(status, status.replace("_", " ").title())
