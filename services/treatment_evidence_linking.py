"""Governed evidence linkage for Aura Wave 2.3C treatment records.

This module extends the established Wave 1.4 evidence-link safety pattern to
Treatment Actions and Treatment Outcomes. It deliberately never commits: the
outer Treatment Action/Outcome coordinator owns the transaction so professional
record mutation, EvidenceLink rows, evidence.linked events and treatment events
succeed or roll back together.
"""

from __future__ import annotations

from dataclasses import dataclass

from evidence.models import EvidenceLink, VehicleEvidence
from extensions import db
from security.access import resolve_vehicle_authority
from services.event_emission import emit_vehicle_event
from treatment.models import TreatmentAction, TreatmentOutcome


class TreatmentEvidenceLinkError(RuntimeError):
    """Base safe failure for Treatment Action/Outcome evidence linkage."""


class TreatmentEvidenceLinkAuthorityError(TreatmentEvidenceLinkError):
    """Raised when the actor lacks professional vehicle authority."""


class TreatmentEvidenceLinkConflict(TreatmentEvidenceLinkError):
    """Raised when evidence cannot safely support the requested subject."""


class TreatmentEvidenceLinkNotFound(TreatmentEvidenceLinkError):
    """Raised when evidence or the target treatment subject is missing."""


@dataclass(frozen=True)
class TreatmentEvidenceLinkResult:
    link_id: int
    evidence_id: int
    car_id: int
    subject_type: str
    subject_id: int
    relationship_type: str
    created: bool


def _subject(*, subject_type: str, subject_id: int):
    if subject_type == "treatment_action":
        target = db.session.get(TreatmentAction, subject_id)
    elif subject_type == "treatment_outcome":
        target = db.session.get(TreatmentOutcome, subject_id)
    else:
        raise TreatmentEvidenceLinkConflict(
            "Treatment evidence subject must be treatment_action or treatment_outcome."
        )

    if target is None:
        raise TreatmentEvidenceLinkNotFound("Treatment evidence subject was not found.")
    return target


def _require_advisor(*, actor_user_id: int, car_id: int) -> str:
    authority = resolve_vehicle_authority(actor_user_id, car_id)
    if authority not in {"advisor", "administrator"}:
        raise TreatmentEvidenceLinkAuthorityError(
            "Advisor authority is required for treatment evidence linkage."
        )
    return authority


def _validate_evidence_visibility(*, evidence: VehicleEvidence, target_visibility: str) -> None:
    # A client-visible professional fact may reference only evidence that the
    # client is already permitted to know exists. Advisor-visible facts may
    # reference client/advisor evidence, but never internal-only evidence.
    if target_visibility == "client" and evidence.visibility != "client":
        raise TreatmentEvidenceLinkConflict(
            "Client-visible treatment facts require client-visible supporting evidence."
        )
    if target_visibility == "advisor" and evidence.visibility not in {"client", "advisor"}:
        raise TreatmentEvidenceLinkConflict(
            "Advisor-visible treatment facts cannot expose internal-only evidence references."
        )


def _emit_link_event(
    *,
    evidence: VehicleEvidence,
    link: EvidenceLink,
    actor_user_id: int,
) -> None:
    if link.id is None or link.created_at is None:
        raise TreatmentEvidenceLinkConflict("Treatment evidence link metadata is incomplete.")

    emit_vehicle_event(
        car_id=evidence.car_id,
        event_type="evidence.linked",
        subject_type="vehicle_evidence",
        subject_id=evidence.id,
        actor_type="user",
        actor_user_id=actor_user_id,
        visibility=evidence.visibility,
        source="evidence.link.treatment",
        occurred_at=link.created_at,
        title="Evidence linked to treatment record",
        progression_direction="not_applicable",
        idempotency_key=f"evidence-link:{link.id}",
        evidence_refs=[{"type": "vehicle_evidence", "id": evidence.id}],
        data={
            "link_id": link.id,
            "linked_subject_type": link.subject_type,
            "linked_subject_id": link.subject_id,
            "relationship_type": link.relationship_type,
        },
    )


def link_accepted_evidence_to_treatment_subject(
    *,
    actor_user_id: int,
    evidence_id: int,
    subject_type: str,
    subject_id: int,
    relationship_type: str = "supports",
) -> TreatmentEvidenceLinkResult:
    """Create/reconcile one accepted same-vehicle treatment evidence link.

    This function never commits or rolls back. The caller must own the outer
    transaction.
    """

    relationship = (relationship_type or "").strip().lower()
    if relationship not in {"supports", "documents"}:
        raise TreatmentEvidenceLinkConflict(
            "Treatment evidence relationship must be supports or documents."
        )

    target = _subject(subject_type=subject_type, subject_id=subject_id)
    _require_advisor(actor_user_id=actor_user_id, car_id=target.car_id)

    evidence = db.session.get(VehicleEvidence, evidence_id)
    if evidence is None:
        raise TreatmentEvidenceLinkNotFound("Vehicle evidence was not found.")
    if evidence.car_id != target.car_id:
        raise TreatmentEvidenceLinkAuthorityError(
            "Evidence and treatment subject must belong to the same vehicle."
        )
    if evidence.review_status != "accepted":
        raise TreatmentEvidenceLinkConflict(
            "Only advisor-accepted evidence may support a treatment record."
        )
    if evidence.storage_state != "available" or evidence.deleted_at is not None:
        raise TreatmentEvidenceLinkConflict(
            "Supporting treatment evidence must be available and not deleted."
        )

    target_visibility = getattr(target, "visibility", "client") or "client"
    _validate_evidence_visibility(
        evidence=evidence,
        target_visibility=target_visibility,
    )

    existing = EvidenceLink.query.filter_by(
        evidence_id=evidence.id,
        car_id=evidence.car_id,
        subject_type=subject_type,
        subject_id=subject_id,
        relationship_type=relationship,
    ).first()

    created = False
    if existing is None:
        existing = EvidenceLink(
            evidence_id=evidence.id,
            car_id=evidence.car_id,
            subject_type=subject_type,
            subject_id=subject_id,
            relationship_type=relationship,
            created_by_user_id=actor_user_id,
        )
        db.session.add(existing)
        db.session.flush()
        created = True

    _emit_link_event(
        evidence=evidence,
        link=existing,
        actor_user_id=(existing.created_by_user_id or actor_user_id),
    )

    return TreatmentEvidenceLinkResult(
        link_id=existing.id,
        evidence_id=evidence.id,
        car_id=evidence.car_id,
        subject_type=subject_type,
        subject_id=subject_id,
        relationship_type=relationship,
        created=created,
    )
