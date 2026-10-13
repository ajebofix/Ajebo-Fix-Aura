"""Public homepage and controlled private-access enquiry handling."""

from datetime import datetime
import os
import re

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy.exc import SQLAlchemyError

from admin.utils import advisor_required
from extensions import db
from services.email_delivery import build_email_idempotency_key, send_transactional_email

from .models import PrivateAccessRequest


public_site_bp = Blueprint("public_site", __name__)
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
REQUEST_TYPES = {"private_access", "assessment", "fleet"}
REQUEST_STATUSES = {"pending", "contacted", "closed"}


@public_site_bp.get("/")
def home():
    """Public marketing page; never expose private account or vehicle information."""
    return render_template("public/home.html")


@public_site_bp.post("/request-access")
def request_private_access():
    """Save an enquiry without creating an account or granting access."""
    # The honeypot absorbs most scripted form posts without storing their data.
    if request.form.get("website", "").strip():
        return redirect(url_for("public_site.home", submitted="1") + "#request-access", code=303)

    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    phone = request.form.get("phone", "").strip()
    vehicle = request.form.get("vehicle", "").strip()
    message = request.form.get("message", "").strip()
    request_type = request.form.get("request_type", "private_access").strip()
    consent = request.form.get("contact_consent") == "yes"

    valid = (
        2 <= len(name) <= 120
        and len(email) <= 254 and EMAIL_PATTERN.fullmatch(email) is not None
        and len(phone) <= 30 and len(vehicle) <= 120 and len(message) <= 1000
        and request_type in REQUEST_TYPES and consent
    )
    if not valid:
        return render_template(
            "public/home.html",
            enquiry_error="Please check your details and agree to be contacted about this enquiry.",
        ), 400

    enquiry = PrivateAccessRequest(
        name=name,
        email=email,
        phone=phone or None,
        vehicle=vehicle or None,
        message=message or None,
        request_type=request_type,
    )
    try:
        db.session.add(enquiry)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Could not persist a private-access enquiry")
        return render_template(
            "public/home.html",
            enquiry_error="We could not receive your request. Please try again later.",
        ), 503

    # Email is informational; an email-provider outage never loses the saved request.
    recipient = (
        current_app.config.get("AURA_ACCESS_REQUEST_NOTIFY_EMAIL")
        or os.getenv("AURA_ACCESS_REQUEST_NOTIFY_EMAIL")
        or "ajebofix@gmail.com"
    )
    try:
        result = send_transactional_email(
            to=recipient,
            subject="New Aura private access enquiry",
            text=(
                "A new private access enquiry is awaiting review in Aura.\n"
                f"Request reference: {enquiry.id}\n"
                "Open the advisor-only access requests page to review it.\n"
                "https://aura.ajebofix.com/admin/access-requests\n"
            ),
            idempotency_key=build_email_idempotency_key(
                "private-access-request", enquiry.id
            ),
        )
        if not result.success:
            current_app.logger.warning(
                "Private-access notification was not delivered",
                extra={"access_request_id": enquiry.id, "error": result.error_code},
            )
    except Exception:
        current_app.logger.exception(
            "Private-access notification failed",
            extra={"access_request_id": enquiry.id},
        )

    return redirect(url_for("public_site.home", submitted="1") + "#request-access", code=303)


@public_site_bp.get("/admin/access-requests")
@login_required
@advisor_required
def access_requests():
    status = request.args.get("status", "pending")
    if status not in REQUEST_STATUSES | {"all"}:
        status = "pending"
    query = PrivateAccessRequest.query
    if status != "all":
        query = query.filter_by(status=status)
    enquiries = query.order_by(
        PrivateAccessRequest.created_at.desc(), PrivateAccessRequest.id.desc()
    ).limit(100).all()
    return render_template(
        "public/access_requests.html", enquiries=enquiries, active_status=status
    )


@public_site_bp.post("/admin/access-requests/<int:request_id>/status")
@login_required
@advisor_required
def update_access_request_status(request_id):
    status = request.form.get("status", "")
    if status not in REQUEST_STATUSES:
        return "Invalid request status", 400
    enquiry = db.get_or_404(PrivateAccessRequest, request_id)
    enquiry.status = status
    enquiry.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Access request updated.", "success")
    return redirect(url_for("public_site.access_requests"), code=303)
