"""Client-only read view of existing Ajebo Fix Billing records inside Aura."""
from __future__ import annotations

from flask import Blueprint, abort, current_app, render_template
from flask_login import current_user, login_required

from models import CarOwnership
from services.billing_client_bridge import (
    BillingBridgeUnavailable,
    client_billing_snapshot,
    client_billing_document,
)
from services.client_repair_progress import client_published_progress


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


@client_billing_bp.get("/cars/<int:car_id>/repair-progress")
@login_required
def owner_repair_progress(car_id: int):
    if current_user.role != "user" or not getattr(current_user, "email_verified_at", None):
        abort(403)
    ownership = CarOwnership.query.filter_by(
        car_id=car_id,
        user_id=current_user.id,
        is_active=True,
    ).first_or_404()
    return render_template(
        "treatment_actions/owner_repair_progress.html",
        car=ownership.car,
        updates=client_published_progress(
            car_id=car_id, owner_user_id=current_user.id
        ),
    )


@client_billing_bp.get("/cars/<int:car_id>/billing/documents/<string:document_id>")
@login_required
def owner_billing_document(car_id: int, document_id: str):
    """Source-backed document details. Never return a Billing share token."""
    if current_user.role != "user" or not getattr(current_user, "email_verified_at", None):
        abort(403)
    ownership = CarOwnership.query.filter_by(
        car_id=car_id,
        user_id=current_user.id,
        is_active=True,
    ).first_or_404()
    try:
        details = client_billing_document(
            car_id=ownership.car_id,
            owner_user_id=current_user.id,
            vin=ownership.car.vin,
            document_id=document_id,
        )
    except BillingBridgeUnavailable:
        current_app.logger.warning("Billing document detail temporarily unavailable")
        return render_template(
            "billing/document_unavailable.html", car=ownership.car,
        ), 503
    if details is None:
        abort(404)
    response = render_template(
        "billing/owner_document.html",
        car=ownership.car,
        document=details["document"],
        brand=details["brand"],
    )
    from flask import make_response
    result = make_response(response)
    result.headers["Cache-Control"] = "private, no-store"
    result.headers["X-Content-Type-Options"] = "nosniff"
    result.headers["Referrer-Policy"] = "no-referrer"
    return result
