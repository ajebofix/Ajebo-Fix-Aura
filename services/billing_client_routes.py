"""Client-only read view of existing Ajebo Fix Billing records inside Aura."""
from __future__ import annotations

from flask import Blueprint, abort, current_app, render_template, request, flash, redirect, url_for
from flask_login import current_user, login_required

from models import CarOwnership, AdvisorNote, User, Car
from extensions import db
from sqlalchemy import or_
from services.billing_client_bridge import (
    BillingBridgeUnavailable,
    client_billing_snapshot,
    client_billing_document,
    advisor_billing_inventory,
)
from services.client_repair_progress import client_published_progress
from services.billing_accounts_delivery import (
    DELIVERY_PREFIX, already_delivered, publish_native_billing_estimate,
    send_accounts_estimate_via_resend,
)
import json


client_billing_bp = Blueprint("client_billing", __name__)



@client_billing_bp.get("/my-financial-records")
@login_required
def owner_financial_records_index():
    """Vehicle-first access to only the signed-in, verified owner's accounts."""
    if current_user.role != "user" or not getattr(current_user, "email_verified_at", None):
        abort(403)
    ownerships = (
        CarOwnership.query.filter_by(
            user_id=current_user.id, is_active=True,
        )
        .order_by(CarOwnership.start_date.desc())
        .all()
    )
    from flask import make_response
    response = make_response(render_template(
        "billing/owner_index.html", ownerships=ownerships,
    ))
    response.headers["Cache-Control"] = "private, no-store"
    return response


@client_billing_bp.get("/admin/billing")
@login_required
def advisor_billing_index():
    """Advisor Billing & Accounts hub: clients selected by current ownership."""
    if current_user.role != "admin":
        abort(403)
    query = request.args.get("q", "").strip()[:80]
    ownerships_query = (
        CarOwnership.query
        .join(CarOwnership.car)
        .join(CarOwnership.user)
        .filter(
            CarOwnership.is_active.is_(True),
            User.is_active.is_(True),
            User.role == "user",
        )
    )
    if query:
        term = f"%{query}%"
        ownerships_query = ownerships_query.filter(or_(
            User.name.ilike(term),
            User.email.ilike(term),
            Car.vin.ilike(term),
            Car.brand.ilike(term),
            Car.model.ilike(term),
            CarOwnership.plate_number.ilike(term),
        ))
    ownerships = ownerships_query.order_by(
        CarOwnership.id.desc(),
    ).limit(100).all()
    from flask import make_response
    response = make_response(render_template(
        "billing/advisor_index.html",
        ownerships=ownerships, query=query,
    ))
    response.headers["Cache-Control"] = "private, no-store"
    return response


@client_billing_bp.get("/admin/cars/<int:car_id>/billing/workspace")
@login_required
def advisor_billing_workspace(car_id: int):
    """Admin-only vehicle workspace with actionable Billing document links."""
    if current_user.role != "admin":
        abort(403)
    ownerships = CarOwnership.query.filter_by(
        car_id=car_id, is_active=True,
    ).all()
    if len(ownerships) != 1 or ownerships[0].user is None:
        abort(404)
    ownership = ownerships[0]
    if ownership.user.role != "user" or not ownership.user.is_active:
        abort(404)
    try:
        inventory = advisor_billing_inventory(
            car_id=car_id,
            owner_user_id=ownership.user_id,
            advisor_user_id=current_user.id,
            vin=ownership.car.vin,
        )
    except BillingBridgeUnavailable:
        current_app.logger.warning("Advisor Billing inventory unavailable car=%s",car_id)
        inventory = {"state": "unavailable", "documents": []}
    documents = inventory["documents"]
    for item in documents:
        item["submitted_to_resend"] = (
            item["kind"] == "estimate"
            and already_delivered(
                car_id=car_id, owner_user_id=ownership.user_id,
                document_id=item["id"],
            )
        )
    from flask import make_response
    response = make_response(render_template(
        "billing/advisor_workspace.html",
        car=ownership.car, ownership=ownership, inventory=inventory,
        documents=documents,
    ))
    response.headers["Cache-Control"] = "private, no-store"
    return response


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


@client_billing_bp.route(
    "/admin/cars/<int:car_id>/billing/estimate/<string:document_id>/send",
    methods=["GET", "POST"],
)
@login_required
def advisor_issue_estimate_email(car_id: int, document_id: str):
    """Advisor reviews and explicitly sends a native Billing estimate via Resend.

    Requires independent Billing native-created provenance + owner publication
    and an exact authenticated, verified owner email. No impersonation,
    browser-held Resend secret, or premature financial completion flag.
    """
    if current_user.role != "admin":
        abort(403)
    owners = CarOwnership.query.filter_by(car_id=car_id, is_active=True).all()
    if len(owners) != 1 or owners[0].user is None:
        abort(404)
    owner_link = owners[0]
    owner = owner_link.user
    if not owner.is_active or not getattr(owner, "email_verified_at", None) or not owner.email:
        abort(409)
    try:
        detail = client_billing_document(
            car_id=car_id, owner_user_id=owner.id,
            advisor_user_id=current_user.id,
            vin=owner_link.car.vin,
            document_id=document_id,
        )
    except BillingBridgeUnavailable:
        flash("Billing estimate not available. Please retry.", "error")
        return redirect(url_for("client_billing.advisor_client_finance_preview", car_id=car_id))
    if detail is None:
        abort(404)
    doc = detail["document"]
    if doc.get("kind") != "estimate" or doc.get("group") != "job_record":
        abort(404)
    if request.method == "POST":
        if request.form.get("confirmed") != "yes":
            flash("Review the original Billing estimate and confirm before sending.", "error")
            return redirect(request.path)
        if doc.get("status") not in {"issued", "sent"}:
            flash("Issue the estimate inside Ajebo Fix Billing before sending.", "error")
            return redirect(request.path)
        if already_delivered(
            car_id=car_id, owner_user_id=owner.id, document_id=doc["id"]
        ):
            flash("This estimate was already submitted to Resend. Review its delivery record before resending.", "error")
            return redirect(request.path)
        try:
            percent = int(request.form.get("upfront_percentage", ""))
            publish_native_billing_estimate(
                car_id=car_id, owner_user_id=owner.id,
                advisor_user_id=current_user.id,
                vin=owner_link.car.vin, document_id=doc["id"],
            )
            # Obtain a SECOND independent owner-scoped publication read.
            published = client_billing_document(
                car_id=car_id, owner_user_id=owner.id,
                vin=owner_link.car.vin, document_id=doc["id"],
            )
            if not published or published["document"]["id"] != doc["id"]:
                raise BillingBridgeUnavailable("Owner document not accessible")
            provider_id = send_accounts_estimate_via_resend(
                to=owner.email,
                customer=published["document"]["billed_to"],
                vehicle=owner_link.car.rina_display_name,
                document=published["document"],
                car_id=car_id,
                upfront_percentage=percent,
            )
            db.session.add(AdvisorNote(
                user_id=owner.id,
                car_id=car_id,
                advisor_id=current_user.id,
                note=DELIVERY_PREFIX + json.dumps({
                    "event": "submitted",
                    "document_id": doc["id"],
                    "document_number": doc["number"],
                    "provider_message_id": provider_id,
                    "upfront_percentage": percent,
                    "actor_user_id": current_user.id,
                }, separators=(",", ":")),
            ))
            db.session.commit()
            flash(f"Resend accepted the estimate email (ID {provider_id}). Delivery confirmation may follow.", "success")
        except (BillingBridgeUnavailable, ValueError) as exc:
            db.session.rollback()
            current_app.logger.warning(
                "Restricted Billing accounts send was not confirmed car=%s", car_id
            )
            flash(str(exc), "error")
        return redirect(request.path)

    previously_submitted = already_delivered(
        car_id=car_id, owner_user_id=owner.id, document_id=doc["id"]
    )
    response = render_template(
        "billing/advisor_estimate_send.html",
        car=owner_link.car, owner=owner, document=doc,
        sent_before=previously_submitted,
    )
    from flask import make_response
    result = make_response(response)
    result.headers["Cache-Control"] = "no-store"
    return result
