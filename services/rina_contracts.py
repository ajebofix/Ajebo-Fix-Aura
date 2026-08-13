"""Structured request/response contracts for Aura's Rina orchestration layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Final


RINA_STATE_ANSWERED: Final = "answered"
RINA_STATE_ABSTAINED: Final = "abstained"
RINA_STATE_VEHICLE_REQUIRED: Final = "vehicle_required"
RINA_STATE_AUTHORITY_DENIED: Final = "authority_denied"
RINA_STATE_ESCALATION_REQUIRED: Final = "escalation_required"
RINA_STATE_PROVIDER_UNAVAILABLE: Final = "provider_unavailable"

RINA_STATES: Final = frozenset(
    {
        RINA_STATE_ANSWERED,
        RINA_STATE_ABSTAINED,
        RINA_STATE_VEHICLE_REQUIRED,
        RINA_STATE_AUTHORITY_DENIED,
        RINA_STATE_ESCALATION_REQUIRED,
        RINA_STATE_PROVIDER_UNAVAILABLE,
    }
)

PROVIDER_STATUS_NOT_CALLED: Final = "not_called"
PROVIDER_STATUS_OK: Final = "ok"
PROVIDER_STATUS_UNAVAILABLE: Final = "unavailable"
PROVIDER_STATUS_REJECTED: Final = "rejected"


@dataclass(frozen=True)
class RinaRequest:
    request_id: str
    user_id: int
    car_id: int
    authority: str
    channel: str
    message: str
    conversation_id: str
    context_version: int
    memory_policy: str
    allowed_actions: tuple[str, ...]
    denied_actions: tuple[str, ...]

    def to_provider_safe_dict(self) -> dict[str, Any]:
        """Return structured metadata suitable for minimized provider context.

        The user message itself is intentionally included because it is required
        for response generation.  Secrets, raw system prompts and private record
        bodies are never fields on this contract.
        """

        return asdict(self)


@dataclass(frozen=True)
class RinaResponse:
    request_id: str
    car_id: int
    authority: str
    state: str
    message: str
    uncertainty: str | None
    escalation: str | None
    actions: tuple[str, ...]
    evidence_refs: tuple[dict[str, Any], ...]
    provider_status: str

    def __post_init__(self) -> None:
        if self.state not in RINA_STATES:
            raise ValueError(f"unsupported Rina response state: {self.state!r}")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence_refs"] = [dict(item) for item in self.evidence_refs]
        return payload
