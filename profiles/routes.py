"""Authenticated client profile and privacy-centre routes."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from extensions import db
from models import CarDriver, CarOwnership
from profiles.models import (
    COMMUNICATION_VALUES,
    GENDER_VALUES,
    ClientProfile,
    ProfileAuditEvent,
)
from security.field_encryption import ProfileEncryptionError


profiles_bp = Blueprint("profiles", __name__, url_prefix="/profile")


ALLOWED_COUNTRIES = (
    "Nigeria",
    "Ghana",
    "United Kingdom",
    "United States",
    "Canada",
    "United Arab Emirates",
    "South Africa",
)

ALLOWED_TIMEZONES = (
    "Africa/Lagos",
    "Africa/Accra",
    "Europe/London",
    "America/New_York",
    "America/Los_Angeles",
    "Asia/Dubai",
    "Africa/Johannesburg",
)

AUDITABLE_FIELDS = {
    "name",
    "occupation",
    "organisation",
    "gender",
    "city",
    "state_region",
    "country",
    "home_address",
    "office_address",
    "preferred_communication",
    "preferred_communication_time",
    "care_preference",
    "preferred_language",
    "timezone",
    "emergency_contact_name",
    "emergency_contact_phone",
    "marketing_consent",
}

CARE_PLAN_LABELS = {
    "standard": "Standard Access",
    "standard_access": "Standard Access",
    "active_monitoring": "Active Monitoring",
    "preventive": "Preventive Coverage",
    "preventive_coverage": "Preventive Coverage",
    "priority": "Priority Access",
    "priority_access": "Priority Access",
}

CARE_PLAN_RANKS = {
    "standard": 1,
    "standard_access": 1,
    "active_monitoring": 2,
    "preventive": 3,
    "preventive_coverage": 3,
    "priority": 4,
    "priority_access": 4,
}


class ProfileValidationError(ValueError):
    """Raised when submitted profile information is not acceptable."""


def _request_id() -> str:
    supplied = request.headers.get("X-Request-ID", "").strip()
    if supplied and re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", supplied):
        return supplied
    return uuid.uuid4().hex


def _submitted_audit_fields() -> list[str]:
    return sorted(AUDITABLE_FIELDS.intersection(request.form.keys()))


def _record_audit(
    *,
    action: str,
    changed_fields: Iterable[str],
    success: bool,
    reason_code: str | None = None,
) -> None:
    event = ProfileAuditEvent(
        user_id=current_user.id,
        action=action,
        changed_fields=sorted(set(changed_fields)),
        request_id=_request_id(),
        success=success,
        reason_code=reason_code,
    )
    db.session.add(event)


def _clean_optional(value: str | None, *, maximum: int, label: str) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > maximum:
        raise ProfileValidationError(f"{label} must be {maximum} characters or fewer.")
    return cleaned


def _normalise_phone(value: str | None, *, label: str) -> str | None:
    cleaned = _clean_optional(value, maximum=30, label=label)
    if cleaned is None:
        return None

    normalised = re.sub(r"[\s().-]", "", cleaned)
    if not re.fullmatch(r"\+?\d{7,20}", normalised):
        raise ProfileValidationError(
            f"{label} must contain 7 to 20 digits and may begin with +."
        )
    return normalised


def _validated_form() -> dict[str, object]:
    name = request.form.get("name", "").strip()
    if not name:
        raise ProfileValidationError("Full name is required.")
    if len(name) > 120:
        raise ProfileValidationError("Full name must be 120 characters or fewer.")

    gender = _clean_optional(request.form.get("gender"), maximum=30, label="Gender")
    if gender and gender not in GENDER_VALUES:
        raise ProfileValidationError("Select a valid gender option.")

    communication = _clean_optional(
        request.form.get("preferred_communication"),
        maximum=30,
        label="Preferred communication",
    )
    if communication and communication not in COMMUNICATION_VALUES:
        raise ProfileValidationError("Select a valid communication preference.")

    country = (
        _clean_optional(request.form.get("country"), maximum=120, label="Country")
        or "Nigeria"
    )
    if country not in ALLOWED_COUNTRIES:
        raise ProfileValidationError("Select a supported country.")

    timezone = (
        _clean_optional(request.form.get("timezone"), maximum=80, label="Time zone")
        or "Africa/Lagos"
    )
    if timezone not in ALLOWED_TIMEZONES:
        raise ProfileValidationError("Select a supported time zone.")

    return {
        "name": name,
        "occupation": _clean_optional(
            request.form.get("occupation"), maximum=120, label="Occupation"
        ),
        "organisation": _clean_optional(
            request.form.get("organisation"), maximum=120, label="Organisation"
        ),
        "gender": gender,
        "city": _clean_optional(request.form.get("city"), maximum=120, label="City"),
        "state_region": _clean_optional(
            request.form.get("state_region"), maximum=120, label="State or region"
        ),
        "country": country,
        "home_address": _clean_optional(
            request.form.get("home_address"), maximum=500, label="Home address"
        ),
        "office_address": _clean_optional(
            request.form.get("office_address"), maximum=500, label="Office address"
        ),
        "preferred_communication": communication,
        "preferred_communication_time": _clean_optional(
            request.form.get("preferred_communication_time"),
            maximum=120,
            label="Preferred communication time",
        ),
        "care_preference": _clean_optional(
            request.form.get("care_preference"),
            maximum=1000,
            label="Care preference",
        ),
        "preferred_language": _clean_optional(
            request.form.get("preferred_language"),
            maximum=80,
            label="Preferred language",
        ),
        "timezone": timezone,
        "emergency_contact_name": _clean_optional(
            request.form.get("emergency_contact_name"),
            maximum=120,
            label="Emergency contact name",
        ),
        "emergency_contact_phone": _normalise_phone(
            request.form.get("emergency_contact_phone"),
            label="Emergency contact phone",
        ),
        "marketing_consent": request.form.get("marketing_consent") == "on",
    }


def _protected_values(profile: ClientProfile | None) -> tuple[dict[str, str], bool]:
    empty = {
        "home_address": "",
        "office_address": "",
        "emergency_contact_name": "",
        "emergency_contact_phone": "",
    }
    if profile is None:
        return empty, True

    try:
        return {
            "home_address": profile.home_address or "",
            "office_address": profile.office_address or "",
            "emergency_contact_name": profile.emergency_contact_name or "",
            "emergency_contact_phone": profile.emergency_contact_phone or "",
        }, True
    except ProfileEncryptionError:
        current_app.logger.exception(
            "Protected client profile fields could not be decrypted",
            extra={"user_id": current_user.id},
        )
        return empty, False


def _active_ownerships() -> list[CarOwnership]:
    return (
        CarOwnership.query.filter_by(user_id=current_user.id, is_active=True)
        .order_by(CarOwnership.start_date.asc())
        .all()
    )


def _assigned_driver_count(ownerships: list[CarOwnership]) -> int:
    car_ids = [ownership.car_id for ownership in ownerships]
    if not car_ids:
        return 0

    return (
        db.session.query(CarDriver.user_id)
        .filter(
            CarDriver.car_id.in_(car_ids),
            CarDriver.is_active.is_(True),
        )
        .distinct()
        .count()
    )


def _access_tier(ownerships: list[CarOwnership]) -> str:
    if any(bool(ownership.priority_access) for ownership in ownerships):
        return "Priority Access"

    plans = [
        (ownership.care_plan or "standard_access").strip().lower()
        for ownership in ownerships
    ]
    if not plans:
        return "Standard Access"

    best = max(plans, key=lambda plan: CARE_PLAN_RANKS.get(plan, 1))
    return CARE_PLAN_LABELS.get(best, best.replace("_", " ").title())


def _client_type() -> str:
    if current_user.role == "admin":
        return "Advisor"
    if current_user.role == "driver":
        return "Driver"
    return "Private Client"


def _initials() -> str:
    parts = [part for part in (current_user.name or "").split() if part]
    if not parts:
        return "AU"
    return "".join(part[0] for part in parts[:2]).upper()


def _profile_completeness(profile: ClientProfile | None) -> int:
    if profile is None:
        return 10 if current_user.name else 0

    checks = (
        bool(current_user.name),
        bool(profile.occupation),
        bool(profile.city),
        bool(profile.country),
        bool(profile.preferred_communication),
        bool(profile.care_preference),
        bool(profile.timezone),
        bool(profile.home_address_ciphertext or profile.office_address_ciphertext),
        bool(profile.emergency_contact_name_ciphertext),
        bool(profile.emergency_contact_phone_ciphertext),
    )
    return round((sum(checks) / len(checks)) * 100)


def _template_context(profile: ClientProfile | None) -> dict[str, object]:
    protected, protected_data_available = _protected_values(profile)
    ownerships = _active_ownerships()

    values = {
        "name": current_user.name or "",
        "email": current_user.email,
        "phone_number": current_user.phone_number,
        "occupation": profile.occupation if profile else "",
        "organisation": profile.organisation if profile else "",
        "gender": profile.gender if profile else "",
        "city": profile.city if profile else "",
        "state_region": profile.state_region if profile else "",
        "country": profile.country if profile else "Nigeria",
        "preferred_communication": profile.preferred_communication if profile else "",
        "preferred_communication_time": (
            profile.preferred_communication_time if profile else ""
        ),
        "care_preference": profile.care_preference if profile else "",
        "preferred_language": profile.preferred_language if profile else "",
        "timezone": profile.timezone if profile else "Africa/Lagos",
        "marketing_consent": bool(profile.marketing_consent) if profile else False,
        **protected,
    }

    return {
        "profile": profile,
        "values": values,
        "initials": _initials(),
        "profile_completeness": _profile_completeness(profile),
        "vehicle_count": len(ownerships),
        "driver_count": _assigned_driver_count(ownerships),
        "access_tier": _access_tier(ownerships),
        "client_type": _client_type(),
        "email_verified": getattr(current_user, "email_verified_at", None) is not None,
        "protected_data_available": protected_data_available,
        "gender_values": GENDER_VALUES,
        "communication_values": COMMUNICATION_VALUES,
        "countries": ALLOWED_COUNTRIES,
        "timezones": ALLOWED_TIMEZONES,
    }


def _changed_fields(profile: ClientProfile | None, cleaned: dict[str, object]) -> list[str]:
    changed: list[str] = []
    protected, _ = _protected_values(profile)

    if (current_user.name or "") != cleaned["name"]:
        changed.append("name")

    ordinary_fields = (
        "occupation",
        "organisation",
        "gender",
        "city",
        "state_region",
        "country",
        "preferred_communication",
        "preferred_communication_time",
        "care_preference",
        "preferred_language",
        "timezone",
        "marketing_consent",
    )
    for field in ordinary_fields:
        existing = getattr(profile, field, None) if profile else None
        if existing != cleaned[field]:
            changed.append(field)

    for field in (
        "home_address",
        "office_address",
        "emergency_contact_name",
        "emergency_contact_phone",
    ):
        if protected.get(field, "") != (cleaned[field] or ""):
            changed.append(field)

    return changed


def _apply_profile(profile: ClientProfile, cleaned: dict[str, object]) -> None:
    current_user.name = str(cleaned["name"])

    for field in (
        "occupation",
        "organisation",
        "gender",
        "city",
        "state_region",
        "country",
        "preferred_communication",
        "preferred_communication_time",
        "care_preference",
        "preferred_language",
        "timezone",
        "marketing_consent",
    ):
        setattr(profile, field, cleaned[field])

    profile.home_address = cleaned["home_address"]  # type: ignore[arg-type]
    profile.office_address = cleaned["office_address"]  # type: ignore[arg-type]
    profile.emergency_contact_name = cleaned["emergency_contact_name"]  # type: ignore[arg-type]
    profile.emergency_contact_phone = cleaned["emergency_contact_phone"]  # type: ignore[arg-type]


@profiles_bp.get("/")
@login_required
def profile():
    return render_template(
        "profiles/profile.html",
        **_template_context(current_user.client_profile),
    )


@profiles_bp.route("/edit", methods=["GET", "POST"])
@login_required
def edit_profile():
    profile_record = current_user.client_profile

    if request.method == "GET":
        return render_template(
            "profiles/edit.html",
            **_template_context(profile_record),
        )

    if getattr(current_user, "email_verified_at", None) is None:
        flash(
            "Please verify your email address before changing protected profile information.",
            "info",
        )
        return redirect(url_for("email_verification.verification_required"))

    try:
        cleaned = _validated_form()
    except ProfileValidationError as exc:
        _record_audit(
            action="profile_update_rejected",
            changed_fields=_submitted_audit_fields(),
            success=False,
            reason_code="validation_failed",
        )
        db.session.commit()
        flash(str(exc), "error")
        return (
            render_template(
                "profiles/edit.html",
                **_template_context(profile_record),
            ),
            400,
        )

    changed = _changed_fields(profile_record, cleaned)
    if not changed:
        flash("Your profile is already up to date.", "info")
        return redirect(url_for("profiles.profile"))

    is_new = profile_record is None
    if profile_record is None:
        profile_record = ClientProfile(user_id=current_user.id)
        db.session.add(profile_record)

    try:
        _apply_profile(profile_record, cleaned)
        _record_audit(
            action="profile_created" if is_new else "profile_updated",
            changed_fields=changed,
            success=True,
        )
        db.session.commit()
    except ProfileEncryptionError:
        db.session.rollback()
        current_app.logger.exception(
            "Client profile encryption was unavailable",
            extra={"user_id": current_user.id},
        )
        _record_audit(
            action="profile_update_rejected",
            changed_fields=changed,
            success=False,
            reason_code="encryption_unavailable",
        )
        db.session.commit()
        flash(
            "Aura could not securely save the protected profile fields. "
            "No unencrypted address or emergency-contact information was stored.",
            "error",
        )
        return (
            render_template(
                "profiles/edit.html",
                **_template_context(current_user.client_profile),
            ),
            503,
        )
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Client profile update failed",
            extra={"user_id": current_user.id},
        )
        flash("Your profile could not be updated. Please try again.", "error")
        return redirect(url_for("profiles.edit_profile"))

    flash("Your Aura profile has been updated securely.", "success")
    return redirect(url_for("profiles.profile"))


@profiles_bp.get("/privacy")
@login_required
def privacy():
    return render_template("profiles/privacy.html")
