"""Authority-first policy service for A.J. Rina.

Rina authority is deliberately separate from tone and provider behavior.  This
module resolves an authenticated account's effective authority for one explicit
vehicle using persisted Aura relationships, then derives a deny-by-default
record/action policy.

Existing route authorization remains owned by ``security.access``.  Wave 1.3
uses this service for Rina orchestration so global account role and
vehicle-specific relationship remain distinguishable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from models import (
    AdvisorNote,
    Car,
    CarDriver,
    CarOwnership,
    Consultation,
    TreatmentPlan,
    User,
    VehicleAssessment,
)


AUTHORITY_OWNER: Final = "owner"
AUTHORITY_DRIVER: Final = "driver"
AUTHORITY_ADVISOR: Final = "advisor"
AUTHORITY_ADMINISTRATOR: Final = "administrator"

SUPPORTED_AUTHORITIES: Final = frozenset(
    {
        AUTHORITY_OWNER,
        AUTHORITY_DRIVER,
        AUTHORITY_ADVISOR,
        AUTHORITY_ADMINISTRATOR,
    }
)

ACTION_READ_CLIENT_VEHICLE_CONTEXT: Final = "read_client_vehicle_context"
ACTION_READ_CLIENT_PROGRESSION: Final = "read_client_progression"
ACTION_READ_CHAT_HISTORY: Final = "read_chat_history"
ACTION_READ_CLIENT_SUMMARY: Final = "read_client_summary"
ACTION_READ_ADVISOR_MEMORY: Final = "read_advisor_memory"
ACTION_READ_OWNER_FINANCIAL_CONTEXT: Final = "read_owner_financial_context"
ACTION_REQUEST_CONSULTATION: Final = "request_consultation"
ACTION_APPROVE_ASSESSMENT: Final = "approve_assessment"
ACTION_APPROVE_TREATMENT: Final = "approve_treatment"
ACTION_ADMIN_GOVERNANCE: Final = "admin_governance"

ALL_RINA_ACTIONS: Final = frozenset(
    {
        ACTION_READ_CLIENT_VEHICLE_CONTEXT,
        ACTION_READ_CLIENT_PROGRESSION,
        ACTION_READ_CHAT_HISTORY,
        ACTION_READ_CLIENT_SUMMARY,
        ACTION_READ_ADVISOR_MEMORY,
        ACTION_READ_OWNER_FINANCIAL_CONTEXT,
        ACTION_REQUEST_CONSULTATION,
        ACTION_APPROVE_ASSESSMENT,
        ACTION_APPROVE_TREATMENT,
        ACTION_ADMIN_GOVERNANCE,
    }
)

# Human approval remains outside Rina for every role.
_ALWAYS_DENIED: Final = frozenset(
    {
        ACTION_APPROVE_ASSESSMENT,
        ACTION_APPROVE_TREATMENT,
    }
)

_ALLOWED_BY_AUTHORITY: Final = {
    AUTHORITY_OWNER: frozenset(
        {
            ACTION_READ_CLIENT_VEHICLE_CONTEXT,
            ACTION_READ_CLIENT_PROGRESSION,
            ACTION_READ_CHAT_HISTORY,
            ACTION_READ_CLIENT_SUMMARY,
            ACTION_READ_OWNER_FINANCIAL_CONTEXT,
            ACTION_REQUEST_CONSULTATION,
        }
    ),
    AUTHORITY_DRIVER: frozenset(
        {
            ACTION_READ_CLIENT_VEHICLE_CONTEXT,
            ACTION_READ_CLIENT_PROGRESSION,
            ACTION_READ_CHAT_HISTORY,
            ACTION_READ_CLIENT_SUMMARY,
            ACTION_REQUEST_CONSULTATION,
        }
    ),
    AUTHORITY_ADVISOR: frozenset(
        {
            ACTION_READ_CLIENT_VEHICLE_CONTEXT,
            ACTION_READ_CLIENT_PROGRESSION,
            ACTION_READ_CHAT_HISTORY,
            ACTION_READ_CLIENT_SUMMARY,
            ACTION_READ_ADVISOR_MEMORY,
            ACTION_REQUEST_CONSULTATION,
        }
    ),
    AUTHORITY_ADMINISTRATOR: frozenset(
        {
            ACTION_READ_CLIENT_VEHICLE_CONTEXT,
            ACTION_READ_CLIENT_PROGRESSION,
            ACTION_READ_CHAT_HISTORY,
            ACTION_READ_CLIENT_SUMMARY,
            ACTION_READ_ADVISOR_MEMORY,
            ACTION_READ_OWNER_FINANCIAL_CONTEXT,
            ACTION_REQUEST_CONSULTATION,
            ACTION_ADMIN_GOVERNANCE,
        }
    ),
}


class RinaAuthorityError(PermissionError):
    """Base class for authority resolution failures."""


class RinaIdentityUnavailable(RinaAuthorityError):
    """The authenticated user cannot be proven as active."""


class RinaVehicleUnavailable(RinaAuthorityError):
    """The requested vehicle does not exist."""


class RinaVehicleAuthorityDenied(RinaAuthorityError):
    """No supported relationship to the requested vehicle can be proven."""


@dataclass(frozen=True)
class RinaAuthorityContext:
    user_id: int
    car_id: int
    global_role: str
    authority: str
    relationships: tuple[str, ...]
    allowed_actions: frozenset[str]
    denied_actions: frozenset[str]

    def allows(self, action: str) -> bool:
        return action in self.allowed_actions

    def to_safe_dict(self) -> dict[str, object]:
        """Return provider/audit-safe authority metadata without private rows."""

        return {
            "user_id": self.user_id,
            "car_id": self.car_id,
            "global_role": self.global_role,
            "authority": self.authority,
            "relationships": list(self.relationships),
            "allowed_actions": sorted(self.allowed_actions),
            "denied_actions": sorted(self.denied_actions),
        }


def _has_owner_relationship(user_id: int, car_id: int) -> bool:
    return (
        CarOwnership.query.filter_by(
            user_id=user_id,
            car_id=car_id,
            is_active=True,
        ).first()
        is not None
    )


def _has_driver_relationship(user_id: int, car_id: int) -> bool:
    return (
        CarDriver.query.filter_by(
            user_id=user_id,
            car_id=car_id,
            is_active=True,
        ).first()
        is not None
    )


def _has_explicit_advisor_scope(user_id: int, car_id: int) -> bool:
    """Prove professional scope from existing vehicle-linked operational rows.

    Aura does not yet have a dedicated advisor-assignment model.  Until it does,
    Rina may treat an account with global role ``advisor`` as vehicle-scoped only
    when an existing consultation, assessment, treatment plan or advisor note
    links that advisor to the vehicle.  A role string alone is not sufficient.
    """

    checks = (
        Consultation.query.filter_by(car_id=car_id, advisor_id=user_id).first(),
        VehicleAssessment.query.filter_by(car_id=car_id, advisor_id=user_id).first(),
        TreatmentPlan.query.filter_by(car_id=car_id, advisor_id=user_id).first(),
        AdvisorNote.query.filter_by(car_id=car_id, advisor_id=user_id).first(),
    )
    return any(item is not None for item in checks)


def _relationships_for(user: User, car_id: int) -> tuple[str, ...]:
    relationships: list[str] = []

    if _has_owner_relationship(user.id, car_id):
        relationships.append(AUTHORITY_OWNER)

    if _has_driver_relationship(user.id, car_id):
        relationships.append(AUTHORITY_DRIVER)

    if _has_explicit_advisor_scope(user.id, car_id):
        relationships.append(AUTHORITY_ADVISOR)

    if user.role == "admin":
        relationships.append(AUTHORITY_ADMINISTRATOR)

    return tuple(relationships)


def _effective_authority(user: User, relationships: tuple[str, ...]) -> str | None:
    # Administrator preserves Aura's current broad admin access but remains
    # semantically distinct from ownership and clinical advisor scope.
    if user.role == "admin":
        return AUTHORITY_ADMINISTRATOR

    # Future dedicated advisor accounts must still prove vehicle scope.
    if user.role == "advisor" and AUTHORITY_ADVISOR in relationships:
        return AUTHORITY_ADVISOR

    if AUTHORITY_OWNER in relationships:
        return AUTHORITY_OWNER

    if AUTHORITY_DRIVER in relationships:
        return AUTHORITY_DRIVER

    return None


def resolve_rina_authority(*, user_id: int, car_id: int) -> RinaAuthorityContext:
    """Resolve Rina authority for one explicit vehicle, failing closed.

    The resolver never guesses a vehicle and never derives owner/driver/advisor
    authority from free text or provider output.
    """

    user = User.query.filter_by(id=user_id, is_active=True).first()
    if user is None:
        raise RinaIdentityUnavailable("active authenticated identity is required")

    if Car.query.filter_by(id=car_id).first() is None:
        raise RinaVehicleUnavailable("requested vehicle does not exist")

    relationships = _relationships_for(user, car_id)
    authority = _effective_authority(user, relationships)
    if authority is None:
        raise RinaVehicleAuthorityDenied(
            "no supported authority can be proven for the requested vehicle"
        )

    allowed = frozenset(_ALLOWED_BY_AUTHORITY[authority] - _ALWAYS_DENIED)
    denied = frozenset(ALL_RINA_ACTIONS - allowed)

    return RinaAuthorityContext(
        user_id=user.id,
        car_id=car_id,
        global_role=user.role,
        authority=authority,
        relationships=relationships,
        allowed_actions=allowed,
        denied_actions=denied,
    )


def require_rina_action(
    *,
    authority_context: RinaAuthorityContext,
    action: str,
) -> None:
    """Deny unknown or disallowed Rina actions by default."""

    if action not in ALL_RINA_ACTIONS:
        raise RinaVehicleAuthorityDenied("unknown Rina action is denied")

    if not authority_context.allows(action):
        raise RinaVehicleAuthorityDenied(
            f"Rina authority {authority_context.authority!r} cannot perform {action!r}"
        )
