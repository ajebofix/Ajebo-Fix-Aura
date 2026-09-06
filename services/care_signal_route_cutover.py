"""Runtime cutover for legacy advisor care-signal mutation routes.

The public URLs/endpoints stay stable while mutation authority moves from the
large legacy admin module into ``CareSignalLifecycleService``.
"""

from __future__ import annotations

from flask import flash, redirect, url_for
from flask_login import current_user

from admin.utils import advisor_required
from extensions import db
from models import VehicleHealthAlert
from services.care_signal_lifecycle import (
    CareSignalError,
    CareSignalLifecycleService,
    CareSignalScopeError,
    CareSignalStateError,
)


def _redirect_alert_center():
    return redirect(url_for("admin.admin_alert_center"))


@advisor_required
def acknowledge_alert_cutover(alert_id: int):
    if db.session.get(VehicleHealthAlert, alert_id) is None:
        from flask import abort

        abort(404)

    try:
        CareSignalLifecycleService.acknowledge(
            signal_id=alert_id,
            actor_user_id=current_user.id,
        )
        db.session.commit()
    except (CareSignalStateError, CareSignalScopeError) as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return _redirect_alert_center()
    except CareSignalError:
        db.session.rollback()
        raise
    except Exception:
        db.session.rollback()
        raise

    flash("Alert acknowledged", "success")
    return _redirect_alert_center()


@advisor_required
def resolve_alert_cutover(alert_id: int):
    if db.session.get(VehicleHealthAlert, alert_id) is None:
        from flask import abort

        abort(404)

    try:
        CareSignalLifecycleService.resolve(
            signal_id=alert_id,
            actor_type="user",
            actor_user_id=current_user.id,
            source_classification="advisor_resolution",
        )
        db.session.commit()
    except (CareSignalStateError, CareSignalScopeError) as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return _redirect_alert_center()
    except CareSignalError:
        db.session.rollback()
        raise
    except Exception:
        db.session.rollback()
        raise

    flash("Alert resolved", "success")
    return _redirect_alert_center()


def init_care_signal_route_cutover(app) -> None:
    """Replace only the two legacy mutation view functions after registration."""

    required = {
        "admin.acknowledge_alert": acknowledge_alert_cutover,
        "admin.resolve_alert": resolve_alert_cutover,
    }
    missing = [endpoint for endpoint in required if endpoint not in app.view_functions]
    if missing:
        raise RuntimeError(
            "Care-signal route cutover could not find legacy endpoints: "
            + ", ".join(sorted(missing))
        )

    app.view_functions.update(required)
