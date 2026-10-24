"""Advisor-controlled corrections to historical-review context, never source evidence.

A source extraction is immutable. Edits affect only the pending encrypted advisor
reconciliation draft, with an append-only correction trail inside that draft.
The same primitive powers the manual form and deterministic Rina commands.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from hashlib import sha256
import json

from extensions import db
from historical_ingestion.reconciliation import (
    HistoricalReconciliationError,
    applied_reconciliation_plan,
    save_reconciliation_review,
)
from security.access import resolve_vehicle_authority

ALLOWED_CONTEXT_CATEGORIES = frozenset({
    "reported_concern",
    "assessment_finding",
    "external_event",
    "client_advisor_context",
    "procurement",
    "recommendation",
    "clarification",
    "not_performed",
    "other",
})
MAX_CONTEXT_NOTES = 40
MAX_CONTEXT_NOTE_CHARS = 1200


class HistoricalContextEditError(HistoricalReconciliationError):
    """The requested draft-only change cannot be performed safely."""


def is_active_context_note(row: dict) -> bool:
    """Older saved drafts without a status field are active by default."""
    return row.get("context_status", "active") != "superseded"


def _read_draft(*, extraction_id: int, car_id: int, actor_user_id: int):
    # The Rina review state loader verifies source/episode vehicle provenance.
    # Its read-time vehicle authority is supplemented here with explicit RBAC.
    from services.rina_historical_review import _load_state

    if resolve_vehicle_authority(actor_user_id, car_id) not in {
        "administrator", "advisor"
    }:
        raise HistoricalContextEditError("Advisor access to this vehicle is required.")
    extraction, _episode, payload = _load_state(
        extraction_id=extraction_id,
        actor_user_id=actor_user_id,
        car_id=car_id,
    )
    if applied_reconciliation_plan(extraction.id) is not None:
        raise HistoricalContextEditError(
            "This reconciliation was already applied. Record a separate reviewed "
            "addendum instead of rewriting completed vehicle history."
        )
    return extraction, payload


def _draft_digest(payload: dict) -> str:
    """Optimistic revision, including candidate changes made in another tab."""
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(serialized.encode("utf-8")).hexdigest()


def _note_id(extraction_id: int, ordinal: int, row: dict) -> str:
    existing = str(row.get("context_note_id") or "").strip()
    if existing:
        return existing
    # Stable for an unchanged legacy draft between GET and POST. IDs are
    # persisted on the first edit so subsequent removals do not renumber them.
    material = json.dumps([
        extraction_id, ordinal, row.get("category"), row.get("occurred_at"),
        row.get("note"),
    ], ensure_ascii=False, separators=(",", ":"), default=str)
    return "ctx_" + sha256(material.encode("utf-8")).hexdigest()[:16]


def context_editor_snapshot(
    *, extraction_id: int, car_id: int, actor_user_id: int
) -> dict:
    extraction, payload = _read_draft(
        extraction_id=extraction_id, car_id=car_id, actor_user_id=actor_user_id
    )
    notes = []
    for ordinal, row in enumerate(payload.get("advisor_context_notes") or [], start=1):
        if not isinstance(row, dict) or not is_active_context_note(row):
            continue
        item = deepcopy(row)
        item["context_note_id"] = _note_id(extraction.id, ordinal, row)
        item["display_number"] = len(notes) + 1
        notes.append(item)
    return {
        "extraction_id": extraction.id,
        "car_id": car_id,
        "revision": _draft_digest(payload),
        "notes": notes,
        "correction_count": len(payload.get("context_correction_audit") or []),
    }


def context_command_already_applied(
    *, extraction_id: int, car_id: int, actor_user_id: int, operation_key: str
) -> bool:
    """Guard numbered Rina commands against accidental replay after renumbering."""
    _extraction, payload = _read_draft(
        extraction_id=extraction_id, car_id=car_id, actor_user_id=actor_user_id
    )
    return any(
        isinstance(row, dict)
        and row.get("operation_key") == operation_key
        and row.get("actor_user_id") == actor_user_id
        for row in payload.get("context_correction_audit") or []
    )


def revise_context_note(
    *,
    extraction_id: int,
    car_id: int,
    actor_user_id: int,
    note_id: str | None = None,
    position: int | None = None,
    operation: str,
    reason: str,
    revision: str | None = None,
    note: str | None = None,
    category: str | None = None,
    occurred_at: str | None = None,
    source: str = "advisor_manual",
    operation_key: str | None = None,
) -> dict:
    """Stage an explicit correction or supersession; caller commits/rolls back.

    Exactly one note_id or position selects the target. No completed Treatment
    Action, source document, assessment or reported concern is altered.
    """
    if source not in {"advisor_manual", "rina_explicit_command"}:
        raise HistoricalContextEditError("Unsupported historical correction source.")
    if operation not in {"correct", "supersede"}:
        raise HistoricalContextEditError("Choose correction or supersession.")
    if bool(note_id) == bool(position is not None):
        raise HistoricalContextEditError("Select one historical context note.")
    reason = (reason or "").strip()
    if not 3 <= len(reason) <= 1000:
        raise HistoricalContextEditError("Explain this correction in 3–1000 characters.")

    extraction, payload = _read_draft(
        extraction_id=extraction_id, car_id=car_id, actor_user_id=actor_user_id
    )
    if operation_key and any(
        isinstance(entry, dict)
        and entry.get("operation_key") == operation_key
        and entry.get("actor_user_id") == actor_user_id
        for entry in payload.get("context_correction_audit") or []
    ):
        return {"operation": operation, "already_applied": True}
    if revision is not None and revision != _draft_digest(payload):
        raise HistoricalContextEditError(
            "The draft changed in another review. Reopen it before saving this edit."
        )

    rows = [
        deepcopy(row) if isinstance(row, dict) else row
        for row in payload.get("advisor_context_notes") or []
    ]
    active = [
        (index, row)
        for index, row in enumerate(rows, start=1)
        if isinstance(row, dict) and is_active_context_note(row)
    ]
    if position is not None:
        if position < 1 or position > len(active):
            raise HistoricalContextEditError("That context note number is unavailable.")
        selected_index, selected_row = active[position - 1]
    else:
        matching = [
            (index, row)
            for index, row in active
            if _note_id(extraction.id, index, row) == note_id
        ]
        if len(matching) != 1:
            raise HistoricalContextEditError(
                "That note is no longer in this draft. Reopen the context editor."
            )
        selected_index, selected_row = matching[0]
    key = _note_id(extraction.id, selected_index, selected_row)
    before = deepcopy(selected_row)
    updated = deepcopy(selected_row)
    updated["context_note_id"] = key

    if operation == "correct":
        normalized_note = (note or "").strip()
        category = (category or selected_row.get("category") or "").strip()
        occurred_at = (occurred_at if occurred_at is not None else
                       selected_row.get("occurred_at")) or None
        if not normalized_note or len(normalized_note) > MAX_CONTEXT_NOTE_CHARS:
            raise HistoricalContextEditError("Context note must contain 1–1200 characters.")
        if category not in ALLOWED_CONTEXT_CATEGORIES:
            raise HistoricalContextEditError("Unsupported context category.")
        if occurred_at:
            try:
                if date.fromisoformat(str(occurred_at)).isoformat() != str(occurred_at):
                    raise ValueError("not a calendar date")
            except ValueError as exc:
                raise HistoricalContextEditError(
                    "Use a verified date YYYY-MM-DD, or leave the date blank."
                ) from exc
        updated.update({
            "category": category,
            "occurred_at": occurred_at,
            "note": normalized_note,
            "advisor_supplied": True,
            "context_status": "active",
        })
        if all(
            before.get(k) == updated.get(k)
            for k in ("category", "occurred_at", "note")
        ):
            raise HistoricalContextEditError("No context changes were entered.")
    else:
        updated.update({
            "context_status": "superseded",
            "superseded_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "superseded_by_user_id": actor_user_id,
            "supersession_reason": reason,
        })

    rows[selected_index - 1] = updated
    payload["advisor_context_notes"] = rows
    history = list(payload.get("context_correction_audit") or [])
    if len(history) >= 120:
        raise HistoricalContextEditError(
            "This draft has reached its correction history limit. Contact an administrator."
        )
    history.append({
        "context_note_id": key,
        "operation": operation,
        "reason": reason,
        "source": source,
        "operation_key": operation_key,
        "actor_user_id": actor_user_id,
        "recorded_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "before": before,
        "after": deepcopy(updated),
    })
    payload["context_correction_audit"] = history
    payload["advisor_review_note"] = (
        "Historical context corrected by an authorised advisor; original imported "
        "evidence and completed-work decisions remain unchanged."
    )
    save_reconciliation_review(
        extraction_id=extraction.id,
        actor_user_id=actor_user_id,
        reviewed_payload=payload,
    )
    db.session.flush()
    return {
        "note_id": key,
        "operation": operation,
        "active_note_count": sum(
            1 for row in rows if isinstance(row, dict) and is_active_context_note(row)
        ),
    }
