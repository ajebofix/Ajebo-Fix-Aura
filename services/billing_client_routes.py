"""Client-only read view of existing Ajebo Fix Billing records inside Aura."""
from __future__ import annotations

from flask import Blueprint, abort, current_app, render_template, request, flash, redirect, url_for
from flask_login import current_user, login_required

from models import CarOwnership, AdvisorNote, User, Car
from extensions import db
from sqlalchemy import or_
from decimal import Decimal
from services.billing_client_bridge import (
    BillingBridgeUnavailable,
    client_billing_snapshot,
    client_billing_document,
    advisor_billing_inventory,
)
from services.client_repair_progress import client_published_progress
from services.billing_accounts_delivery import (
    DELIVERY_PREFIX, already_delivered, publish_native_billing_estimate,
    send_accounts_estimate_via_resend, validate_estimate_delivery,
    approve_native_estimate_issue, publish_native_billing_document,
    send_accounts_document_via_resend,
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
            item["kind"] in {"estimate", "invoice", "receipt"}
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
    "/admin/cars/<int:car_id>/billing/documents/<string:document_id>/delivery",
    methods=["GET", "POST"],
)
@login_required
def advisor_invoice_receipt_delivery(car_id: int, document_id: str):
    """Review, publish and separately email a verified invoice or receipt.

    Billing is authoritative. Publishing is owner-visible but does not send.
    Sending is a second advisor action with duplicate submission prevention.
    """
    if current_user.role != "admin":
        abort(403)
    ownerships = CarOwnership.query.filter_by(car_id=car_id, is_active=True).all()
    if len(ownerships) != 1 or ownerships[0].user is None:
        abort(404)
    link = ownerships[0]
    owner = link.user
    if (owner.role != "user" or not owner.is_active
        or not getattr(owner, "email_verified_at", None) or not owner.email):
        abort(409)
    try:
        inventory = advisor_billing_inventory(
            car_id=car_id, owner_user_id=owner.id,
            advisor_user_id=current_user.id, vin=link.car.vin,
        )
        matching = [item for item in inventory.get("documents", [])
                    if item["id"] == document_id]
        if len(matching) != 1:
            abort(404)
        entry = matching[0]
        if (entry["kind"] not in {"invoice", "receipt"}
            or not entry["native_created"] or not entry["job_linked"]):
            abort(404)
        detail = client_billing_document(
            car_id=car_id, owner_user_id=owner.id,
            advisor_user_id=current_user.id, vin=link.car.vin,
            document_id=document_id,
        )
    except BillingBridgeUnavailable:
        current_app.logger.warning("Billing delivery review unavailable car=%s", car_id)
        return render_template("billing/document_unavailable.html", car=link.car), 503
    if not detail or detail["document"]["kind"] != entry["kind"]:
        abort(404)
    doc = detail["document"]
    eligible = {
        "invoice": {"issued", "sent", "overdue", "partially_paid", "paid"},
        "receipt": {"issued"},
    }
    if doc["status"] not in eligible[doc["kind"]]:
        flash("This Billing document is not ready for publication.", "error")
        return redirect(url_for("client_billing.advisor_billing_workspace",car_id=car_id))
    was_sent = already_delivered(
        car_id=car_id,owner_user_id=owner.id,document_id=doc["id"],
    )

    if request.method == "POST":
        requested = request.form.get("action")
        if (requested not in {"publish", "send"} or
            request.form.get("confirmed") != "yes" or
            request.form.get("expected_updated_at") != doc["updated_at"] or
            request.form.get("expected_total") != doc["total"] or
            request.form.get("expected_paid") != doc["paid"] or
            request.form.get("expected_status") != doc["status"]):
            flash("Document details changed. Review the current Billing record again.", "error")
            return redirect(request.path)
        if requested == "publish":
            if entry["published"]:
                flash("Document is already published. Use the separate send approval.", "error")
                return redirect(request.path)
            try:
                publish_native_billing_document(
                    car_id=car_id, owner_user_id=owner.id,
                    advisor_user_id=current_user.id,
                    vin=link.car.vin, document_id=doc["id"],
                )
                published = client_billing_document(
                    car_id=car_id, owner_user_id=owner.id,
                    vin=link.car.vin, document_id=doc["id"],
                )
                if not published or published["document"]["id"] != doc["id"]:
                    raise BillingBridgeUnavailable("Owner publication not confirmed")
                flash(
                    f"{doc['kind'].title()} published in Aura. No email sent.",
                    "success",
                )
            except BillingBridgeUnavailable as exc:
                flash(str(exc), "error")
            return redirect(request.path)

        if not entry["published"] or was_sent:
            flash("Publish this document first, or review its previous email.", "error")
            return redirect(request.path)
        try:
            # Re-read strictly as the current owner, without advisor privileges.
            published = client_billing_document(
                car_id=car_id, owner_user_id=owner.id,
                vin=link.car.vin, document_id=doc["id"],
            )
            if not published or published["document"]["id"] != doc["id"]:
                raise BillingBridgeUnavailable("Owner document not available")
            owner_doc = published["document"]
            if (owner_doc["total"] != doc["total"]
                or owner_doc["paid"] != doc["paid"]
                or owner_doc["status"] != doc["status"]):
                raise BillingBridgeUnavailable("Amounts changed. Review again before sending")
            provider_id = send_accounts_document_via_resend(
                to=owner.email, customer=owner_doc["billed_to"],
                vehicle=link.car.rina_display_name,
                document=owner_doc, car_id=car_id,
            )
            db.session.add(AdvisorNote(
                user_id=owner.id,car_id=car_id,advisor_id=current_user.id,
                note=DELIVERY_PREFIX+json.dumps({
                    "event":"submitted",
                    "document_id":doc["id"],"document_number":doc["number"],
                    "document_kind":doc["kind"],"provider_message_id":provider_id,
                    "actor_user_id":current_user.id,
                    "paid":doc["paid"],"balance":doc["balance"],
                },separators=(",",":")),
            ))
            db.session.commit()
            flash(
                f"Resend accepted the {doc['kind']} email (ID {provider_id}). "
                "Delivery confirmation may follow.",
                "success",
            )
        except BillingBridgeUnavailable as exc:
            db.session.rollback()
            flash(str(exc),"error")
        return redirect(request.path)
    from flask import make_response
    result = make_response(render_template(
        "billing/advisor_invoice_receipt_delivery.html",
        car=link.car,owner=owner,document=doc,
        published=entry["published"],sent_before=was_sent,
    ))
    result.headers["Cache-Control"] = "private, no-store"
    return result


@client_billing_bp.route(
    "/admin/cars/<int:car_id>/billing/estimate/<string:document_id>/issue",
    methods=["GET", "POST"],
)
@login_required
def advisor_review_issue_estimate(car_id: int, document_id: str):
    """Separate adviser approval: Draft -> Issued, never publish or send."""
    if current_user.role != "admin":
        abort(403)
    active = CarOwnership.query.filter_by(car_id=car_id, is_active=True).all()
    if len(active) != 1 or active[0].user is None:
        abort(404)
    link = active[0]
    if link.user.role != "user" or not link.user.is_active:
        abort(404)
    try:
        detail = client_billing_document(
            car_id=car_id, owner_user_id=link.user_id,
            advisor_user_id=current_user.id, vin=link.car.vin,
            document_id=document_id,
        )
    except BillingBridgeUnavailable:
        return render_template("billing/document_unavailable.html", car=link.car), 503
    if not detail or detail["document"]["kind"] != "estimate":
        abort(404)
    doc = detail["document"]
    if doc["status"] != "draft" or doc["group"] != "job_record":
        flash("This document is not an eligible native draft.", "error")
        return redirect(url_for("client_billing.advisor_billing_workspace", car_id=car_id))
    try:
        total, upfront, balance = validate_estimate_delivery(
            doc, 90, allow_draft=True,
        )
        validation_issue = None
    except BillingBridgeUnavailable as exc:
        validation_issue = str(exc)
        total = Decimal(doc["total"])
        upfront = (total * Decimal("0.90")).quantize(Decimal("0.01"))
        balance = total - upfront

    if request.method == "POST":
        if validation_issue:
            flash(validation_issue, "error")
            return redirect(request.path)
        if (request.form.get("confirmed") != "yes"
            or request.form.get("expected_updated_at") != doc["updated_at"]
            or request.form.get("expected_revision") != str(doc["revision"])
            or request.form.get("expected_total") != doc["total"]):
            flash("Review the current draft again before issuing it.", "error")
            return redirect(request.path)
        try:
            approve_native_estimate_issue(
                car_id=car_id, owner_user_id=link.user_id,
                advisor_user_id=current_user.id, vin=link.car.vin,
                document=doc,
            )
        except BillingBridgeUnavailable as exc:
            flash(str(exc), "error")
            return redirect(request.path)
        flash(
            "Estimate issued in Billing. No client publication, email, or payment "
            "was performed. You can now review and send it separately.",
            "success",
        )
        return redirect(url_for("client_billing.advisor_billing_workspace", car_id=car_id))

    from flask import make_response
    response = make_response(render_template(
        "billing/advisor_estimate_issue.html", car=link.car,
        owner=link.user, document=doc, total=total, upfront=upfront,
        balance=balance, validation_issue=validation_issue,
    ))
    response.headers["Cache-Control"] = "private, no-store"
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
            # Preflight the current native revision before publishing anything.
            # In particular, an old 650k payment schedule must not be approved
            # for a revised 685k estimate.
            validate_estimate_delivery(doc, percent)
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
    payment_terms_issue = None
    if doc.get("status") in {"issued", "sent"}:
        try:
            validate_estimate_delivery(doc, 90)
        except BillingBridgeUnavailable as exc:
            payment_terms_issue = str(exc)
    total = Decimal(doc["total"])
    upfront_90 = (total * Decimal("0.90")).quantize(Decimal("0.01"))
    balance_10 = total - upfront_90
    response = render_template(
        "billing/advisor_estimate_send.html",
        car=owner_link.car, owner=owner, document=doc,
        sent_before=previously_submitted,
        upfront_90=upfront_90, balance_10=balance_10,
        payment_terms_issue=payment_terms_issue,
    )
    from flask import make_response
    result = make_response(response)
    result.headers["Cache-Control"] = "no-store"
    return result
