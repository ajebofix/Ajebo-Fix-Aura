"""Client-only read view of existing Ajebo Fix Billing records inside Aura."""
from __future__ import annotations

from flask import Blueprint, abort, current_app, render_template
from flask_login import current_user, login_required

from models import CarOwnership
from services.billing_client_bridge import BillingBridgeUnavailable, client_billing_snapshot


client_billing_bp = Blueprint("client_billing", __name__)


@client_billing_bp.get("/cars/<int:car_id>/billing")
@login_required
def owner_vehicle_billing(car_id: int):
    # An advisor, administrator, or assigned driver does not inherit the
    # owner's financial access. Only the current authenticated owner qualifies.
    if current_user.role != "user" or not getattr(current_user, "email_verified_at", None):
        abort(403)
    ownership = CarOwnership.query.filter_by(
        car_id=car_id,
        user_id=current_user.id,
        is_active=True,
    ).first_or_404()

    try:
        snapshot = client_billing_snapshot(
            car_id=ownership.car_id,
            owner_user_id=current_user.id,
            vin=ownership.car.vin,
        )
    except BillingBridgeUnavailable:
        # No Supabase errors, secrets, or internal identity fields reach HTML.
        current_app.logger.warning("Owner billing projection temporarily unavailable")
        snapshot = {"state": "unavailable", "documents": [], "payments": []}

    return render_template(
        "billing/owner_vehicle.html",
        car=ownership.car,
        snapshot=snapshot,
    )
