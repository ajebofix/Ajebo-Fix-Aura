"""Authority-filtered timeline projection for reviewed Wave 1.4 evidence.

This projection is deliberately narrower than the canonical VehicleEvent ledger.
It exposes governance facts needed by owners/drivers/advisors without returning
raw event payloads, storage identifiers, checksums, media bytes, or diagnostic
claims. Reported Concern progression remains owned by concern_progression.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from evidence.models import EvidenceLink, VehicleEvidence
from models import Car, CarFault, VehicleEvent
from security.access import resolve_vehicle_authority


_CLIENT_AUTHORITIES = frozenset({"owner", "driver"})
_ADVISOR_AUTHORITIES = frozenset({"advisor", "administrator"})
_REVIEW_EVENT_TYPE = "evidence.reviewed"
_LINK_EVENT_TYPE = "evidence.linked"

_PURPOSE_LABELS = {
    "concern_support": "Reported concern support",
    "consultation_support": "Consultation support",
    "assessment_evidence": "Assessment evidence",
    "treatment_evidence": "Treatment evidence",
    "diagnostic_document": "Diagnostic document",
    "service_document": "Service document",
    "driver_observation": "Driver observation",
}

_CLIENT_REVIEW_COPY = {
    "accepted": "Reviewed and accepted into the vehicle care record.",
    "rejected": "Reviewed and not used as professional care evidence.",
}

_CLIENT_REJECTION_COPY = {
    "insufficient_quality": "The submitted evidence could not be used because the media quality was insufficient.",
    "not_relevant": "The submitted evidence was reviewed but was not relevant to this vehicle care record.",
    "wrong_vehicle": "The submitted evidence was reviewed but did not match the selected vehicle record.",
    "duplicate": "The submitted evidence was reviewed as a duplicate.",
    "privacy_restriction": "The submitted evidence was not used because of a privacy restriction.",
}


class EvidenceTimelineError(ValueError):
    """Base error for safe evidence timeline projection."""


class EvidenceTimelineNotFound(EvidenceTimelineError):
    """Raised when the requested vehicle does not exist."""


class EvidenceTimelineAccessError(EvidenceTimelineError):
    """Raised when the viewer has no supported authority for the vehicle."""


@dataclass(frozen=True)
class SafeConcernLink:
    concern_id: int
    title: str
    status: str
    link_event_id: int
    linked_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "concern_id": self.concern_id,
            "title": self.title,
            "status": self.status,
            "link_event_id": self.link_event_id,
            "linked_at": self.linked_at.isoformat(),
        }


@dataclass(frozen=True)
class SafeEvidenceTimelineItem:
    evidence_id: int
    evidence_type: str
    purpose: str
    purpose_label: str
    review_status: str
    review_summary: str
    uploaded_at: datetime
    reviewed_at: datetime
    review_event_id: int
    linked_concerns: tuple[SafeConcernLink, ...]
    uploaded_by_self: bool
    can_request_private_content: bool
    visibility: str | None = None
    review_reason_code: str | None = None
    uploaded_by_user_id: int | None = None

    def to_dict(self, *, advisor_view: bool) -> dict[str, object]:
        payload: dict[str, object] = {
            "evidence_id": self.evidence_id,
            "evidence_type": self.evidence_type,
            "purpose": self.purpose,
            "purpose_label": self.purpose_label,
            "review_status": self.review_status,
            "review_summary": self.review_summary,
            "uploaded_at": self.uploaded_at.isoformat(),
            "reviewed_at": self.reviewed_at.isoformat(),
            "review_event_id": self.review_event_id,
            "linked_concerns": [item.to_dict() for item in self.linked_concerns],
            "uploaded_by_self": self.uploaded_by_self,
            "can_request_private_content": self.can_request_private_content,
        }
        if advisor_view:
            payload.update(
                {
                    "visibility": self.visibility,
                    "review_reason_code": self.review_reason_code,
                    "uploaded_by_user_id": self.uploaded_by_user_id,
                }
            )
        return payload


@dataclass(frozen=True)
class EvidenceTimelineProjection:
    car_id: int
    viewer_authority: str
    records: tuple[SafeEvidenceTimelineItem, ...]
    generated_at: datetime
    advisor_view: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "car_id": self.car_id,
            "viewer_authority": self.viewer_authority,
            "record_count": len(self.records),
            "records": [
                item.to_dict(advisor_view=self.advisor_view) for item in self.records
            ],
            "safety_note": (
                "This timeline describes reviewed vehicle-care evidence and record linkage. "
                "It does not establish a mechanical diagnosis, failed component, repair instruction, "
                "or vehicle-health progression."
            ),
            "generated_at": self.generated_at.isoformat(),
        }


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _authority_for(*, viewer_user_id: int, car_id: int) -> str:
    if Car.query.filter_by(id=car_id).first() is None:
        raise EvidenceTimelineNotFound("Vehicle was not found.")
    authority = resolve_vehicle_authority(viewer_user_id, car_id)
    if authority is None:
        raise EvidenceTimelineAccessError(
            "Viewer has no proven authority for this vehicle."
        )
    if authority not in _CLIENT_AUTHORITIES | _ADVISOR_AUTHORITIES:
        raise EvidenceTimelineAccessError(
            "Viewer authority is not supported for evidence timeline access."
        )
    return authority


def _eligible_evidence_query(
    *,
    viewer_user_id: int,
    car_id: int,
    authority: str,
):
    query = VehicleEvidence.query.filter(
        VehicleEvidence.car_id == car_id,
        VehicleEvidence.review_status.in_(("accepted", "rejected")),
        VehicleEvidence.storage_state == "available",
        VehicleEvidence.deleted_at.is_(None),
    )

    if authority in _ADVISOR_AUTHORITIES:
        return query.filter(
            VehicleEvidence.visibility.in_(("client", "advisor", "internal"))
        )

    query = query.filter(VehicleEvidence.visibility == "client")
    if authority == "driver":
        query = query.filter(VehicleEvidence.uploaded_by_user_id == viewer_user_id)
    return query


def _canonical_events_for(
    *,
    car_id: int,
    evidence_ids: tuple[int, ...],
    authority: str,
) -> list[VehicleEvent]:
    if not evidence_ids:
        return []

    allowed_visibilities = (
        ("client", "advisor", "internal")
        if authority in _ADVISOR_AUTHORITIES
        else ("client",)
    )
    return (
        VehicleEvent.query.filter(
            VehicleEvent.car_id == car_id,
            VehicleEvent.subject_type == "vehicle_evidence",
            VehicleEvent.subject_id.in_(evidence_ids),
            VehicleEvent.event_type.in_((_REVIEW_EVENT_TYPE, _LINK_EVENT_TYPE)),
            VehicleEvent.visibility.in_(allowed_visibilities),
            VehicleEvent.is_deleted.is_(False),
        )
        .order_by(VehicleEvent.recorded_at.asc(), VehicleEvent.id.asc())
        .all()
    )


def _review_summary(evidence: VehicleEvidence, *, advisor_view: bool) -> str:
    base = _CLIENT_REVIEW_COPY.get(
        evidence.review_status,
        "Reviewed evidence record.",
    )
    if advisor_view or evidence.review_status != "rejected":
        return base
    return _CLIENT_REJECTION_COPY.get(evidence.review_reason_code or "", base)


def _safe_links(
    *,
    evidence: VehicleEvidence,
    link_events: list[VehicleEvent],
) -> tuple[SafeConcernLink, ...]:
    events_by_link_id: dict[int, VehicleEvent] = {}
    for event in link_events:
        data = event.data if isinstance(event.data, dict) else {}
        try:
            link_id = int(data.get("link_id"))
        except (TypeError, ValueError):
            continue
        events_by_link_id[link_id] = event

    if not events_by_link_id:
        return ()

    links = EvidenceLink.query.filter(
        EvidenceLink.id.in_(tuple(events_by_link_id)),
        EvidenceLink.evidence_id == evidence.id,
        EvidenceLink.car_id == evidence.car_id,
        EvidenceLink.subject_type == "reported_concern",
        EvidenceLink.relationship_type == "supports",
    ).all()

    safe: list[SafeConcernLink] = []
    for link in links:
        event = events_by_link_id.get(link.id)
        if event is None or link.subject_id <= 0:
            continue
        concern = CarFault.query.filter_by(
            id=link.subject_id,
            car_id=evidence.car_id,
        ).first()
        if concern is None:
            continue
        linked_at = event.occurred_at or event.recorded_at or event.created_at
        safe.append(
            SafeConcernLink(
                concern_id=concern.id,
                title=concern.title,
                status=concern.status,
                link_event_id=event.id,
                linked_at=linked_at,
            )
        )

    return tuple(sorted(safe, key=lambda item: (item.linked_at, item.link_event_id)))


def get_evidence_timeline(
    *,
    car_id: int,
    viewer_user_id: int,
) -> EvidenceTimelineProjection:
    """Return the authority-filtered evidence governance timeline for one vehicle."""

    authority = _authority_for(viewer_user_id=viewer_user_id, car_id=car_id)
    advisor_view = authority in _ADVISOR_AUTHORITIES
    evidence_rows = (
        _eligible_evidence_query(
            viewer_user_id=viewer_user_id,
            car_id=car_id,
            authority=authority,
        )
        .order_by(VehicleEvidence.reviewed_at.desc(), VehicleEvidence.id.desc())
        .all()
    )
    evidence_ids = tuple(row.id for row in evidence_rows)
    events = _canonical_events_for(
        car_id=car_id,
        evidence_ids=evidence_ids,
        authority=authority,
    )

    review_events: dict[int, VehicleEvent] = {}
    link_events: dict[int, list[VehicleEvent]] = {}
    for event in events:
        if event.event_type == _REVIEW_EVENT_TYPE:
            review_events[event.subject_id] = event
        elif event.event_type == _LINK_EVENT_TYPE:
            link_events.setdefault(event.subject_id, []).append(event)

    records: list[SafeEvidenceTimelineItem] = []
    for evidence in evidence_rows:
        review_event = review_events.get(evidence.id)
        if review_event is None or evidence.reviewed_at is None:
            # A reviewed row without its canonical event is an incomplete ledger
            # state. Do not project it as trusted timeline history.
            continue
        if review_event.new_state != evidence.review_status:
            continue

        records.append(
            SafeEvidenceTimelineItem(
                evidence_id=evidence.id,
                evidence_type=evidence.evidence_type,
                purpose=evidence.purpose,
                purpose_label=_PURPOSE_LABELS.get(
                    evidence.purpose,
                    "Vehicle care evidence",
                ),
                review_status=evidence.review_status,
                review_summary=_review_summary(evidence, advisor_view=advisor_view),
                uploaded_at=evidence.uploaded_at,
                reviewed_at=evidence.reviewed_at,
                review_event_id=review_event.id,
                linked_concerns=_safe_links(
                    evidence=evidence,
                    link_events=link_events.get(evidence.id, []),
                ),
                uploaded_by_self=evidence.uploaded_by_user_id == viewer_user_id,
                can_request_private_content=True,
                visibility=evidence.visibility if advisor_view else None,
                review_reason_code=(
                    evidence.review_reason_code if advisor_view else None
                ),
                uploaded_by_user_id=(
                    evidence.uploaded_by_user_id if advisor_view else None
                ),
            )
        )

    return EvidenceTimelineProjection(
        car_id=car_id,
        viewer_authority=authority,
        records=tuple(records),
        generated_at=_utcnow_naive(),
        advisor_view=advisor_view,
    )


def get_client_safe_evidence_timeline(
    *,
    car_id: int,
    viewer_user_id: int,
) -> EvidenceTimelineProjection:
    projection = get_evidence_timeline(
        car_id=car_id,
        viewer_user_id=viewer_user_id,
    )
    if projection.viewer_authority not in _CLIENT_AUTHORITIES:
        raise EvidenceTimelineAccessError(
            "Client-safe evidence timeline requires owner or driver authority."
        )
    return projection


def get_advisor_evidence_timeline(
    *,
    car_id: int,
    viewer_user_id: int,
) -> EvidenceTimelineProjection:
    projection = get_evidence_timeline(
        car_id=car_id,
        viewer_user_id=viewer_user_id,
    )
    if projection.viewer_authority not in _ADVISOR_AUTHORITIES:
        raise EvidenceTimelineAccessError(
            "Advisor evidence timeline requires advisor authority."
        )
    return projection
