"""Explicit vehicle-scoped context resolution for A.J. Rina.

This is intentionally not the provider prompt builder.  It establishes the
trusted context envelope that later memory retrieval and provider composition
must consume.  Free-text vehicle names never change the scope in this module.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from models import (
    Car,
    CarFault,
    Consultation,
    TreatmentPlan,
    VehicleAssessment,
    VehicleProfile,
)
from services.concern_progression import (
    ConcernProgressionAccessError,
    ConcernProgressionNotFound,
    get_reported_concern_progression,
)
from services.rina_authority import (
    AUTHORITY_ADMINISTRATOR,
    AUTHORITY_ADVISOR,
    RinaAuthorityContext,
    resolve_rina_authority,
)


CONTEXT_VERSION = 1


@dataclass(frozen=True)
class RinaVehicleIdentityContext:
    car_id: int
    display_name: str
    vin: str
    current_mileage: int | None
    identity_source: str
    intelligence_source: str | None
    vin_decoded: bool
    verification_state: str


@dataclass(frozen=True)
class RinaRecordPointer:
    record_type: str
    record_id: int
    status: str | None
    visibility: str
    source: str


@dataclass(frozen=True)
class RinaProgressionPointer:
    concern_id: int
    current_state: str
    progression: str
    recurrence: bool | None
    evidence_event_ids: tuple[int, ...]
    explanation: str


@dataclass(frozen=True)
class RinaResolvedContext:
    context_version: int
    generated_at: datetime
    user_id: int
    car_id: int
    authority: str
    global_role: str
    relationships: tuple[str, ...]
    visibility_scope: tuple[str, ...]
    vehicle: RinaVehicleIdentityContext
    concern: RinaRecordPointer | None
    consultation: RinaRecordPointer | None
    assessment: RinaRecordPointer | None
    treatment_plan: RinaRecordPointer | None
    progression: RinaProgressionPointer | None
    allowed_actions: tuple[str, ...]
    denied_actions: tuple[str, ...]

    def to_safe_dict(self) -> dict[str, Any]:
        """Return the trusted context envelope without raw private record text."""

        payload = asdict(self)
        payload["generated_at"] = self.generated_at.isoformat()
        return payload


class RinaContextResolutionError(ValueError):
    """Raised when an explicit trusted context cannot be resolved."""


def _verification_state(profile: VehicleProfile | None) -> str:
    # Current VehicleProfile has source/decode state but no explicit
    # advisor-verification field.  Do not invent verification from decoding.
    if profile is None:
        return "not_recorded"
    return "not_recorded"


def _vehicle_identity(car: Car) -> RinaVehicleIdentityContext:
    profile = VehicleProfile.query.filter_by(car_id=car.id).first()
    return RinaVehicleIdentityContext(
        car_id=car.id,
        display_name=car.decoded_display_name,
        vin=car.vin,
        current_mileage=car.current_mileage,
        identity_source=car.vehicle_identity_source or "unknown",
        intelligence_source=profile.source if profile else None,
        vin_decoded=bool(profile and profile.vin_decoded),
        verification_state=_verification_state(profile),
    )


def _visibility_scope(authority: str) -> tuple[str, ...]:
    if authority in {AUTHORITY_ADVISOR, AUTHORITY_ADMINISTRATOR}:
        return ("client", "advisor", "internal")
    return ("client",)


def _current_concern(car_id: int) -> CarFault | None:
    return (
        CarFault.query.filter(
            CarFault.car_id == car_id,
            CarFault.status != "resolved",
        )
        .order_by(CarFault.created_at.desc(), CarFault.id.desc())
        .first()
    )


def _current_consultation(car_id: int) -> Consultation | None:
    return (
        Consultation.query.filter(
            Consultation.car_id == car_id,
            Consultation.status.in_(("scheduled", "in_progress")),
        )
        .order_by(Consultation.created_at.desc(), Consultation.id.desc())
        .first()
    )


def _current_assessment(car_id: int) -> VehicleAssessment | None:
    return (
        VehicleAssessment.query.filter_by(car_id=car_id)
        .order_by(VehicleAssessment.created_at.desc(), VehicleAssessment.id.desc())
        .first()
    )


def _current_treatment_plan(car_id: int) -> TreatmentPlan | None:
    return (
        TreatmentPlan.query.filter_by(car_id=car_id)
        .order_by(TreatmentPlan.created_at.desc(), TreatmentPlan.id.desc())
        .first()
    )


def _pointer(
    *,
    record_type: str,
    record: Any,
    visibility: str,
    source: str,
) -> RinaRecordPointer | None:
    if record is None:
        return None
    return RinaRecordPointer(
        record_type=record_type,
        record_id=int(record.id),
        status=getattr(record, "status", None),
        visibility=visibility,
        source=source,
    )


def _progression_pointer(
    *,
    authority_context: RinaAuthorityContext,
    concern: CarFault | None,
) -> RinaProgressionPointer | None:
    if concern is None:
        return None

    try:
        summary = get_reported_concern_progression(
            car_id=authority_context.car_id,
            concern_id=concern.id,
            viewer_user_id=authority_context.user_id,
        )
    except (ConcernProgressionAccessError, ConcernProgressionNotFound):
        # A future dedicated advisor role can be added to the Wave 1.2
        # progression resolver under an explicit compatibility PR.  Rina does
        # not weaken the existing resolver here merely to make context richer.
        return None

    return RinaProgressionPointer(
        concern_id=concern.id,
        current_state=summary.current_state,
        progression=summary.progression,
        recurrence=summary.recurrence,
        evidence_event_ids=summary.evidence_event_ids,
        explanation=summary.explanation,
    )


def resolve_rina_vehicle_context(
    *,
    user_id: int,
    car_id: int | None,
) -> RinaResolvedContext:
    """Resolve trusted Rina context for one explicit vehicle.

    ``car_id`` is intentionally required.  Callers that want to restore a
    short-lived session-bound vehicle must pass that identifier explicitly
    after validating the session; this service will then re-check authority.
    """

    if car_id is None:
        raise RinaContextResolutionError(
            "an explicit active vehicle is required before Rina context can load"
        )

    authority_context = resolve_rina_authority(user_id=user_id, car_id=car_id)
    car = Car.query.filter_by(id=car_id).first()
    if car is None:  # defensive; authority resolver already checks this
        raise RinaContextResolutionError("requested vehicle is unavailable")

    concern = _current_concern(car.id)
    consultation = _current_consultation(car.id)
    assessment = _current_assessment(car.id)
    treatment_plan = _current_treatment_plan(car.id)

    internal_visibility = (
        "advisor"
        if authority_context.authority
        in {AUTHORITY_ADVISOR, AUTHORITY_ADMINISTRATOR}
        else "client"
    )

    return RinaResolvedContext(
        context_version=CONTEXT_VERSION,
        generated_at=datetime.utcnow(),
        user_id=authority_context.user_id,
        car_id=car.id,
        authority=authority_context.authority,
        global_role=authority_context.global_role,
        relationships=authority_context.relationships,
        visibility_scope=_visibility_scope(authority_context.authority),
        vehicle=_vehicle_identity(car),
        concern=_pointer(
            record_type="reported_concern",
            record=concern,
            visibility="client",
            source="CarFault",
        ),
        consultation=_pointer(
            record_type="consultation",
            record=consultation,
            visibility="client",
            source="Consultation",
        ),
        assessment=_pointer(
            record_type="assessment",
            record=assessment,
            visibility=internal_visibility,
            source="VehicleAssessment",
        ),
        treatment_plan=_pointer(
            record_type="treatment_plan",
            record=treatment_plan,
            visibility=internal_visibility,
            source="TreatmentPlan",
        ),
        progression=_progression_pointer(
            authority_context=authority_context,
            concern=concern,
        ),
        allowed_actions=tuple(sorted(authority_context.allowed_actions)),
        denied_actions=tuple(sorted(authority_context.denied_actions)),
    )
