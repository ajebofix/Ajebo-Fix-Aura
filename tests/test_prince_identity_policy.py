"""Pure unit fixtures: no global vehicle records or owner identities are created."""
from dataclasses import replace

import pytest

from partnerships.prince_identity_policy import (
    IdentityContext, OwnerConfirmation, normalize_vin, reconciliation_blockers,
)


@pytest.fixture
def identity_context():
    # Format-only fictional unit value; not a VIN assigned to a real or staged car.
    return IdentityContext(
        tenant_id=1, job_id=2, customer_id=3, vehicle_id=4, handoff_id=5,
        source_vin="DEMO123456789ABCD", source_vin_verified=True, handoff_active=True,
        destination_car_id=6, destination_vin="DEMO123456789ABCD",
        ownership_id=7, ownership_car_id=6, ownership_user_id=8,
        ownership_active=True, ownership_ended=False, active_ownership_count=1,
        authenticated_owner_id=8, owner_account_active=True, owner_account_verified=True,
    )


@pytest.fixture
def owner_confirmation():
    return OwnerConfirmation(
        tenant_id=1, job_id=2, customer_id=3, vehicle_id=4, handoff_id=5,
        car_id=6, ownership_id=7, owner_user_id=8,
        customer_identity_verified=True, exact_projection_consent=True,
        current_and_unrevoked=True,
    )


def test_exact_scope_passes_preflight_only(identity_context, owner_confirmation):
    # Use a syntactically valid invented value, not persisted in any database.
    context = replace(identity_context, source_vin="  demx123456789abcd ",
                      destination_vin="DEMX123456789ABCD")
    assert reconciliation_blockers(context, owner_confirmation) == ()


@pytest.mark.parametrize("value", [None, "", "W221", "DEMO123456789ABCD", "DEMX123456789ABC", "DEMX123456789ABC!"])
def test_invalid_vin_format(value):
    assert normalize_vin(value) is None


@pytest.mark.parametrize(("change", "expected"), [
    ({"handoff_active": False}, "clinical_consent_missing_or_withdrawn"),
    ({"source_vin_verified": False}, "verified_workshop_vin_required"),
    ({"source_vin": None}, "verified_workshop_vin_required"),
    ({"destination_vin": "DEMX123456789ABCE"}, "exact_vin_match_required"),
    ({"ownership_active": False}, "unique_current_ownership_required"),
    ({"ownership_ended": True}, "unique_current_ownership_required"),
    ({"active_ownership_count": 2}, "unique_current_ownership_required"),
    ({"ownership_car_id": 19}, "unique_current_ownership_required"),
    ({"authenticated_owner_id": 19}, "authenticated_current_owner_required"),
    ({"owner_account_verified": False}, "authenticated_current_owner_required"),
    ({"owner_account_active": False}, "authenticated_current_owner_required"),
])
def test_identity_preflight_fails_closed(identity_context, owner_confirmation, change, expected):
    context = replace(identity_context, source_vin="DEMX123456789ABCD",
                      destination_vin="DEMX123456789ABCD")
    assert expected in reconciliation_blockers(replace(context, **change), owner_confirmation)


@pytest.mark.parametrize("field", ["tenant_id", "job_id", "customer_id", "vehicle_id", "handoff_id",
                                  "car_id", "ownership_id", "owner_user_id"])
def test_confirmation_cannot_be_reused_for_another_scope(identity_context, owner_confirmation, field):
    assert "confirmation_scope_mismatch" in reconciliation_blockers(
        identity_context, replace(owner_confirmation, **{field: 99}))


def test_separate_owner_proof_required(identity_context, owner_confirmation):
    assert "separate_owner_confirmation_required" in reconciliation_blockers(identity_context, None)
    assert "workshop_customer_identity_not_verified" in reconciliation_blockers(
        identity_context, replace(owner_confirmation, customer_identity_verified=False))
    for field in ("exact_projection_consent", "current_and_unrevoked"):
        assert "current_projection_consent_required" in reconciliation_blockers(
            identity_context, replace(owner_confirmation, **{field: False}))
