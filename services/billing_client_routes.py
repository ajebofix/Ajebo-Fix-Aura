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


@client_billing_bp.get("/admin/cars/<int:car_id>/billing/preview/<string:document_id>")
@login_required
def advisor_billing_document_preview(car_id: int, document_id: str):
    """Strictly read-only projection of linked Billing document for an advisor.

    Preview is not public release, client impersonation or financial approval.
    No native PDF is manufactured here.
    """
    if current_user.role != "admin":
        abort(403)
    active = CarOwnership.query.filter_by(car_id=car_id, is_active=True).all()
    if len(active) != 1:
        abort(404)
    ownership = active[0]
    try:
        detail = client_billing_document(
            car_id=car_id,
            owner_user_id=ownership.user_id,
            vin=ownership.car.vin,
            document_id=document_id,
            advisor_user_id=current_user.id,
        )
    except BillingBridgeUnavailable:
        current_app.logger.warning(
            "Advisor billing preview gateway unavailable for vehicle %s", car_id,
        )
        return render_template(
            "billing/document_unavailable.html", car=ownership.car,
        ), 503
    if not detail:
        abort(404)
    current_app.logger.info(
        "Advisor source financial preview car=%s document=%s actor=%s",
        car_id, document_id, current_user.id,
    )
    from flask import make_response
    response = make_response(render_template(
        "billing/owner_document.html",
        car=ownership.car,
        document=detail["document"],
        brand=detail["brand"],
        advisor_preview=True,
    ))
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@client_billing_bp.get("/admin/cars/<int:car_id>/billing/preview")
@login_required
def advisor_client_finance_preview(car_id: int):
    """Read-only advisor preview of the *published owner* financial projection.

    This never logs into or impersonates a client's session. Owner access is
    evaluated by the same server-to-server publication gates used for clients.
    """
    if current_user.role != "admin":
        abort(403)
    active_owners = CarOwnership.query.filter_by(
        car_id=car_id, is_active=True
    ).all()
    if len(active_owners) != 1 or not active_owners[0].user:
        abort(404)
    ownership = active_owners[0]
    try:
        snapshot = client_billing_snapshot(
            car_id=car_id,
            owner_user_id=ownership.user_id,
            vin=ownership.car.vin,
        )
    except BillingBridgeUnavailable:
        current_app.logger.warning(
            "Advisor read-only published-finance preview unavailable car=%s",
            car_id,
        )
        snapshot = {"state": "unavailable", "documents": [], "payments": []}
    current_app.logger.info(
        "Advisor client-finance preview actor=%s car=%s owner=%s state=%s",
        current_user.id, car_id, ownership.user_id, snapshot["state"],
    )
    from flask import make_response
    response = make_response(render_template(
        "billing/owner_vehicle.html",
        car=ownership.car,
        snapshot=snapshot,
        advisor_preview=True,
    ))
    response.headers["Cache-Control"] = "no-store"
    return response
