"""Owner onboarding routes for self-service completion and advisor assistance."""

from __future__ import annotations

from datetime import datetime, timedelta

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user
from sqlalchemy.exc import IntegrityError

from admin.utils import advisor_required
from auth.routes import password_is_acceptable
from extensions import db
from models import Car, CarOwnership, User
from security.email_verification import send_email_verification
from services.client_onboarding import (
    ClientOnboardingError,
    ClientOnboardingService,
)
from services.mileage_observations import (
    MileageObservationError,
    MileageObservationService,
)


owner_onboarding_bp = Blueprint("owner_onboarding", __name__)


def _activation_url(token: str) -> str:
    return url_for(
        "owner_onboarding.activate_account",
        token=token,
        _external=True,
        _scheme=current_app.config.get("PREFERRED_URL_SCHEME", "https"),
    )


@owner_onboarding_bp.route("/admin/clients/new", methods=["GET", "POST"])
@login_required
@advisor_required
def create_client():
    if request.method == "GET":
        return render_template("admin/create_client.html")

    try:
        user, invitation = ClientOnboardingService.create_client(
            name=request.form.get("name", ""),
            phone_number=request.form.get("phone_number", ""),
            email=request.form.get("email"),
            created_by_user_id=current_user.id,
        )
    except ClientOnboardingError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return render_template("admin/create_client.html"), 400
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Advisor-assisted client creation failed",
            extra={"advisor_user_id": current_user.id},
        )
        flash("Aura could not create the client account.", "error")
        return render_template("admin/create_client.html"), 500

    return render_template(
        "admin/client_activation.html",
        client=user,
        activation_link=_activation_url(invitation.token),
        invitation=invitation.invitation,
    )


@owner_onboarding_bp.post("/admin/clients/<int:user_id>/activation-link")
@login_required
@advisor_required
def regenerate_activation_link(user_id: int):
    user = db.session.get(User, user_id)
    if user is None or user.role != "user":
        abort(404)

    try:
        result = ClientOnboardingService.issue_invitation(
            user=user,
            created_by_user_id=current_user.id,
        )
    except ClientOnboardingError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("admin.admin_client_profile", user_id=user.id))

    flash("A fresh activation link has been created. Older unused links are revoked.", "success")
    return render_template(
        "admin/client_activation.html",
        client=user,
        activation_link=_activation_url(result.token),
        invitation=result.invitation,
    )


@owner_onboarding_bp.route(
    "/admin/clients/<int:user_id>/vehicles/new",
    methods=["GET", "POST"],
)
@login_required
@advisor_required
def add_client_vehicle(user_id: int):
    client = db.session.get(User, user_id)
    if client is None or client.role != "user":
        abort(404)

    if request.method == "GET":
        return render_template("admin/add_client_vehicle.html", client=client)

    brand = request.form.get("brand", "").strip()
    model = request.form.get("model", "").strip()
    year_raw = request.form.get("year", "").strip()
    vin = request.form.get("vin", "").strip().upper()
    plate_number = request.form.get("plate_number", "").strip() or None
    mileage_raw = request.form.get("mileage", "").strip()

    if not brand or not model or not year_raw or not vin:
        flash("Brand, model, year and VIN are required.", "error")
        return render_template("admin/add_client_vehicle.html", client=client), 400

    try:
        year = int(year_raw)
    except ValueError:
        flash("Enter a valid vehicle year.", "error")
        return render_template("admin/add_client_vehicle.html", client=client), 400

    mileage = None
    if mileage_raw:
        try:
            mileage = int(mileage_raw)
        except ValueError:
            flash("Odometer reading must be a whole number.", "error")
            return render_template("admin/add_client_vehicle.html", client=client), 400

    try:
        car = Car.query.filter_by(vin=vin).first()
        if car is not None:
            active_owner = CarOwnership.query.filter_by(
                car_id=car.id,
                is_active=True,
            ).first()
            if active_owner is not None and active_owner.user_id != client.id:
                flash(
                    "This VIN already belongs to another active owner. "
                    "Use the stewardship transfer workflow instead.",
                    "error",
                )
                return render_template("admin/add_client_vehicle.html", client=client), 409
            if active_owner is not None and active_owner.user_id == client.id:
                flash("This vehicle is already assigned to this client.", "info")
                return redirect(
                    url_for("admin.admin_client_profile", user_id=client.id)
                )
        else:
            car = Car(
                brand=brand,
                model=model,
                year=year,
                vin=vin,
                current_mileage=None,
            )
            db.session.add(car)
            db.session.flush()

        ownership = CarOwnership(
            user_id=client.id,
            car_id=car.id,
            plate_number=plate_number,
            mileage_at_transfer=mileage,
            start_date=datetime.utcnow(),
            is_active=True,
        )
        db.session.add(ownership)
        db.session.flush()

        if mileage is not None:
            MileageObservationService.record(
                car=car,
                odometer_km=mileage,
                source="advisor_observation",
                verification_status="advisor_verified",
                recorded_by_user_id=current_user.id,
                ownership_id=ownership.id,
                note="Advisor-verified odometer captured during assisted onboarding.",
                review_status="not_required",
                advance_current=True,
                commit=False,
            )

        db.session.commit()
    except MileageObservationError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return render_template("admin/add_client_vehicle.html", client=client), 400
    except IntegrityError:
        db.session.rollback()
        flash(
            "Aura could not assign this vehicle because one of its identifiers "
            "is already in active use.",
            "error",
        )
        return render_template("admin/add_client_vehicle.html", client=client), 409
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Advisor-assisted vehicle onboarding failed",
            extra={"advisor_user_id": current_user.id, "client_user_id": client.id},
        )
        flash("Aura could not add the vehicle.", "error")
        return render_template("admin/add_client_vehicle.html", client=client), 500

    flash("Vehicle added to the client account.", "success")
    return redirect(url_for("admin.admin_client_profile", user_id=client.id))


@owner_onboarding_bp.route("/auth/activate/<token>", methods=["GET", "POST"])
def activate_account(token: str):
    invitation = ClientOnboardingService.resolve_invitation(token)
    if invitation is None:
        flash("This activation link is invalid, expired, or already used.", "error")
        return redirect(url_for("auth.login"))

    user = invitation.user

    if current_user.is_authenticated and current_user.id != user.id:
        flash("Sign out of the current account before opening another invitation.", "error")
        return redirect(url_for("dashboard.aura_home"))

    if request.method == "GET":
        return render_template(
            "auth/activate_account.html",
            client=user,
            token=token,
            expires_at=invitation.expires_at,
        )

    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")

    if not password_is_acceptable(password):
        flash(
            "Password must be at least 10 characters and include letters and numbers.",
            "error",
        )
        return render_template(
            "auth/activate_account.html",
            client=user,
            token=token,
            expires_at=invitation.expires_at,
        ), 400

    if password != confirm_password:
        flash("Passwords do not match.", "error")
        return render_template(
            "auth/activate_account.html",
            client=user,
            token=token,
            expires_at=invitation.expires_at,
        ), 400

    try:
        user = ClientOnboardingService.accept_invitation(
            invitation=invitation,
            password=password,
        )
    except ClientOnboardingError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("auth.login"))

    session.clear()
    login_user(
        user,
        remember=True,
        duration=timedelta(days=30),
        fresh=True,
    )
    session["last_activity"] = datetime.utcnow().isoformat()
    session.permanent = True

    flash("Your private Aura access is ready. Finish your account setup.", "success")
    return redirect(url_for("owner_onboarding.account_setup"))


@owner_onboarding_bp.route("/account/setup", methods=["GET", "POST"])
@login_required
def account_setup():
    if current_user.role != "user":
        return redirect(url_for("profiles.profile"))

    if request.method == "POST":
        try:
            email_changed = ClientOnboardingService.update_basic_identity(
                user=current_user,
                name=request.form.get("name", ""),
                phone_number=request.form.get("phone_number", ""),
                email=request.form.get("email", ""),
            )
        except ClientOnboardingError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return render_template(
                "auth/account_setup.html",
                email_verified=current_user.email_verified_at is not None,
            ), 400

        should_send = email_changed or current_user.email_verified_at is None
        delivered = True
        if should_send:
            delivered = send_email_verification(current_user)

        if current_user.email_verified_at is not None:
            flash("Account details updated.", "success")
        elif delivered:
            flash(
                "Account details saved. We sent a verification link to your email.",
                "success",
            )
        else:
            flash(
                "Your details were saved, but Aura could not send the verification "
                "email right now. You can resend it from this page.",
                "error",
            )

        return redirect(url_for("owner_onboarding.account_setup"))

    return render_template(
        "auth/account_setup.html",
        email_verified=current_user.email_verified_at is not None,
    )
