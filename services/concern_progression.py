"""Concern-only timeline reconstruction and progression summaries for Wave 1.2.

Domain state remains authoritative in ``CarFault``. This service reconstructs
how that state was reached from canonical ``VehicleEvent`` evidence and emits a
small, explainable summary for advisors or client-safe consumers.

The rules are deliberately conservative:
- no diagnosis or repair recommendation;
- no text-sentiment inference;
- no improvement/deterioration inference without canonical evidence;
- reopenings are not called recurring unless an explicit recurrence event links
  to prior resolution evidence;
- hidden advisor/internal events never leak into a client-visible timeline.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import func

from models import CarFault, VehicleEvent
from security.access import resolve_vehicle_authority


SUBJECT_TYPE_REPORTED_CONCERN = "reported_concern"

_CLIENT_AUTHORITIES = frozenset({"owner", "driver"})
_ADVISOR_AUTHORITIES = frozenset({"advisor"})

_CLIENT_VISIBILITIES = frozenset({"client"})
_ADVISOR_VISIBILITIES = frozenset({"client", "advisor", "internal"})

_PROGRESSION_EVENT_TYPES = frozenset(
    {
        "concern.reported",
        "concern.review_started",
        "concern.monitoring_started",
        "concern.resolved",
        "concern.reopened",
        "concern.corrected",
    }
)


class ConcernProgressionError(ValueError):
    """Base error for concern timeline/progression reconstruction."""


class ConcernProgressionNotFound(ConcernProgressionError):
    """Raised when the requested concern does not belong to the vehicle."""


class ConcernProgressionAccessError(ConcernProgressionError):
    """Raised when the viewer has no proven vehicle authority."""


@dataclass(frozen=True)
class ConcernTimelineEvent:
    event_id: int
    event_type: str
    occurred_at: datetime | None
    recorded_at: datetime | None
    previous_state: str | None
    new_state: str | None
    progression_direction: str | None
    actor_authority: str | None
    visibility: str
    source: str | None
    evidence_refs: tuple[dict[str, Any], ...]
    correction_of_event_id: int | None
    corrected_by_event_id: int | None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["occurred_at"] = (
            self.occurred_at.isoformat() if self.occurred_at else None
        )
        payload["recorded_at"] = (
            self.recorded_at.isoformat() if self.recorded_at else None
        )
        payload["evidence_refs"] = [dict(item) for item in self.evidence_refs]
        return payload


@dataclass(frozen=True)
class ConcernProgressionSummary:
    car_id: int
    concern_id: int
    viewer_authority: str
    current_state: str
    timeline_state: str | None
    progression: str
    recurrence: bool | None
    evidence_event_ids: tuple[int, ...]
    timeline: tuple[ConcernTimelineEvent, ...]
    explanation: str
    safety_note: str
    generated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "car_id": self.car_id,
            "concern_id": self.concern_id,
            "viewer_authority": self.viewer_authority,
            "current_state": self.current_state,
            "timeline_state": self.timeline_state,
            "progression": self.progression,
            "recurrence": self.recurrence,
            "evidence_event_ids": list(self.evidence_event_ids),
            "timeline": [event.to_dict() for event in self.timeline],
            "explanation": self.explanation,
            "safety_note": self.safety_note,
            "generated_at": self.generated_at.isoformat(),
        }


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _allowed_visibilities(authority: str) -> frozenset[str]:
    if authority in _ADVISOR_AUTHORITIES:
        return _ADVISOR_VISIBILITIES
    if authority in _CLIENT_AUTHORITIES:
        return _CLIENT_VISIBILITIES
    raise ConcernProgressionAccessError(
        "viewer has no supported authority for this concern timeline"
    )


def _load_concern(*, car_id: int, concern_id: int) -> CarFault:
    concern = CarFault.query.filter_by(id=concern_id, car_id=car_id).first()
    if concern is None:
        raise ConcernProgressionNotFound(
            "reported concern does not exist for the requested vehicle"
        )
    return concern


def _query_visible_events(
    *,
    car_id: int,
    concern_id: int,
    allowed_visibilities: Iterable[str],
) -> list[VehicleEvent]:
    occurred_order = func.coalesce(VehicleEvent.occurred_at, VehicleEvent.created_at)
    recorded_order = func.coalesce(VehicleEvent.recorded_at, VehicleEvent.created_at)

    return (
        VehicleEvent.query.filter(
            VehicleEvent.car_id == car_id,
            VehicleEvent.subject_type == SUBJECT_TYPE_REPORTED_CONCERN,
            VehicleEvent.subject_id == concern_id,
            VehicleEvent.event_type.in_(_PROGRESSION_EVENT_TYPES),
            VehicleEvent.visibility.in_(tuple(allowed_visibilities)),
            VehicleEvent.is_deleted.is_(False),
        )
        .order_by(
            # The ledger is append-oriented: recorded chronology preserves the
            # causal state-transition sequence even when an occurrence timestamp
            # is later corrected or supplied out of order. occurred_at remains
            # exposed as the factual occurrence time, not silently rewritten.
            recorded_order.asc(),
            occurred_order.asc(),
            VehicleEvent.id.asc(),
        )
        .all()
    )


def _normalise_evidence_refs(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        return ()

    clean: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            clean.append(dict(item))
    return tuple(clean)


def _timeline_rows(events: list[VehicleEvent]) -> tuple[ConcernTimelineEvent, ...]:
    corrected_by: dict[int, int] = {}
    for event in events:
        if (
            event.event_type == "concern.corrected"
            and event.correction_of_event_id is not None
        ):
            corrected_by[event.correction_of_event_id] = event.id

    return tuple(
        ConcernTimelineEvent(
            event_id=event.id,
            event_type=event.event_type,
            occurred_at=event.occurred_at or event.created_at,
            recorded_at=event.recorded_at or event.created_at,
            previous_state=event.previous_state,
            new_state=event.new_state,
            progression_direction=event.progression_direction,
            actor_authority=event.actor_authority,
            visibility=event.visibility or "internal",
            source=event.source,
            evidence_refs=_normalise_evidence_refs(event.evidence_refs),
            correction_of_event_id=event.correction_of_event_id,
            corrected_by_event_id=corrected_by.get(event.id),
        )
        for event in events
    )


def _effective_progression_events(
    timeline: tuple[ConcernTimelineEvent, ...],
) -> tuple[ConcernTimelineEvent, ...]:
    return tuple(
        item
        for item in timeline
        if item.event_type != "concern.corrected"
        and item.corrected_by_event_id is None
    )


def _has_prior_resolution_evidence(
    reopened: ConcernTimelineEvent,
    *,
    timeline: tuple[ConcernTimelineEvent, ...],
) -> bool:
    prior_resolved_ids = {
        item.event_id
        for item in timeline
        if item.event_type == "concern.resolved"
        and item.corrected_by_event_id is None
        and item.event_id < reopened.event_id
    }
    if not prior_resolved_ids:
        return False

    for ref in reopened.evidence_refs:
        if ref.get("type") != "vehicle_event":
            continue
        try:
            referenced_id = int(ref.get("id"))
        except (TypeError, ValueError):
            continue
        if referenced_id in prior_resolved_ids:
            return True

    return False


def _derive_progression(
    *,
    current_state: str,
    timeline: tuple[ConcernTimelineEvent, ...],
) -> tuple[str, bool | None, tuple[int, ...], str, str | None]:
    effective = _effective_progression_events(timeline)
    timeline_state = effective[-1].new_state if effective else None

    if not effective:
        return (
            "insufficient_evidence",
            None,
            (),
            "There is not enough canonical event evidence to classify this concern's progression.",
            timeline_state,
        )

    latest = effective[-1]

    # Domain rows remain authoritative current state. Any disagreement means the
    # canonical history is incomplete or a correction removed decisive evidence.
    if timeline_state != current_state:
        evidence_ids = tuple(item.event_id for item in effective[-2:])
        return (
            "insufficient_evidence",
            None,
            evidence_ids,
            "The recorded event history does not fully reconcile with the concern's current state, so Aura is abstaining from a progression claim.",
            timeline_state,
        )

    if latest.event_type == "concern.resolved" and latest.progression_direction == "resolved":
        return (
            "resolved",
            False,
            (latest.event_id,),
            "The latest durable progression event records this concern as resolved.",
            timeline_state,
        )

    if latest.event_type == "concern.monitoring_started" and latest.progression_direction == "stable":
        return (
            "stable",
            False,
            (latest.event_id,),
            "The latest durable progression event records this concern as being monitored with no material change asserted.",
            timeline_state,
        )

    if latest.event_type == "concern.reopened":
        if (
            latest.progression_direction == "recurring"
            and _has_prior_resolution_evidence(latest, timeline=timeline)
        ):
            prior_ids = tuple(
                int(ref["id"])
                for ref in latest.evidence_refs
                if ref.get("type") == "vehicle_event"
                and str(ref.get("id", "")).isdigit()
            )
            return (
                "recurring",
                True,
                tuple(dict.fromkeys((*prior_ids, latest.event_id))),
                "This reopening has an explicit canonical link to prior resolved evidence, so Aura can classify it as recurring.",
                timeline_state,
            )

        return (
            "insufficient_evidence",
            None,
            (latest.event_id,),
            "The concern was reopened, but the durable evidence does not establish recurrence, so Aura is not making that claim.",
            timeline_state,
        )

    if latest.event_type in {"concern.reported", "concern.review_started"}:
        return (
            "insufficient_evidence",
            None,
            (latest.event_id,),
            "The concern is recorded, but there is not enough canonical progression evidence to classify improvement, deterioration, recurrence, or resolution.",
            timeline_state,
        )

    return (
        "insufficient_evidence",
        None,
        (latest.event_id,),
        "The available canonical evidence does not support a stronger progression statement.",
        timeline_state,
    )


def get_reported_concern_progression(
    *,
    car_id: int,
    concern_id: int,
    viewer_user_id: int,
) -> ConcernProgressionSummary:
    """Return an authority-filtered, evidence-backed progression summary."""

    concern = _load_concern(car_id=car_id, concern_id=concern_id)

    authority = resolve_vehicle_authority(viewer_user_id, car_id)
    if authority is None:
        raise ConcernProgressionAccessError(
            "viewer has no proven authority for this vehicle"
        )

    allowed_visibilities = _allowed_visibilities(authority)
    events = _query_visible_events(
        car_id=car_id,
        concern_id=concern_id,
        allowed_visibilities=allowed_visibilities,
    )
    timeline = _timeline_rows(events)

    progression, recurrence, evidence_ids, explanation, timeline_state = (
        _derive_progression(
            current_state=concern.status,
            timeline=timeline,
        )
    )

    return ConcernProgressionSummary(
        car_id=car_id,
        concern_id=concern_id,
        viewer_authority=authority,
        current_state=concern.status,
        timeline_state=timeline_state,
        progression=progression,
        recurrence=recurrence,
        evidence_event_ids=evidence_ids,
        timeline=timeline,
        explanation=explanation,
        safety_note=(
            "This summary describes recorded concern progression and evidence; it is not a mechanical diagnosis or repair recommendation."
        ),
        generated_at=_utcnow_naive(),
    )


def get_client_safe_reported_concern_progression(
    *,
    car_id: int,
    concern_id: int,
    viewer_user_id: int,
) -> ConcernProgressionSummary:
    """Return a client-safe concern summary for an owner or assigned driver."""

    summary = get_reported_concern_progression(
        car_id=car_id,
        concern_id=concern_id,
        viewer_user_id=viewer_user_id,
    )
    if summary.viewer_authority not in _CLIENT_AUTHORITIES:
        raise ConcernProgressionAccessError(
            "client-safe summary requires owner or driver authority"
        )
    return summary
