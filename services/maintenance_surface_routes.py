"""Role-bounded Maintenance Intelligence read surfaces for M5."""

from __future__ import annotations

from flask import render_template
from flask_login import login_required, current_user

from admin.routes import admin_bp
from admin.utils import advisor_required
from cars.routes import cars_bp
from maintenance.presentation import MaintenancePresentationService
from models import Car, CarOwnership


@cars_bp.get("/<int:car_id>/maintenance", endpoint="maintenance_intelligence")
@login_required
def client_maintenance_intelligence(car_id: int):
    ownership = CarOwnership.query.filter_by(
        car_id=car_id,
        user_id=current_user.id,
        is_active=True,
    ).first_or_404()
    car = ownership.car
    return render_template(
        "maintenance/vehicle.html",
        car=car,
        ownership=ownership,
        is_admin_view=False,
        maintenance=MaintenancePresentationService.owner_view(car),
    )


@admin_bp.get("/cars/<int:car_id>/maintenance", endpoint="maintenance_intelligence")
@login_required
@advisor_required
def advisor_maintenance_intelligence(car_id: int):
    car = Car.query.get_or_404(car_id)
    ownership = CarOwnership.query.filter_by(car_id=car.id, is_active=True).first()
    return render_template(
        "maintenance/vehicle.html",
        car=car,
        ownership=ownership,
        is_admin_view=True,
        maintenance=MaintenancePresentationService.advisor_view(car),
    )
