"""Compatibility cutover for service-history odometer and monitoring semantics.

A service record stores the vehicle's main-odometer reading at the time the
service occurred. That historical reading may legitimately be below the
vehicle's present odometer and must never roll ``Car.current_mileage`` back.

Advisor service entry is split into two explicit modes:
- ``historical`` documents prior service evidence and does not require an active
  consultation;
- ``current`` records care being performed/managed now and remains gated by an
  active consultation.

Historical backfill remains advisor-authorized, preserves provenance/verification
metadata, and still refreshes deterministic care signals because new evidence can
change the vehicle's monitoring state.
"""

from __future__ import annotations

from datetime import datetime
import hashlib

from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from admin.routes import CLINICAL_DISCLAIMER, admin_bp
from admin.utils import advisor_required
from cars.routes import create_service_event
from extensions import db
from models import Car, CarOwnership, VehicleEvent
from services.consultation_guard import require_active_consultation
from services.health_alert_service import CareSignalService
from services.mileage_observations import MileageObservationService


_ALLOWED_RECORD_MODES = {"historical", "current"}
_ALLOWED_INFORMATION_SOURCES = {
    "client_provided": "Client-provided history",
    "invoice_receipt": "Service invoice / receipt",
    "workshop_record": "Workshop / dealer record",
    "service_book": "Service booklet / log",
    "other": "Other documented source",
}
_ALLOWED_VERIFICATION_STATUSES = {
    "unverified": "Unverified",
    "document_reviewed": "Supporting document reviewed",
    "advisor_confirmed": "Advisor-confirmed from available evidence",
}


def _service_fingerprint(
    *, car_id: int, ownership_id: int, service_type: str, mileage: int, service_date: str
) -> str:
    return hashlib.sha256(
        f"{car_id}|{ownership_id}|{service_type}|{mileage}|{service_date}".encode()
    ).hexdigest()


def _attach_service_metadata(
    *,
    car_id: int,
    ownership_id: int,
    service_type: str,
    mileage: int,
    service_date: str,
    metadata: dict | None,
) -> None:
    if not metadata:
        return

    event = VehicleEvent.query.filter_by(
        fingerprint=_service_fingerprint(
            car_id=car_id,
            ownership_id=ownership_id,
            service_type=service_type,
            mileage=mileage,
            service_date=service_date,
        )
    ).first()
    if event is None:
        raise RuntimeError("Saved service event could not be reloaded for audit metadata.")

    event.data = {**(event.data or {}), **metadata}
    db.session.commit()


def _record_service_with_monitoring(
    *,
    car,
    ownership,
    service_type: str,
    mileage: int,
    description: str,
    service_date: str,
    performed_by: int,
    source: str,
    event_metadata: dict | None = None,
) -> bool:
    """Persist a service record, mileage snapshot, audit metadata and care signals."""

    # New current-service routes explicitly identify themselves. Validate the
    # odometer before the legacy helper commits the VehicleEvent so an invalid
    # current reading cannot leave a partially saved service record behind.
    if (
        (event_metadata or {}).get("record_mode") == "current"
        and car.current_mileage is not None
        and mileage < car.current_mileage
    ):
        raise ValueError(
            "Current service odometer cannot be lower than the latest recorded odometer."
        )

    create_service_event(
        car=car,
        ownership=ownership,
        service_type=service_type,
        mileage=mileage,
        description=description,
        service_date=service_date,
        performed_by=performed_by,
        source=source,
    )

    _attach_service_metadata(
        car_id=car.id,
        ownership_id=ownership.id,
        service_type=service_type,
        mileage=mileage,
        service_date=service_date,
        metadata=event_metadata,
    )

    MileageObservationService.record_service_snapshot(
        car=car,
        ownership=ownership,
        odometer_km=mileage,
        service_date=service_date,
        performed_by=performed_by,
        event_metadata=event_metadata,
        source_reference=_service_fingerprint(
            car_id=car.id,
            ownership_id=ownership.id,
            service_type=service_type,
            mileage=mileage,
            service_date=service_date,
        ),
        entry_source=source,
    )

    try:
        CareSignalService.evaluate(car.id, trigger="event_created")
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Service record %s saved but care-signal refresh failed",
            car.id,
        )
        return False

    return True


def _resolve_record_mode() -> str:
    mode = (request.form.get("record_mode") or request.args.get("mode") or "current").strip()
    if mode not in _ALLOWED_RECORD_MODES:
        raise ValueError("Invalid service record mode.")
    return mode


def _validate_historical_record(*, car: Car, mileage: int, service_date: str) -> None:
    try:
        occurred_on = datetime.fromisoformat(service_date).date()
    except ValueError as exc:
        raise ValueError("Historical service date is invalid.") from exc

    if occurred_on > datetime.utcnow().date():
        raise ValueError("Historical service date cannot be in the future.")

    if car.current_mileage is not None and mileage > car.current_mileage:
        raise ValueError(
            "Historical service mileage cannot exceed the vehicle's current odometer."
        )


@login_required
@advisor_required
def admin_add_service_cutover(car_id: int):
    car = Car.query.get_or_404(car_id)
    ownership = CarOwnership.query.filter_by(
        car_id=car.id,
        is_active=True,
    ).first_or_404()

    try:
        record_mode = _resolve_record_mode()
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin.admin_vehicle_records", car_id=car.id))

    # Current Ajebo Fix-managed service remains consultation-first.
    # Historical evidence ingestion is recordkeeping, not a new care encounter.
    if record_mode == "current":
        try:
            require_active_consultation(car_id)
        except PermissionError as exc:
            flash(str(exc), "error")
            return redirect(url_for("admin.view_vehicle", car_id=car.id))

    if request.method == "POST":
        service_type = request.form.get("service_type", "").strip()
        mileage = request.form.get("mileage", type=int)
        description = request.form.get("description", "").strip()
        service_date = request.form.get("service_date", "").strip()

        if not service_type or mileage is None or not service_date:
            flash("All required fields must be completed.", "error")
            return redirect(request.referrer or request.url)

        information_source = None
        verification_status = None

        if record_mode == "historical":
            information_source = request.form.get("information_source", "").strip()
            verification_status = request.form.get("verification_status", "").strip()

            if information_source not in _ALLOWED_INFORMATION_SOURCES:
                flash("Select a valid source of information.", "error")
                return redirect(request.referrer or request.url)
            if verification_status not in _ALLOWED_VERIFICATION_STATUSES:
                flash("Select a valid verification status.", "error")
                return redirect(request.referrer or request.url)

            try:
                _validate_historical_record(
                    car=car,
                    mileage=mileage,
                    service_date=service_date,
                )
            except ValueError as exc:
                flash(str(exc), "error")
                return redirect(request.referrer or request.url)

        event_metadata = {
            "record_mode": record_mode,
            "entered_by_role": "advisor",
            "entered_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        if record_mode == "historical":
            event_metadata.update(
                {
                    "information_source": information_source,
                    "information_source_label": _ALLOWED_INFORMATION_SOURCES[
                        information_source
                    ],
                    "verification_status": verification_status,
                    "verification_status_label": _ALLOWED_VERIFICATION_STATUSES[
                        verification_status
                    ],
                }
            )

        try:
            monitoring_refreshed = _record_service_with_monitoring(
                car=car,
                ownership=ownership,
                service_type=service_type,
                mileage=mileage,
                description=description,
                service_date=service_date,
                performed_by=current_user.id,
                source=(
                    "admin_historical" if record_mode == "historical" else "admin_current"
                ),
                event_metadata=event_metadata,
            )
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return redirect(request.referrer or request.url)
        except Exception:
            db.session.rollback()
            raise

        if record_mode == "historical":
            flash("Historical service record added.", "success")
        else:
            flash("Current service record added.", "success")

        if not monitoring_refreshed:
            flash(
                "The service was saved, but monitoring signals could not be refreshed. "
                "Please retry the monitoring review.",
                "warning",
            )
        return redirect(url_for("admin.admin_vehicle_records", car_id=car.id))

    return render_template(
        "admin/add_service.html",
        car=car,
        ownership=ownership,
        record_mode=record_mode,
        information_sources=_ALLOWED_INFORMATION_SOURCES,
        verification_statuses=_ALLOWED_VERIFICATION_STATUSES,
        disclaimer=CLINICAL_DISCLAIMER,
    )


@login_required
def client_add_service_cutover(ownership_id: int):
    ownership = CarOwnership.query.filter_by(
        id=ownership_id,
        user_id=current_user.id,
        is_active=True,
    ).first_or_404()
    car = ownership.car

    try:
        require_active_consultation(car.id)
    except PermissionError as exc:
        flash(str(exc), "error")
        return redirect(url_for("cars.car_detail", car_id=car.id))

    if request.method == "POST":
        try:
            monitoring_refreshed = _record_service_with_monitoring(
                car=car,
                ownership=ownership,
                service_type=request.form["service_type"].strip(),
                mileage=int(request.form["mileage"]),
                description=request.form.get("description", "").strip(),
                service_date=request.form["service_date"].strip(),
                performed_by=current_user.id,
                source="client",
                event_metadata={
                    "record_mode": "current",
                    "entered_by_role": "client",
                    "entered_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                },
            )
        except (KeyError, TypeError, ValueError) as exc:
            db.session.rollback()
            flash(str(exc) or "Please complete the service details.", "error")
            return redirect(request.referrer or request.url)
        except Exception:
            db.session.rollback()
            raise

        flash("Service record saved.", "success")
        if not monitoring_refreshed:
            flash(
                "The service was saved, but monitoring is still being reviewed.",
                "warning",
            )
        return redirect(url_for("cars.car_detail", car_id=car.id))

    return render_template("cars/add_service.html", car=car, ownership=ownership)


@admin_bp.record_once
def install_service_history_route_cutover(state):
    replacements = {
        "admin.admin_add_service": admin_add_service_cutover,
        "cars.add_service_record": client_add_service_cutover,
    }

    missing = [
        endpoint for endpoint in replacements if endpoint not in state.app.view_functions
    ]
    if missing:
        raise RuntimeError(
            "Service-history cutover could not find endpoint(s): " + ", ".join(missing)
        )

    for endpoint, view_func in replacements.items():
        state.app.view_functions[endpoint] = view_func
