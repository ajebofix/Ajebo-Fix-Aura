"""Administrator-only operational status for the active Rina provider boundary."""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request
from flask_login import login_required

from admin.utils import advisor_required
from services.rina_provider_diagnostics import build_rina_provider_diagnostics


rina_operations_bp = Blueprint(
    "rina_operations",
    __name__,
    url_prefix="/admin/rina",
)


@rina_operations_bp.get("/provider-status")
@login_required
@advisor_required
def provider_status():
    """Show privacy-safe provider telemetry without making a provider call."""

    report = build_rina_provider_diagnostics(limit=10)

    if request.args.get("format") == "json":
        return jsonify(report), 200

    return render_template(
        "admin/rina_provider_status.html",
        report=report,
    )
