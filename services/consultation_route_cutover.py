"""Wave 2.2A2 consultation route cutover adapters.

This module is an incremental compatibility bridge: the existing Flask URL
rules remain stable, while their bound view functions are replaced at blueprint
registration time with thin adapters that delegate lifecycle legality and
canonical event emission to ``ConsultationLifecycleService``.

The legacy route bodies remain in place temporarily for a low-risk cutover, but
are no longer bound at runtime for the migrated consultation endpoints. A later
cleanup can delete those dead mutation blocks after production verification.
"""

from __future__ import annotations

from datetime import datetime

from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from admin.routes import CLINICAL_DISCLAIMER, admin_bp
from admin.utils import advisor_required
from cars.routes import cars_bp
from extensions import db
from models import BookingIntent, Car, CarOwnership, Consultation, VehicleAssessment
from services.care_pathways import has_priority_access
from services.consultation_lifecycle import (
    CONSULTATION_DEFERRED,
    CONSULTATION_IN_PROGRESS,
    CONSULTATION_REQUESTED,
    ConsultationLifecycleError,
    ConsultationLifecycleService,
)
from services.feature_gateways import FEATURE_PRIORITY_SCHEDULING, has_feature
from services.whatsapp import notify_admin_new_booking, send_booking_confirmation


def _safe_owner_phone() -> str:
    phone = (getattr(current_user, "phone_number", "") or "").strip().replace("+", "")
    if phone.startswith("0"):
        phone = "234" + phone[1:]
    return phone


def _owner_name() -> str:
    return (
        getattr(current_user, "first_name", None)
        or getattr(current_user, "name", None)
        or "there"
    )


def _parse_datetime(value: str | None, *, field_label: str) -> datetime | None:
    if not value:
        flash(f"{field_label} is required.", "error")
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        flash(f"Please provide a valid {field_label.lower()}.", "error")
        return None


@login_required
def book_consultation_cutover(car_id: int):
    """Owner consultation request adapter.

    The request, canonical event and BookingIntent completion commit together.
    WhatsApp delivery remains downstream of the durable transaction.
    """

    ownership = CarOwnership.query.filter_by(
        car_id=car_id,
        user_id=current_user.id,
        is_active=True,
    ).first_or_404()

    if request.method == "GET":
        existing_intent = BookingIntent.query.filter_by(
            user_id=current_user.id,
            car_id=car_id,
            completed=False,
        ).first()
        if not existing_intent:
            db.session.add(
                BookingIntent(
                    user_id=current_user.id,
                    car_id=car_id,
                    started_at=datetime.utcnow(),
                )
            )
            db.session.commit()

        return render_template(
            "cars/book_consultation.html",
            car=ownership.car,
            ownership=ownership,
        )

    preferred_raw = request.form.get("preferred_time")
    preferred_for = _parse_datetime(preferred_raw, field_label="Preferred time")
    if preferred_for is None:
        return redirect(url_for("cars.book_consultation", car_id=car_id))

    description = request.form.get("description", "").strip() or None

    try:
        ConsultationLifecycleService.request(
            car_id=car_id,
            actor_user_id=current_user.id,
            preferred_for=preferred_for,
            notes=description,
            source="cars.book_consultation",
        )

        intent = (
            BookingIntent.query.filter_by(
                user_id=current_user.id,
                car_id=car_id,
                completed=False,
            )
            .order_by(BookingIntent.started_at.desc())
            .first()
        )
        if intent:
            intent.completed = True

        db.session.commit()
    except ConsultationLifecycleError:
        db.session.rollback()
        current_app.logger.exception(
            "Owner consultation request rejected car_id=%s user_id=%s",
            car_id,
            current_user.id,
        )
        flash("Unable to record the consultation request.", "error")
        return redirect(url_for("cars.book_consultation", car_id=car_id))
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Owner consultation request failed car_id=%s user_id=%s",
            car_id,
            current_user.id,
        )
        flash("Unable to record the consultation request right now.", "error")
        return redirect(url_for("cars.book_consultation", car_id=car_id))

    # Communication is intentionally downstream of the committed domain fact.
    try:
        vehicle_name = f"{ownership.car.brand} {ownership.car.model}"
        send_booking_confirmation(
            phone=_safe_owner_phone(),
            name=_owner_name(),
            vehicle=vehicle_name,
        )
        notify_admin_new_booking(
            user=_owner_name(),
            vehicle=vehicle_name,
            time=preferred_raw,
        )
    except Exception:
        current_app.logger.exception(
            "Consultation request notification failed after commit car_id=%s consultation_owner_id=%s",
            car_id,
            current_user.id,
        )

    flash("Consultation requested", "success")
    return redirect(url_for("cars.car_detail", car_id=car_id))


@login_required
def request_priority_scheduling_cutover(car_id: int):
    """Record priority scheduling as an owner request, not a confirmed slot."""

    car = Car.query.get_or_404(car_id)
    ownership = CarOwnership.query.filter_by(
        car_id=car.id,
        user_id=current_user.id,
        is_active=True,
    ).first_or_404()

    if not has_feature(ownership, FEATURE_PRIORITY_SCHEDULING):
        from flask import abort

        abort(403)

    try:
        ConsultationLifecycleService.request(
            car_id=car.id,
            actor_user_id=current_user.id,
            preferred_for=datetime.utcnow(),
            notes="Priority scheduling request by client.",
            source="cars.priority_request",
        )
        db.session.commit()
    except ConsultationLifecycleError:
        db.session.rollback()
        current_app.logger.exception(
            "Priority consultation request rejected car_id=%s user_id=%s",
            car.id,
            current_user.id,
        )
        flash("Unable to record the priority scheduling request.", "error")
        return redirect(url_for("cars.car_detail", car_id=car.id))
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Priority consultation request failed car_id=%s user_id=%s",
            car.id,
            current_user.id,
        )
        flash("Unable to record the priority scheduling request right now.", "error")
        return redirect(url_for("cars.car_detail", car_id=car.id))

    flash(
        "Priority scheduling request received. An advisor will coordinate the next available session.",
        "success",
    )
    return redirect(url_for("cars.car_detail", car_id=car.id))


@login_required
@advisor_required
def admin_schedule_consultation_cutover(car_id: int):
    """Advisor-created schedule adapter for the existing vehicle entry point."""

    car = Car.query.get_or_404(car_id)
    ownership = CarOwnership.query.filter_by(
        car_id=car.id,
        is_active=True,
    ).first_or_404()

    if request.method == "POST":
        scheduled_for = _parse_datetime(
            request.form.get("scheduled_for"),
            field_label="Scheduled date",
        )
        if scheduled_for is None:
            return redirect(url_for("admin.admin_schedule_consultation", car_id=car.id))

        try:
            ConsultationLifecycleService.create_scheduled(
                car_id=car.id,
                actor_user_id=current_user.id,
                scheduled_for=scheduled_for,
                source="admin.direct_schedule",
            )
            db.session.commit()
        except ConsultationLifecycleError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return redirect(url_for("admin.admin_consultations"))
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "Advisor direct consultation scheduling failed car_id=%s advisor_id=%s",
                car.id,
                current_user.id,
            )
            flash("Failed to schedule consultation due to a system error.", "error")
            return redirect(url_for("admin.admin_consultations"))

        flash("Consultation scheduled.", "success")
        return redirect(url_for("admin.admin_consultations"))

    return render_template(
        "admin/schedule_consultation.html",
        car=car,
        ownership=ownership,
        consultation=None,
    )


@admin_bp.route(
    "/consultations/<int:consultation_id>/schedule-request",
    methods=["GET", "POST"],
)
@login_required
@advisor_required
def admin_schedule_requested_consultation(consultation_id: int):
    """Accept a durable owner request and confirm its appointment time."""

    consultation = Consultation.query.get_or_404(consultation_id)
    if consultation.status not in {CONSULTATION_REQUESTED, CONSULTATION_DEFERRED}:
        flash("This consultation request is no longer awaiting scheduling.", "info")
        return redirect(url_for("admin.admin_consultations"))

    car = Car.query.get_or_404(consultation.car_id)
    ownership = CarOwnership.query.filter_by(
        id=consultation.ownership_id,
        car_id=car.id,
        is_active=True,
    ).first_or_404()

    if request.method == "POST":
        scheduled_for = _parse_datetime(
            request.form.get("scheduled_for"),
            field_label="Scheduled date",
        )
        if scheduled_for is None:
            return redirect(
                url_for(
                    "admin.admin_schedule_requested_consultation",
                    consultation_id=consultation.id,
                )
            )

        try:
            ConsultationLifecycleService.schedule(
                consultation_id=consultation.id,
                actor_user_id=current_user.id,
                scheduled_for=scheduled_for,
                source="admin.request_schedule",
            )
            db.session.commit()
        except ConsultationLifecycleError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return redirect(url_for("admin.admin_consultations"))
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "Requested consultation scheduling failed consultation_id=%s advisor_id=%s",
                consultation.id,
                current_user.id,
            )
            flash("Failed to confirm the consultation schedule.", "error")
            return redirect(url_for("admin.admin_consultations"))

        flash("Consultation request accepted and scheduled.", "success")
        return redirect(url_for("admin.admin_consultations"))

    return render_template(
        "admin/schedule_consultation.html",
        car=car,
        ownership=ownership,
        consultation=consultation,
    )


@login_required
@advisor_required
def admin_start_consultation_cutover(consultation_id: int):
    try:
        ConsultationLifecycleService.start(
            consultation_id=consultation_id,
            actor_user_id=current_user.id,
            source="admin.start_consultation",
        )
        db.session.commit()
    except ConsultationLifecycleError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("admin.admin_consultations"))
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Consultation start failed consultation_id=%s advisor_id=%s",
            consultation_id,
            current_user.id,
        )
        flash("Failed to start consultation due to a system error.", "error")
        return redirect(url_for("admin.admin_consultations"))

    flash("Consultation is now in progress.", "success")
    return redirect(url_for("admin.admin_consultations"))


@login_required
@advisor_required
def admin_complete_consultation_cutover(consultation_id: int):
    consultation = Consultation.query.get_or_404(consultation_id)

    if consultation.status != CONSULTATION_IN_PROGRESS:
        flash("Only active consultations can be completed.", "error")
        return redirect(url_for("admin.admin_consultations"))

    assessment = VehicleAssessment.query.filter_by(
        consultation_id=consultation.id
    ).first()

    if request.method == "GET":
        return render_template(
            "admin/complete_consultation.html",
            consultation=consultation,
            assessment=assessment,
        )

    try:
        ConsultationLifecycleService.complete(
            consultation_id=consultation.id,
            actor_user_id=current_user.id,
            summary=request.form.get("summary", ""),
            client_visible_summary=request.form.get("client_visible_summary", ""),
            source="admin.complete_consultation",
        )
        db.session.commit()
    except ConsultationLifecycleError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("admin.admin_consultations"))
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Consultation completion failed consultation_id=%s advisor_id=%s",
            consultation.id,
            current_user.id,
        )
        flash("Failed to complete consultation due to a system error.", "error")
        return redirect(url_for("admin.admin_consultations"))

    flash("Consultation completed.", "success")
    return redirect(url_for("admin.view_vehicle", car_id=consultation.car_id))


@login_required
@advisor_required
def admin_consultations_cutover():
    """Render explicit requested/scheduled/in-progress/completed queue buckets."""

    consultations = Consultation.query.order_by(Consultation.scheduled_for.asc()).all()
    grouped = {
        "requested": [],
        "scheduled": [],
        "in_progress": [],
        "completed": [],
        "other": [],
    }

    for consultation in consultations:
        bucket = consultation.status if consultation.status in grouped else "other"
        grouped[bucket].append(consultation)

    return render_template(
        "admin/consultations.html",
        grouped=grouped,
        disclaimer=CLINICAL_DISCLAIMER,
        now=datetime.utcnow(),
    )


@admin_bp.record_once
def install_consultation_route_cutover(state):
    """Swap only the migrated URL endpoints after admin blueprint registration."""

    replacements = {
        "cars.book_consultation": book_consultation_cutover,
        "cars.request_priority_scheduling": request_priority_scheduling_cutover,
        "admin.admin_schedule_consultation": admin_schedule_consultation_cutover,
        "admin.admin_start_consultation": admin_start_consultation_cutover,
        "admin.admin_complete_consultation": admin_complete_consultation_cutover,
        "admin.admin_consultations": admin_consultations_cutover,
    }

    missing = [
        endpoint for endpoint in replacements if endpoint not in state.app.view_functions
    ]
    if missing:
        raise RuntimeError(
            "Consultation route cutover could not find endpoints: " + ", ".join(missing)
        )

    state.app.view_functions.update(replacements)
