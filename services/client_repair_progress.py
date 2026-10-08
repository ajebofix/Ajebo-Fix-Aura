"""Explicitly approved owner-visible repair journey updates.

The source repair timeline lives in private, namespaced AdvisorNote records.
Client publication is a *separate* immutable advisor-authored note, never an
automatic rendering of raw repair progress or classifier-generated tags.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json

from extensions import db
from models import AdvisorNote, CarOwnership
from security.access import resolve_vehicle_authority
from services.repair_progress import PREFIX as PRIVATE_PREFIX, _parse

PUBLICATION_PREFIX = "[AURA_CLIENT_REPAIR_PROGRESS_V1]"
MAX_CLIENT_SUMMARY = 700


class ClientProgressPublicationError(ValueError):
    pass


@dataclass(frozen=True)
class ClientProgressUpdate:
    source_note_id: int
    milestone: str
    summary: str
    recorded_at: datetime | None
    approved_at: datetime | None


def _ensure_advisor(actor_user_id: int, car_id: int) -> None:
    if resolve_vehicle_authority(actor_user_id, car_id) not in {
        "advisor", "administrator",
    }:
        raise ClientProgressPublicationError("Advisor authority required.")


def _active_owner_user_id(car_id: int) -> int:
    owners = CarOwnership.query.filter_by(car_id=car_id, is_active=True).all()
    if len(owners) != 1:
        raise ClientProgressPublicationError("Exactly one active owner is required.")
    return int(owners[0].user_id)


def _source_note(car_id: int, note_id: int) -> AdvisorNote:
    note = db.session.get(AdvisorNote, note_id)
    if note is None or note.car_id != car_id or not str(note.note).startswith(PRIVATE_PREFIX):
        raise ClientProgressPublicationError("Repair progress source was not found.")
    if _parse(note) is None:
        raise ClientProgressPublicationError("Repair progress source is invalid.")
    return note


def publish_client_progress(
    *, car_id: int, note_id: int, actor_user_id: int, client_summary: str
) -> None:
    _ensure_advisor(actor_user_id, car_id)
    source = _source_note(car_id, note_id)
    summary = " ".join(str(client_summary or "").split())
    if not (10 <= len(summary) <= MAX_CLIENT_SUMMARY):
        raise ClientProgressPublicationError("Provide a reviewed client summary of 10–700 characters.")
    parsed = _parse(source)
    if parsed is None:
        raise ClientProgressPublicationError("Invalid source note")
    owner_id = _active_owner_user_id(car_id)
    payload = {
        "v": 1,
        "source_note_id": source.id,
        "source_sha256": hashlib.sha256(source.note.encode("utf-8")).hexdigest(),
        "milestone": parsed.milestone,
        "client_summary": summary,
        "published": True,
    }
    db.session.add(AdvisorNote(
        user_id=owner_id,
        car_id=car_id,
        advisor_id=actor_user_id,
        note=PUBLICATION_PREFIX + json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
    ))
    db.session.commit()


def revoke_client_progress(*, car_id: int, note_id: int, actor_user_id: int) -> None:
    _ensure_advisor(actor_user_id, car_id)
    _source_note(car_id, note_id)
    owner_id = _active_owner_user_id(car_id)
    payload = {"v": 1, "source_note_id": note_id, "published": False}
    db.session.add(AdvisorNote(
        user_id=owner_id,
        car_id=car_id,
        advisor_id=actor_user_id,
        note=PUBLICATION_PREFIX + json.dumps(payload, separators=(",", ":")),
    ))
    db.session.commit()


def client_published_progress(*, car_id: int, limit: int = 40) -> list[ClientProgressUpdate]:
    # Later records for the same source (including revocation) override prior publication.
    records = (
        AdvisorNote.query.filter(
            AdvisorNote.car_id == car_id,
            AdvisorNote.note.like(f"{PUBLICATION_PREFIX}%"),
        )
        .order_by(AdvisorNote.created_at.desc(), AdvisorNote.id.desc())
        .limit(200)
        .all()
    )
    seen: set[int] = set()
    result: list[ClientProgressUpdate] = []
    for publication in records:
        try:
            payload = json.loads(publication.note[len(PUBLICATION_PREFIX):])
            note_id = int(payload["source_note_id"])
            if note_id <= 0 or note_id in seen:
                continue
            if not isinstance(payload.get("published"), bool):
                continue
            seen.add(note_id)
            if payload["published"] is False:
                continue
            source = _source_note(car_id, note_id)
            if payload.get("source_sha256") != hashlib.sha256(
                source.note.encode("utf-8")
            ).hexdigest():
                continue
            summary = str(payload.get("client_summary") or "").strip()
            parsed = _parse(source)
            if parsed is None or not (10 <= len(summary) <= MAX_CLIENT_SUMMARY):
                continue
            # Do not expose raw private summary, classifier tags or source type.
            result.append(ClientProgressUpdate(
                source_note_id=note_id,
                milestone=parsed.milestone,
                summary=summary,
                recorded_at=source.created_at,
                approved_at=publication.created_at,
            ))
        except (ValueError, TypeError, KeyError, AttributeError, ClientProgressPublicationError):
            continue
    return result[: min(max(int(limit), 1), 100)]
