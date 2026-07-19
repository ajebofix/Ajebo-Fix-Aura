"""Persistent session registry and server-side revocation for Aura."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, Flask, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, logout_user, user_logged_in, user_logged_out

from extensions import db


class UserSession(db.Model):
    __tablename__ = "user_sessions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    password_fingerprint = db.Column(db.String(64), nullable=False)
    device_label = db.Column(db.String(120), nullable=False)
    user_agent = db.Column(db.String(255), nullable=True)
    ip_hash = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    revoked_at = db.Column(db.DateTime, nullable=True, index=True)
    revoked_reason = db.Column(db.String(80), nullable=True)

    user = db.relationship("User")

    @property
    def is_current(self) -> bool:
        return session.get("session_token_hash") == self.token_hash

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None and self.expires_at > datetime.utcnow()


session_registry_bp = Blueprint(
    "session_registry",
    __name__,
    url_prefix="/auth/sessions",
)


def _token_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _password_fingerprint(password_hash: str) -> str:
    secret = str(current_app.config["SECRET_KEY"]).encode("utf-8")
    return hmac.new(secret, password_hash.encode("utf-8"), hashlib.sha256).hexdigest()


def _ip_hash() -> str | None:
    address = request.remote_addr
    if not address:
        return None
    secret = str(current_app.config["SECRET_KEY"]).encode("utf-8")
    return hmac.new(secret, address.encode("utf-8"), hashlib.sha256).hexdigest()


def _device_label(user_agent: str) -> str:
    value = user_agent.lower()

    if "iphone" in value:
        device = "iPhone"
    elif "ipad" in value:
        device = "iPad"
    elif "android" in value:
        device = "Android device"
    elif "windows" in value:
        device = "Windows device"
    elif "macintosh" in value or "mac os" in value:
        device = "Mac"
    elif "linux" in value:
        device = "Linux device"
    else:
        device = "Unknown device"

    if "chrome" in value and "edg" not in value:
        browser = "Chrome"
    elif "safari" in value and "chrome" not in value:
        browser = "Safari"
    elif "firefox" in value:
        browser = "Firefox"
    elif "edg" in value:
        browser = "Edge"
    else:
        browser = "browser"

    return f"{device} · {browser}"


def _create_session_record(user, remember: bool) -> None:
    raw_token = secrets.token_urlsafe(32)
    token_hash = _token_hash(raw_token)
    now = datetime.utcnow()
    lifetime = timedelta(days=30) if remember else current_app.permanent_session_lifetime
    user_agent = request.headers.get("User-Agent", "")[:255]

    record = UserSession(
        user_id=user.id,
        token_hash=token_hash,
        password_fingerprint=_password_fingerprint(user.password_hash),
        device_label=_device_label(user_agent),
        user_agent=user_agent or None,
        ip_hash=_ip_hash(),
        created_at=now,
        last_seen_at=now,
        expires_at=now + lifetime,
    )
    db.session.add(record)
    db.session.commit()

    session["session_token"] = raw_token
    session["session_token_hash"] = token_hash


def _revoke_current(reason: str) -> None:
    token_hash = session.get("session_token_hash")
    if not token_hash:
        return

    record = UserSession.query.filter_by(token_hash=token_hash).first()
    if record and record.revoked_at is None:
        record.revoked_at = datetime.utcnow()
        record.revoked_reason = reason
        db.session.commit()


def _invalidate_browser_session(message: str) -> None:
    _revoke_current("server_invalidated")
    logout_user()
    session.clear()
    flash(message, "info")


def _validate_current_session() -> bool:
    raw_token = session.get("session_token")
    token_hash = session.get("session_token_hash")

    if not raw_token or not token_hash:
        return False
    if not hmac.compare_digest(_token_hash(raw_token), token_hash):
        return False

    record = UserSession.query.filter_by(token_hash=token_hash).first()
    if not record or record.user_id != current_user.id:
        return False
    if record.revoked_at is not None or record.expires_at <= datetime.utcnow():
        return False
    if not hmac.compare_digest(
        record.password_fingerprint,
        _password_fingerprint(current_user.password_hash),
    ):
        record.revoked_at = datetime.utcnow()
        record.revoked_reason = "password_changed"
        db.session.commit()
        return False

    if datetime.utcnow() - record.last_seen_at >= timedelta(minutes=5):
        record.last_seen_at = datetime.utcnow()
        db.session.commit()

    return True


@session_registry_bp.get("")
@login_required
def list_sessions():
    sessions = (
        UserSession.query.filter_by(user_id=current_user.id)
        .order_by(UserSession.last_seen_at.desc())
        .all()
    )
    return render_template("auth/sessions.html", sessions=sessions)


@session_registry_bp.post("/<int:session_id>/revoke")
@login_required
def revoke_session(session_id: int):
    record = UserSession.query.filter_by(
        id=session_id,
        user_id=current_user.id,
    ).first_or_404()

    if record.token_hash == session.get("session_token_hash"):
        flash("Use Sign Out to end your current session.", "info")
        return redirect(url_for("session_registry.list_sessions"))

    if record.revoked_at is None:
        record.revoked_at = datetime.utcnow()
        record.revoked_reason = "user_revoked"
        db.session.commit()
        flash("That session has been signed out.", "success")

    return redirect(url_for("session_registry.list_sessions"))


@session_registry_bp.post("/revoke-others")
@login_required
def revoke_other_sessions():
    current_hash = session.get("session_token_hash")
    now = datetime.utcnow()

    count = (
        UserSession.query.filter(
            UserSession.user_id == current_user.id,
            UserSession.token_hash != current_hash,
            UserSession.revoked_at.is_(None),
        )
        .update(
            {
                UserSession.revoked_at: now,
                UserSession.revoked_reason: "user_revoked_others",
            },
            synchronize_session=False,
        )
    )
    db.session.commit()
    flash(f"Signed out {count} other session{'s' if count != 1 else ''}.", "success")
    return redirect(url_for("session_registry.list_sessions"))


def init_session_registry(app: Flask) -> None:
    @user_logged_in.connect_via(app)
    def register_login(_sender, user, **_extra):
        remember = bool(request.form.get("remember"))
        _create_session_record(user, remember)

    @user_logged_out.connect_via(app)
    def revoke_logout(_sender, user, **_extra):
        if user is not None:
            _revoke_current("logout")

    @app.before_request
    def enforce_registered_session():
        if not current_user.is_authenticated:
            return None

        if request.endpoint in {"static", "auth.logout"}:
            return None

        if _validate_current_session():
            return None

        _invalidate_browser_session(
            "Your Aura session ended or was revoked. Please sign in again."
        )
        return redirect(url_for("auth.login", next=request.path))
