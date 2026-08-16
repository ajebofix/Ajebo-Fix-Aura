"""Advisor-governed evidence review, linkage and canonical audit events.

Wave 1.4 starts linkage with Reported Concerns only. Review/link mutations and
their canonical VehicleEvents share one transaction so professional record
state cannot silently diverge from the care timeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from evidence.models import EvidenceLink, VehicleEvidence
from extensions import db
from models import CarFault
from security.access import resolve_vehicle_authority
from services.event_emission import EventEmissionError, emit_vehicle_event


logger = logging.getLogger(__name__)

_ACCEPT_REASONS = frozenset({"advisor_verified", "sufficient_for_record"})
_REJECT_REASONS = frozenset(
    {
        "insufficient_quality",
        "not_relevant",
        "wrong_vehicle",
        "duplicate",
        "privacy_restriction",
    }
)


class EvidenceReviewError(RuntimeError):
    """Base safe failure for evidence review/link workflows."""


class EvidenceReviewAccessError(EvidenceReviewError):
    """Raised when the caller does not hold advisor authority for the vehicle."""


class EvidenceReviewConflict(EvidenceReviewError):
    """Raised when the requested review/link transition is not permitted."""


class EvidenceReviewNotFound(EvidenceReviewError):
    """Raised when the evidence or care subject cannot be resolved safely."""


@dataclass(frozen=True)
class EvidenceReviewResult:
    evidence_id: int
    car_id: int
    review_status: str
    visibility: str
    reviewed_by_user_id: int
    reviewed_at: datetime
    review_reason_code: str


@dataclass(frozen=True)
class EvidenceConcernLinkResult:
    link_id: int
    evidence_id: int
    car_id: int
    concern_id: int
    relationship_type: str
    created: bool


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _advisor_evidence(*, reviewer_user_id: int, evidence_id: int) -> VehicleEvidence:
    evidence = db.session.get(VehicleEvidence, evidence_id)
    if evidence is None:
        raise EvidenceReviewNotFound("Evidence was not found.")

    authority = resolve_vehicle_authority(reviewer_user_id, evidence.car_id)
    if authority not in {"advisor", "administrator"}:
        raise EvidenceReviewAccessError(
            "Advisor authority is required for evidence review."
        )
    return evidence


def _emit_review_event(
    *,
    evidence: VehicleEvidence,
    reviewer_user_id: int,
) -> None:
    if (
        evidence.review_status not in {"accepted", "rejected"}
        or evidence.reviewed_at is None
        or not evidence.review_reason_code
    ):
        raise EvidenceReviewConflict("Evidence review metadata is incomplete.")

    emit_vehicle_event(
        car_id=evidence.car_id,
        event_type="evidence.reviewed",
        subject_type="vehicle_evidence",
        subject_id=evidence.id,
        actor_type="user",
        actor_user_id=reviewer_user_id,
        visibility=evidence.visibility,
        source="evidence.review",
        occurred_at=evidence.reviewed_at,
        title="Vehicle evidence reviewed",
        progression_direction="not_applicable",
        idempotency_key=(
            f"evidence-review:{evidence.id}:{evidence.review_status}"
        ),
        previous_state="pending_review",
        new_state=evidence.review_status,
        evidence_refs=[{"type": "vehicle_evidence", "id": evidence.id}],
        data={"review_reason_code": evidence.review_reason_code},
    )


def _emit_link_event(
    *,
    evidence: VehicleEvidence,
    concern: CarFault,
    link: EvidenceLink,
    reviewer_user_id: int,
) -> None:
    if link.id is None or link.created_at is None:
        raise EvidenceReviewConflict("Evidence link metadata is incomplete.")

    emit_vehicle_event(
        car_id=evidence.car_id,
        event_type="evidence.linked",
        subject_type="vehicle_evidence",
        subject_id=evidence.id,
        actor_type="user",
        actor_user_id=reviewer_user_id,
        visibility=evidence.visibility,
        source="evidence.link",
        occurred_at=link.created_at,
        title="Evidence linked to reported concern",
        progression_direction="not_applicable",
        idempotency_key=f"evidence-link:{link.id}",
        evidence_refs=[{"type": "vehicle_evidence", "id": evidence.id}],
        data={
            "link_id": link.id,
            "linked_subject_type": "reported_concern",
            "linked_subject_id": concern.id,
            "relationship_type": "supports",
        },
    )


def review_evidence(
    *,
    reviewer_user_id: int,
    evidence_id: int,
    decision: str,
    reason_code: str,
) -> EvidenceReviewResult:
    """Accept or reject evidence and atomically record its canonical event."""

    evidence = _advisor_evidence(
        reviewer_user_id=reviewer_user_id,
        evidence_id=evidence_id,
    )
    normalized_decision = (decision or "").strip().lower()
    normalized_reason = (reason_code or "").strip().lower()

    if normalized_decision not in {"accepted", "rejected"}:
        raise EvidenceReviewConflict("Review decision must be accepted or rejected.")

    allowed_reasons = (
        _ACCEPT_REASONS if normalized_decision == "accepted" else _REJECT_REASONS
    )
    if normalized_reason not in allowed_reasons:
        raise EvidenceReviewConflict("Select an approved evidence review reason.")

    if evidence.deleted_at is not None or evidence.review_status == "deleted":
        raise EvidenceReviewConflict("Deleted evidence cannot be reviewed.")
    if evidence.storage_state != "available":
        raise EvidenceReviewConflict(
            "Evidence must have an available verified private object before review."
        )

    if evidence.review_status == normalized_decision:
        if (
            evidence.reviewed_by_user_id is None
            or evidence.reviewed_at is None
            or not evidence.review_reason_code
        ):
            raise EvidenceReviewConflict("Existing review metadata is incomplete.")
        try:
            _emit_review_event(
                evidence=evidence,
                reviewer_user_id=evidence.reviewed_by_user_id,
            )
            db.session.commit()
        except (SQLAlchemyError, EventEmissionError) as exc:
            db.session.rollback()
            raise EvidenceReviewError(
                "Aura could not reconcile the evidence review event."
            ) from exc

        return EvidenceReviewResult(
            evidence_id=evidence.id,
            car_id=evidence.car_id,
            review_status=evidence.review_status,
            visibility=evidence.visibility,
            reviewed_by_user_id=evidence.reviewed_by_user_id,
            reviewed_at=evidence.reviewed_at,
            review_reason_code=evidence.review_reason_code,
        )

    if evidence.review_status != "pending_review":
        raise EvidenceReviewConflict(
            "A completed evidence review cannot be overwritten in place."
        )

    now = _utcnow_naive()
    evidence.review_status = normalized_decision
    evidence.reviewed_by_user_id = reviewer_user_id
    evidence.reviewed_at = now
    evidence.review_reason_code = normalized_reason
    evidence.updated_at = now

    try:
        _emit_review_event(
            evidence=evidence,
            reviewer_user_id=reviewer_user_id,
        )
        db.session.commit()
    except (SQLAlchemyError, EventEmissionError) as exc:
        db.session.rollback()
        raise EvidenceReviewError(
            "Aura could not persist the evidence review and event."
        ) from exc

    logger.info(
        "evidence_reviewed evidence_id=%s car_id=%s reviewer_id=%s decision=%s reason_code=%s visibility=%s",
        evidence.id,
        evidence.car_id,
        reviewer_user_id,
        normalized_decision,
        normalized_reason,
        evidence.visibility,
    )

    return EvidenceReviewResult(
        evidence_id=evidence.id,
        car_id=evidence.car_id,
        review_status=evidence.review_status,
        visibility=evidence.visibility,
        reviewed_by_user_id=reviewer_user_id,
        reviewed_at=now,
        review_reason_code=normalized_reason,
    )


def _return_existing_link(
    *,
    evidence: VehicleEvidence,
    concern: CarFault,
    existing: EvidenceLink,
) -> EvidenceConcernLinkResult:
    return EvidenceConcernLinkResult(
        link_id=existing.id,
        evidence_id=evidence.id,
        car_id=evidence.car_id,
        concern_id=concern.id,
        relationship_type="supports",
        created=False,
    )


def link_evidence_to_reported_concern(
    *,
    reviewer_user_id: int,
    evidence_id: int,
    concern_id: int,
) -> EvidenceConcernLinkResult:
    """Create a same-vehicle support link and its canonical event atomically."""

    evidence = _advisor_evidence(
        reviewer_user_id=reviewer_user_id,
        evidence_id=evidence_id,
    )
    if evidence.review_status != "accepted":
        raise EvidenceReviewConflict(
            "Only advisor-accepted evidence may be linked to a care record."
        )
    if evidence.storage_state != "available" or evidence.deleted_at is not None:
        raise EvidenceReviewConflict("Evidence is not available for care linkage.")

    concern = db.session.get(CarFault, concern_id)
    if concern is None:
        raise EvidenceReviewNotFound("Reported Concern was not found.")
    if concern.car_id != evidence.car_id:
        raise EvidenceReviewAccessError(
            "Evidence and Reported Concern must belong to the same vehicle."
        )

    existing = EvidenceLink.query.filter_by(
        evidence_id=evidence.id,
        car_id=evidence.car_id,
        subject_type="reported_concern",
        subject_id=concern.id,
        relationship_type="supports",
    ).first()
    if existing is not None:
        try:
            _emit_link_event(
                evidence=evidence,
                concern=concern,
                link=existing,
                reviewer_user_id=existing.created_by_user_id,
            )
            db.session.commit()
        except (SQLAlchemyError, EventEmissionError) as exc:
            db.session.rollback()
            raise EvidenceReviewError(
                "Aura could not reconcile the evidence link event."
            ) from exc
        return _return_existing_link(
            evidence=evidence,
            concern=concern,
            existing=existing,
        )

    link = EvidenceLink(
        evidence_id=evidence.id,
        car_id=evidence.car_id,
        subject_type="reported_concern",
        subject_id=concern.id,
        relationship_type="supports",
        created_by_user_id=reviewer_user_id,
    )
    db.session.add(link)

    try:
        db.session.flush()
        _emit_link_event(
            evidence=evidence,
            concern=concern,
            link=link,
            reviewer_user_id=reviewer_user_id,
        )
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing = EvidenceLink.query.filter_by(
            evidence_id=evidence.id,
            car_id=evidence.car_id,
            subject_type="reported_concern",
            subject_id=concern.id,
            relationship_type="supports",
        ).first()
        if existing is None:
            raise EvidenceReviewError("Aura could not create the evidence link.")
        try:
            _emit_link_event(
                evidence=evidence,
                concern=concern,
                link=existing,
                reviewer_user_id=existing.created_by_user_id,
            )
            db.session.commit()
        except (SQLAlchemyError, EventEmissionError) as exc:
            db.session.rollback()
            raise EvidenceReviewError(
                "Aura could not reconcile the evidence link event."
            ) from exc
        return _return_existing_link(
            evidence=evidence,
            concern=concern,
            existing=existing,
        )
    except (SQLAlchemyError, EventEmissionError) as exc:
        db.session.rollback()
        raise EvidenceReviewError(
            "Aura could not create the evidence link and event."
        ) from exc

    logger.info(
        "evidence_concern_link_created link_id=%s evidence_id=%s car_id=%s concern_id=%s reviewer_id=%s",
        link.id,
        evidence.id,
        evidence.car_id,
        concern.id,
        reviewer_user_id,
    )

    return EvidenceConcernLinkResult(
        link_id=link.id,
        evidence_id=evidence.id,
        car_id=evidence.car_id,
        concern_id=concern.id,
        relationship_type="supports",
        created=True,
    )
