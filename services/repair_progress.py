"""Low-risk live Repair Journey capture for the Aura pilot.

The pilot reuses the existing advisor_notes table rather than introducing a new
schema under time pressure. Repair progress rows are namespaced structured
advisor notes so they remain vehicle-scoped, attributable, durable and easy to
migrate into a first-class entity later.

This module never infers diagnosis, completion or treatment outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re

from extensions import db
from models import AdvisorNote, Car, CarOwnership
from security.access import resolve_vehicle_authority


PREFIX = "[AURA_REPAIR_PROGRESS_V1]"

MILESTONES = (
    "custody",
    "inspection",
    "dismantling",
    "parts",
    "bodywork",
    "paint",
    "reassembly",
    "testing",
    "ready_for_delivery",
    "delivered",
    "general",
)


class RepairProgressError(ValueError):
    pass


@dataclass(frozen=True)
class RepairProgressEntry:
    note_id: int
    car_id: int
    advisor_id: int
    milestone: str
    tags: tuple[str, ...]
    summary: str
    source: str
    occurred_at: str | None
    recorded_at: datetime | None


def _require_advisor(*, actor_user_id: int, car_id: int) -> str:
    authority = resolve_vehicle_authority(actor_user_id, car_id)
    if authority not in {"advisor", "administrator"}:
        raise RepairProgressError(
            "Repair progress recording requires advisor access to this vehicle."
        )
    return authority


def _owner_or_actor(*, car_id: int, actor_user_id: int) -> int:
    ownership = (
        CarOwnership.query.filter_by(car_id=car_id, is_active=True)
        .order_by(CarOwnership.id.desc())
        .first()
    )
    return int(ownership.user_id) if ownership is not None else int(actor_user_id)


def _normalise_summary(value: str) -> str:
    summary = " ".join(str(value or "").strip().split())
    if not summary:
        raise RepairProgressError("Repair progress summary is required.")
    if len(summary) > 3000:
        raise RepairProgressError("Repair progress summary is limited to 3000 characters.")
    return summary


def classify_repair_progress(summary: str) -> tuple[str, tuple[str, ...]]:
    """Conservative deterministic tagging; never infers repair completion."""

    text = str(summary or "").lower()
    rules = (
        ("delivered", ("delivered", "handed over", "returned to client", "client collected")),
        ("ready_for_delivery", ("ready for delivery", "ready for handover", "ready for pickup")),
        (
            "custody",
            (
                "police custody",
                "released from police",
                "released by police",
                "received into ajebo fix",
                "came back into ajebo fix",
                "took possession",
                "vehicle arrived",
                "car arrived",
            ),
        ),
        ("testing", ("road test", "testing", "tested", "quality check", "final inspection")),
        ("reassembly", ("reassembly", "reassembled", "assembly", "fitted back")),
        ("paint", ("paint", "painting", "spray", "refinish")),
        (
            "bodywork",
            (
                "panel beating started",
                "panel beating commenced",
                "panel work started",
                "bodywork started",
                "body work started",
                "straightening started",
                "straightening",
            ),
        ),
        ("dismantling", ("dismantl", "strip down", "strip-down", "stripped")),
        ("parts", ("parts", "part ", "headlamp", "bumper", "bonnet", "condenser", "radiator", "procure", "source")),
        ("inspection", ("inspect", "assessment", "check damage", "pre-repair")),
    )
    tags = tuple(name for name, needles in rules if any(needle in text for needle in needles))
    primary = tags[0] if tags else "general"
    return primary, tags


def _normalise_milestone(value: str | None, *, summary: str) -> tuple[str, tuple[str, ...]]:
    requested = str(value or "").strip().lower().replace(" ", "_")
    inferred, tags = classify_repair_progress(summary)
    if requested and requested not in MILESTONES:
        raise RepairProgressError("Select a supported repair-progress milestone.")
    milestone = requested or inferred
    if milestone not in tags and milestone != "general":
        tags = (milestone, *tags)
    return milestone, tuple(dict.fromkeys(tags))


_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def _explicit_date_from_summary(summary: str) -> str | None:
    """Return an explicit calendar date without inventing a clock time."""

    match = re.search(
        r"\b(?:on\s+)?(?P<day>[0-3]?\d)\s+"
        r"(?P<month>january|february|march|april|may|june|july|august|"
        r"september|october|november|december)\s+"
        r"(?P<year>20\d{2})\b",
        str(summary or ""),
        re.IGNORECASE,
    )
    if match is None:
        return None

    try:
        dt = datetime(
            int(match.group("year")),
            _MONTHS[match.group("month").lower()],
            int(match.group("day")),
        )
    except (TypeError, ValueError):
        return None
    return dt.date().isoformat()


def _normalise_occurred_at(value: str | datetime | None) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            try:
                return datetime.fromisoformat(raw).date().isoformat()
            except ValueError as exc:
                raise RepairProgressError("Enter a valid progress date/time.") from exc
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise RepairProgressError("Enter a valid progress date/time.") from exc
    if dt.tzinfo is None:
        return dt.isoformat(timespec="minutes")
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def record_repair_progress(
    *,
    actor_user_id: int,
    car_id: int,
    summary: str,
    milestone: str | None = None,
    occurred_at: str | datetime | None = None,
    source: str = "manual",
    commit: bool = True,
) -> RepairProgressEntry:
    car = db.session.get(Car, int(car_id))
    if car is None:
        raise RepairProgressError("Vehicle was not found.")
    _require_advisor(actor_user_id=actor_user_id, car_id=car.id)

    normalized_summary = _normalise_summary(summary)
    normalized_milestone, tags = _normalise_milestone(
        milestone,
        summary=normalized_summary,
    )
    normalized_source = str(source or "manual").strip().lower()
    if normalized_source not in {"manual", "rina"}:
        normalized_source = "manual"
    if occurred_at in (None, "") and normalized_source == "rina":
        occurred_at = _explicit_date_from_summary(normalized_summary)
    normalized_occurred_at = _normalise_occurred_at(occurred_at)

    payload = {
        "schema_version": 1,
        "milestone": normalized_milestone,
        "tags": list(tags),
        "summary": normalized_summary,
        "source": normalized_source,
        "occurred_at": normalized_occurred_at,
    }
    note = AdvisorNote(
        user_id=_owner_or_actor(car_id=car.id, actor_user_id=actor_user_id),
        car_id=car.id,
        advisor_id=actor_user_id,
        note=PREFIX + json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )
    db.session.add(note)
    db.session.flush()
    if commit:
        db.session.commit()

    return RepairProgressEntry(
        note_id=note.id,
        car_id=car.id,
        advisor_id=actor_user_id,
        milestone=normalized_milestone,
        tags=tags,
        summary=normalized_summary,
        source=normalized_source,
        occurred_at=normalized_occurred_at,
        recorded_at=note.created_at,
    )


def _parse(note: AdvisorNote) -> RepairProgressEntry | None:
    raw = str(note.note or "")
    if not raw.startswith(PREFIX):
        return None
    try:
        payload = json.loads(raw[len(PREFIX):])
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    summary = str(payload.get("summary") or "").strip()
    milestone = str(payload.get("milestone") or "general").strip().lower()
    if not summary or milestone not in MILESTONES:
        return None
    tags = payload.get("tags")
    if not isinstance(tags, list):
        tags = []
    clean_tags = tuple(
        str(item).strip().lower()
        for item in tags
        if str(item).strip().lower() in MILESTONES
    )
    return RepairProgressEntry(
        note_id=note.id,
        car_id=int(note.car_id),
        advisor_id=int(note.advisor_id),
        milestone=milestone,
        tags=clean_tags,
        summary=summary,
        source=str(payload.get("source") or "manual"),
        occurred_at=(str(payload.get("occurred_at")) if payload.get("occurred_at") else None),
        recorded_at=note.created_at,
    )


def repair_progress_for_car(
    *,
    car_id: int,
    limit: int = 100,
) -> list[RepairProgressEntry]:
    bounded_limit = min(max(int(limit), 1), 200)
    rows = (
        AdvisorNote.query.filter(
            AdvisorNote.car_id == int(car_id),
            AdvisorNote.note.like(f"{PREFIX}%"),
        )
        .order_by(AdvisorNote.created_at.desc(), AdvisorNote.id.desc())
        .limit(bounded_limit)
        .all()
    )
    result = []
    for row in rows:
        parsed = _parse(row)
        if parsed is not None:
            result.append(parsed)
    return result


def repair_progress_context(*, car_id: int, limit: int = 20) -> list[dict]:
    rows = repair_progress_for_car(car_id=car_id, limit=limit)
    return [
        {
            "record_id": item.note_id,
            "milestone": item.milestone,
            "tags": list(item.tags),
            "summary": item.summary,
            "source": item.source,
            "occurred_at": item.occurred_at,
            "recorded_at": item.recorded_at.isoformat() if item.recorded_at else None,
            "authority": "advisor_recorded_operational_progress",
            "does_not_imply": [
                "diagnosis",
                "repair_completion",
                "successful_outcome",
                "payment",
            ],
        }
        for item in rows
    ]
