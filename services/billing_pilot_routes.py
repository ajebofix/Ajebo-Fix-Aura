"""Private advisor-only, read-only Billing identity verification workspace."""
from __future__ import annotations

from flask import Blueprint, abort, current_app, render_template, request
from flask_login import current_user, login_required

from models import Car, CarOwnership
from services.billing_client_bridge import (
    BillingBridgeUnavailable, advisor_job_link_preflight,
)

billing_pilot_bp = Blueprint("billing_pilot", __name__)


@billing_pilot_bp.route("/admin/vehicles/<int:car_id>/billing-preflight", methods=["GET", "POST"])
@login_required
def advisor_billing_preflight(car_id: int):
    if current_user.role != "admin":
        abort(403)

    car = Car.query.get_or_404(car_id)
    owner_links = CarOwnership.query.filter_by(car_id=car_id, is_active=True).all()
    # Refuse to determine whom to bill if ownership is absent or ambiguous.
    if len(owner_links) != 1 or not owner_links[0].user:
        return render_template(
            "billing/advisor_preflight.html",
            car=car, owner=None, result={"status": "owner_not_verified"},
        ), 409

    owner = owner_links[0].user
    result = None
    if request.method == "POST":
        job_uuid = str(request.form.get("billing_job_uuid") or "").strip()
        try:
            result = advisor_job_link_preflight(
                car_id=car.id,
                vin=car.vin,
                owner_email=owner.email,
                job_uuid=job_uuid,
            )
        except BillingBridgeUnavailable:
            current_app.logger.warning("Billing advisor preflight unavailable")
            result = {"status": "billing_unavailable"}

    return render_template(
        "billing/advisor_preflight.html", car=car, owner=owner, result=result,
    )
