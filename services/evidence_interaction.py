"""Safe interaction metadata for the first Wave 1.4 evidence product workflow.

This module exposes no storage identifiers and performs no media retrieval. It
only supplies advisor-authorized pending evidence metadata for the vehicle-page
workflow; private bytes remain behind the protected retrieval service.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from evidence.models import VehicleEvidence
from models import Car
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


class EvidenceInteractionError(ValueError):
    """Base error for safe evidence interaction metadata."""


class EvidenceInteractionAccessError(EvidenceInteractionError):
    """Raised when the viewer lacks advisor authority for the vehicle."""


class EvidenceInteractionNotFound(EvidenceInteractionError):
    """Raised when the vehicle cannot be resolved."""


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
    records: tuple[PendingEvidenceItem, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "car_id": self.car_id,
            "record_count": len(self.records),
            "records": [item.to_dict() for item in self.records],
        }


def _uploader_label(evidence: VehicleEvidence) -> str:
    uploader = evidence.uploaded_by
    if uploader is None:
        return "Authorized vehicle user"
    return uploader.name or uploader.email or "Authorized vehicle user"


def get_advisor_pending_evidence_queue(
    *,
    car_id: int,
    viewer_user_id: int,
) -> PendingEvidenceQueue:
    """Return pending, privately available evidence metadata for advisor review."""

    if Car.query.filter_by(id=car_id).first() is None:
        raise EvidenceInteractionNotFound("Vehicle was not found.")

    authority = resolve_vehicle_authority(viewer_user_id, car_id)
    if authority not in {"advisor", "administrator"}:
        raise EvidenceInteractionAccessError(
            "Advisor authority is required for pending evidence review."
        )

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
        records=tuple(
            PendingEvidenceItem(
                evidence_id=row.id,
                evidence_type=row.evidence_type,
                purpose=row.purpose,
                purpose_label=_PURPOSE_LABELS.get(
                    row.purpose,
                    "Vehicle care evidence",
                ),
                visibility=row.visibility,
                uploaded_at=row.uploaded_at,
                uploader_label=_uploader_label(row),
                byte_size=int(row.byte_size),
                content_type=row.content_type,
            )
            for row in rows
        ),
    )
