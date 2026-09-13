"""Advisor-governed verification review for existing historical service facts.

Classification answers *what maintenance item this service completed*.
Verification answers *how strongly Aura can trust that the historical service occurred*.
Those are deliberately separate professional facts.
"""

from __future__ import annotations

from datetime import datetime

from models import User, VehicleEvent


HISTORICAL_SERVICE_INFORMATION_SOURCES = {
    "client_provided": "Client-provided history",
    "invoice_receipt": "Service invoice / receipt",
    "workshop_record": "Workshop / dealer record",
    "service_book": "Service booklet / log",
    "other": "Other documented source",
}

HISTORICAL_SERVICE_VERIFICATION_STATUSES = {
    "unverified": "Unverified",
    "document_reviewed": "Supporting document reviewed",
    "advisor_confirmed": "Advisor-confirmed from available evidence",
}


class HistoricalServiceVerificationError(ValueError):
    """Raised when an existing service fact cannot be reviewed safely."""


class HistoricalServiceVerificationAuthorityError(PermissionError):
    """Raised when a non-advisor attempts to verify service history."""


def _require_advisor(actor_user_id: int) -> User:
    user = User.query.get(actor_user_id)
    if user is None or user.role != "admin" or not user.is_active:
        raise HistoricalServiceVerificationAuthorityError(
            "Only an active advisor can review historical service evidence."
        )
    return user


class HistoricalServiceVerificationService:
    """Append-preserving review of provenance on a saved historical service event."""

    @staticmethod
    def review(
        *,
        service_event_id: int,
        actor_user_id: int,
        information_source: str,
        verification_status: str,
        reviewed_at: datetime | None = None,
    ) -> tuple[VehicleEvent, bool]:
        _require_advisor(actor_user_id)

        event = VehicleEvent.query.filter_by(
            id=service_event_id,
            event_type="service",
            is_deleted=False,
        ).first()
        if event is None:
            raise HistoricalServiceVerificationError("Historical service record was not found.")

        data = dict(event.data or {})
        if data.get("record_mode") != "historical":
            raise HistoricalServiceVerificationError(
                "Only historical service records can use evidence review."
            )

        if information_source not in HISTORICAL_SERVICE_INFORMATION_SOURCES:
            raise HistoricalServiceVerificationError("Select a valid source of information.")
        if verification_status not in HISTORICAL_SERVICE_VERIFICATION_STATUSES:
            raise HistoricalServiceVerificationError("Select a valid verification status.")

        previous_source = data.get("information_source")
        previous_verification = data.get("verification_status")
        if (
            previous_source == information_source
            and previous_verification == verification_status
        ):
            return event, False

        reviewed_at = reviewed_at or datetime.utcnow()
        history = list(data.get("verification_history") or [])
        history.append(
            {
                "previous_information_source": previous_source,
                "previous_verification_status": previous_verification,
                "information_source": information_source,
                "information_source_label": HISTORICAL_SERVICE_INFORMATION_SOURCES[
                    information_source
                ],
                "verification_status": verification_status,
                "verification_status_label": HISTORICAL_SERVICE_VERIFICATION_STATUSES[
                    verification_status
                ],
                "reviewed_by_user_id": actor_user_id,
                "reviewed_at": reviewed_at.isoformat(timespec="seconds") + "Z",
            }
        )

        data.update(
            {
                "information_source": information_source,
                "information_source_label": HISTORICAL_SERVICE_INFORMATION_SOURCES[
                    information_source
                ],
                "verification_status": verification_status,
                "verification_status_label": HISTORICAL_SERVICE_VERIFICATION_STATUSES[
                    verification_status
                ],
                "verification_reviewed_by_user_id": actor_user_id,
                "verification_reviewed_at": reviewed_at.isoformat(timespec="seconds") + "Z",
                "verification_history": history,
            }
        )
        event.data = data
        return event, True
