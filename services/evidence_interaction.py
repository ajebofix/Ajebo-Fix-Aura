"""Safe metadata projections for Wave 1.4 evidence interaction surfaces.

This module intentionally exposes no private object identifiers and performs no
media retrieval or evidence mutation. It prepares authority-filtered metadata
for the submission and advisor-review UI while the existing intake, retrieval
and review services remain the only mutation/content boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from evidence.models import VehicleEvidence
from models import Car, CarFault
from security.access import resolve_vehicle_authority


_PURPOSE_LABELS = {
    "concern_support": "Reported concern support",
    "consultation_support": "Consultation support",
    "assessment_evidence": "Assessment evidence",
    "treatment_evidence": "Treatment evidence",
    "diagnostic_document": "Diagnostic document",
    "service_document": "Service document",
    "driver_observation": "Driver observation",
}
_OWNER_PURPOSES = (
    "concern_support",
    "consultation_support",
    "assessment_evidence",
    "treatment_evidence",
    "diagnostic_document",
    "service_document",
    "driver_observation",
)
_DRIVER_PURPOSES = ("concern_support", "driver_observation")

_ACCEPT_REASON_LABELS = {
    "advisor_verified": "Verified for the care record",
    "sufficient_for_record": "Sufficient supporting evidence",
}
_REJECT_REASON_LABELS = {
    "insufficient_quality": "Image quality is insufficient",
    "not_relevant": "Not relevant to this care record",
    "wrong_vehicle": "Does not match this vehicle",
    "duplicate": "Duplicate supporting evidence",
    "privacy_restriction": "Privacy restriction",
}


class EvidenceInteractionError(ValueError):
    """Base error for safe evidence interaction metadata."""


class EvidenceInteractionAccessError(EvidenceInteractionError):
    """Raised when the viewer lacks the required vehicle authority."""


class EvidenceInteractionNotFound(EvidenceInteractionError):
    """Raised when a requested vehicle/evidence record cannot be resolved."""


@dataclass(frozen=True)
class ChoiceOption:
    value: str
    label: str

    def to_dict(self) -> dict[str, str]:
        return {"value": self.value, "label": self.label}


@dataclass(frozen=True)
class EvidenceSubmissionContext:
    car_id: int
    vehicle_label: str
    viewer_authority: str
    purposes: tuple[ChoiceOption, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "car_id": self.car_id,
            "vehicle_label": self.vehicle_label,
            "viewer_authority": self.viewer_authority,
            "purposes": [item.to_dict() for item in self.purposes],
        }


@dataclass(frozen=True)
class PendingEvidenceItem:
    evidence_id: int
    evidence_type: str
    purpose: str
    purpose_label: str
    visibility: str
    uploaded_at: datetime
    uploader_label: str
    byte_size: int
    content_type: str

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_id": self.evidence_id,
            "evidence_type": self.evidence_type,
            "purpose": self.purpose,
            "purpose_label": self.purpose_label,
            "visibility": self.visibility,
            "uploaded_at": self.uploaded_at.isoformat(),
            "uploader_label": self.uploader_label,
            "byte_size": self.byte_size,
            "content_type": self.content_type,
        }


@dataclass(frozen=True)
class PendingEvidenceQueue:
    car_id: int
    vehicle_label: str
    records: tuple[PendingEvidenceItem, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "car_id": self.car_id,
            "vehicle_label": self.vehicle_label,
            "record_count": len(self.records),
            "records": [item.to_dict() for item in self.records],
        }


@dataclass(frozen=True)
class ReportedConcernOption:
    concern_id: int
    title: str
    status: str
    reported_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "concern_id": self.concern_id,
            "title": self.title,
            "status": self.status,
            "reported_at": self.reported_at.isoformat(),
        }


@dataclass(frozen=True)
class EvidenceReviewWorkspace:
    car_id: int
    vehicle_label: str
    evidence: PendingEvidenceItem
    concerns: tuple[ReportedConcernOption, ...]
    accept_reasons: tuple[ChoiceOption, ...]
    reject_reasons: tuple[ChoiceOption, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "car_id": self.car_id,
            "vehicle_label": self.vehicle_label,
            "evidence": self.evidence.to_dict(),
            "concerns": [item.to_dict() for item in self.concerns],
            "accept_reasons": [item.to_dict() for item in self.accept_reasons],
            "reject_reasons": [item.to_dict() for item in self.reject_reasons],
        }


def _vehicle_label(car: Car) -> str:
    return car.decoded_display_name or car.display_name


def _uploader_label(evidence: VehicleEvidence) -> str:
    uploader = evidence.uploaded_by
    if uploader is None:
        return "Authorized vehicle user"
    return uploader.name or uploader.email or "Authorized vehicle user"


def _pending_item(evidence: VehicleEvidence) -> PendingEvidenceItem:
    return PendingEvidenceItem(
        evidence_id=evidence.id,
        evidence_type=evidence.evidence_type,
        purpose=evidence.purpose,
        purpose_label=_PURPOSE_LABELS.get(
            evidence.purpose,
            "Vehicle care evidence",
        ),
        visibility=evidence.visibility,
        uploaded_at=evidence.uploaded_at,
        uploader_label=_uploader_label(evidence),
        byte_size=int(evidence.byte_size),
        content_type=evidence.content_type,
    )


def _advisor_authority(*, viewer_user_id: int, car_id: int) -> None:
    authority = resolve_vehicle_authority(viewer_user_id, car_id)
    if authority not in {"advisor", "administrator"}:
        raise EvidenceInteractionAccessError(
            "Advisor authority is required for pending evidence review."
        )


def get_evidence_submission_context(
    *,
    car_id: int,
    viewer_user_id: int,
) -> EvidenceSubmissionContext:
    """Return the allowed image-submission choices for an owner or driver."""

    car = Car.query.filter_by(id=car_id).first()
    if car is None:
        raise EvidenceInteractionNotFound("Vehicle was not found.")

    authority = resolve_vehicle_authority(viewer_user_id, car_id)
    if authority == "owner":
        purposes = _OWNER_PURPOSES
    elif authority == "driver":
        purposes = _DRIVER_PURPOSES
    else:
        raise EvidenceInteractionAccessError(
            "This submission surface is reserved for vehicle owners and assigned drivers."
        )

    return EvidenceSubmissionContext(
        car_id=car.id,
        vehicle_label=_vehicle_label(car),
        viewer_authority=authority,
        purposes=tuple(
            ChoiceOption(value=value, label=_PURPOSE_LABELS[value])
            for value in purposes
        ),
    )


def get_advisor_pending_evidence_queue(
    *,
    car_id: int,
    viewer_user_id: int,
) -> PendingEvidenceQueue:
    """Return pending, privately available evidence metadata for advisor review."""

    car = Car.query.filter_by(id=car_id).first()
    if car is None:
        raise EvidenceInteractionNotFound("Vehicle was not found.")

    _advisor_authority(viewer_user_id=viewer_user_id, car_id=car_id)

    rows = (
        VehicleEvidence.query.filter(
            VehicleEvidence.car_id == car_id,
            VehicleEvidence.evidence_type == "image",
            VehicleEvidence.review_status == "pending_review",
            VehicleEvidence.storage_state == "available",
            VehicleEvidence.deleted_at.is_(None),
        )
        .order_by(VehicleEvidence.uploaded_at.asc(), VehicleEvidence.id.asc())
        .all()
    )

    return PendingEvidenceQueue(
        car_id=car_id,
        vehicle_label=_vehicle_label(car),
        records=tuple(_pending_item(row) for row in rows),
    )


def get_advisor_evidence_review_workspace(
    *,
    evidence_id: int,
    viewer_user_id: int,
) -> EvidenceReviewWorkspace:
    """Return one pending evidence record plus safe same-vehicle concern choices."""

    evidence = VehicleEvidence.query.filter(
        VehicleEvidence.id == evidence_id,
        VehicleEvidence.evidence_type == "image",
        VehicleEvidence.review_status == "pending_review",
        VehicleEvidence.storage_state == "available",
        VehicleEvidence.deleted_at.is_(None),
    ).first()
    if evidence is None:
        raise EvidenceInteractionNotFound(
            "Pending evidence was not found or is no longer available for review."
        )

    car = Car.query.filter_by(id=evidence.car_id).first()
    if car is None:
        raise EvidenceInteractionNotFound("Vehicle was not found.")

    _advisor_authority(
        viewer_user_id=viewer_user_id,
        car_id=evidence.car_id,
    )

    concerns = (
        CarFault.query.filter_by(car_id=evidence.car_id)
        .order_by(CarFault.reported_at.desc(), CarFault.id.desc())
        .limit(50)
        .all()
    )

    concern_options = []
    for concern in concerns:
        fallback = (concern.description or "").strip()
        title = (concern.title or "").strip() or fallback[:90]
        if not title:
            title = f"Reported Concern #{concern.id}"
        concern_options.append(
            ReportedConcernOption(
                concern_id=concern.id,
                title=title,
                status=concern.status,
                reported_at=concern.reported_at,
            )
        )

    return EvidenceReviewWorkspace(
        car_id=car.id,
        vehicle_label=_vehicle_label(car),
        evidence=_pending_item(evidence),
        concerns=tuple(concern_options),
        accept_reasons=tuple(
            ChoiceOption(value=value, label=label)
            for value, label in _ACCEPT_REASON_LABELS.items()
        ),
        reject_reasons=tuple(
            ChoiceOption(value=value, label=label)
            for value, label in _REJECT_REASON_LABELS.items()
        ),
    )
