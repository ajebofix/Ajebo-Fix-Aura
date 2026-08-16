# app.py

from datetime import timedelta
import os

from dotenv import load_dotenv
from flask import Flask, redirect, request, session, url_for
from flask_login import LoginManager
from flask_migrate import Migrate
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.middleware.proxy_fix import ProxyFix

from extensions import db
from security.csrf import init_csrf
from security.email_verification import (
    email_verification_bp,
    init_email_verification,
    register_email_verification_gates,
)
from security.rate_limits import init_rate_limiting, register_rate_limits
from security.session_registry import init_session_registry, session_registry_bp
from services.feature_gateways import has_feature

import security.session_events  # noqa: F401, E402
import services.reported_concern_session_events  # noqa: F401, E402
import rina.memory_model_extensions  # noqa: F401, E402


load_dotenv(override=False)


def _environment_name() -> str:
    return (
        os.getenv("APP_ENV")
        or os.getenv("RAILWAY_ENVIRONMENT_NAME")
        or "development"
    ).strip().lower()


def _environment_flag(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _database_uri(*, is_production: bool) -> str:
    uri = (
        os.getenv("SQLALCHEMY_DATABASE_URI")
        or os.getenv("DATABASE_URL")
        or os.getenv("DATABASE_PRIVATE_URL")
    )

    if uri and uri.startswith("postgres://"):
        uri = "postgresql://" + uri[len("postgres://") :]

    if uri:
        return uri

    if is_production or os.getenv("RAILWAY_ENVIRONMENT_NAME"):
        raise RuntimeError(
            "Production database is not configured. Set SQLALCHEMY_DATABASE_URI, "
            "DATABASE_URL, or DATABASE_PRIVATE_URL to the Railway PostgreSQL URL."
        )

    return "sqlite:///aura.db"


def create_app():
    app = Flask(__name__)

    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=1,
        x_proto=1,
        x_host=1,
        x_port=1,
    )

    environment = _environment_name()
    is_production = environment == "production" or bool(
        os.getenv("RAILWAY_ENVIRONMENT_NAME")
    )
    runtime_commit = (
        os.getenv("RAILWAY_GIT_COMMIT_SHA")
        or os.getenv("GIT_COMMIT_SHA")
        or "unknown"
    )

    app.config["APP_ENV"] = environment
    app.config["RUNTIME_COMMIT"] = runtime_commit
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY")
    app.config["SQLALCHEMY_DATABASE_URI"] = _database_uri(
        is_production=is_production
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["RATELIMIT_STORAGE_URI"] = (
        os.getenv("RATE_LIMIT_STORAGE_URI")
        or os.getenv("REDIS_URL")
        or "memory://"
    )
    app.config["PROFILE_ENCRYPTION_KEY"] = os.getenv("PROFILE_ENCRYPTION_KEY")
    app.config["PROFILE_ENCRYPTION_KEY_VERSION"] = os.getenv(
        "PROFILE_ENCRYPTION_KEY_VERSION",
        "v1",
    )
    app.config["PROFILE_ENCRYPTION_KEYS"] = os.getenv("PROFILE_ENCRYPTION_KEYS")
    app.config["PROFILE_ENCRYPTION_ACTIVE_KEY_VERSION"] = os.getenv(
        "PROFILE_ENCRYPTION_ACTIVE_KEY_VERSION",
        app.config["PROFILE_ENCRYPTION_KEY_VERSION"],
    )
    app.config["EVIDENCE_IMAGE_INTAKE_ENABLED"] = _environment_flag(
        "EVIDENCE_IMAGE_INTAKE_ENABLED",
        default=False,
    )
    app.config["EVIDENCE_RETRIEVAL_ENABLED"] = _environment_flag(
        "EVIDENCE_RETRIEVAL_ENABLED",
        default=False,
    )
    app.config["EVIDENCE_ADVISOR_DELETION_ENABLED"] = _environment_flag(
        "EVIDENCE_ADVISOR_DELETION_ENABLED",
        default=False,
    )
    app.config["EVIDENCE_ADVISOR_REVIEW_ENABLED"] = _environment_flag(
        "EVIDENCE_ADVISOR_REVIEW_ENABLED",
        default=False,
    )
    app.config["EVIDENCE_RETENTION_DAYS"] = os.getenv("EVIDENCE_RETENTION_DAYS")
    app.config["EVIDENCE_RETRIEVAL_GRANT_SECONDS"] = os.getenv(
        "EVIDENCE_RETRIEVAL_GRANT_SECONDS"
    )
    app.config["EVIDENCE_STORAGE_PROVIDER"] = os.getenv(
        "EVIDENCE_STORAGE_PROVIDER",
        "r2",
    )
    app.config["R2_ACCOUNT_ID"] = os.getenv("R2_ACCOUNT_ID")
    app.config["R2_ACCESS_KEY_ID"] = os.getenv("R2_ACCESS_KEY_ID")
    app.config["R2_SECRET_ACCESS_KEY"] = os.getenv("R2_SECRET_ACCESS_KEY")
    app.config["R2_BUCKET"] = os.getenv("R2_BUCKET")

    if not app.config["SECRET_KEY"]:
        raise RuntimeError("SECRET_KEY is not set. Check your environment variables.")

    app.config.update(
        SESSION_COOKIE_SECURE=is_production,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        REMEMBER_COOKIE_SECURE=is_production,
        REMEMBER_COOKIE_HTTPONLY=True,
        REMEMBER_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        # The image sanitizer enforces a strict 2 MB file limit. Keep a small
        # multipart/form-data allowance so a 2 MB image plus request framing is
        # not rejected by Flask before the evidence validator can inspect it.
        MAX_CONTENT_LENGTH=3 * 1024 * 1024,
    )

    app.config["MAIL_SERVER"] = "smtp.gmail.com"
    app.config["MAIL_PORT"] = 587
    app.config["MAIL_USE_TLS"] = True
    app.config["MAIL_USE_SSL"] = False
    app.config["MAIL_SUPPRESS_SEND"] = False
    app.config["MAIL_USERNAME"] = os.environ.get("MAIL_USERNAME")
    app.config["MAIL_PASSWORD"] = os.environ.get("MAIL_PASSWORD")
    app.config["MAIL_DEFAULT_SENDER"] = os.environ.get("MAIL_DEFAULT_SENDER")
    app.config["MAIL_MAX_EMAILS"] = None
    app.config["MAIL_ASCII_ATTACHMENTS"] = False
    app.config["MAIL_TIMEOUT"] = 30
    app.config["MAIL_DEBUG"] = not is_production and os.getenv("MAIL_DEBUG") == "1"

    app.config["SERVER_NAME"] = os.getenv("SERVER_NAME")
    app.config["PREFERRED_URL_SCHEME"] = os.getenv(
        "PREFERRED_URL_SCHEME",
        "https" if is_production else "http",
    )

    app.jinja_env.globals.update(has_feature=has_feature)

    db.init_app(app)
    Migrate(app, db)

    login_manager = LoginManager()
    login_manager.login_view = "auth.login"  # type: ignore
    login_manager.session_protection = "strong"
    login_manager.init_app(app)

    init_csrf(app)
    init_rate_limiting(app)
    init_email_verification(app)
    init_session_registry(app)

    from models import User
    from profiles.models import ClientProfile, ProfileAuditEvent  # noqa: F401

    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(User, int(user_id))
        except (TypeError, ValueError):
            return None
        except SQLAlchemyError:
            db.session.rollback()
            session.clear()
            app.logger.exception(
                "Discarded an incompatible or stale authenticated session"
            )
            return None

    @app.after_request
    def apply_security_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "img-src 'self' data:; "
            "font-src 'self' https://fonts.gstatic.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "script-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )

        if is_production:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )

        return response

    from auth.routes import auth_bp, advisor_bp
    from cars.routes import cars_bp
    from admin.routes import admin_bp
    from admin.progression_routes import concern_progression_bp
    from admin.rina_operations import rina_operations_bp
    from routes.chat import chat_bp
    from events.routes import treatments_bp
    from events.audit_routes import audit_bp
    from cars.intelligence_routes import intelligence_bp
    from health.routes import health_bp
    from health.alert_routes import notices_bp
    from routes.health_trends import health_trajectory_bp
    from ownership.routes import stewardship_bp
    from dashboard.routes import dashboard_bp
    from cars.fault_routes import concerns_bp
    from admin.modules.assessments import assessments_bp
    from driver.routes import driver_bp
    from evidence.routes import evidence_bp
    from evidence.review_routes import evidence_review_bp
    from profiles.routes import profiles_bp
    from services.owner_driver_management import init_owner_driver_management

    app.register_blueprint(auth_bp)
    app.register_blueprint(advisor_bp)
    app.register_blueprint(email_verification_bp)
    app.register_blueprint(session_registry_bp)
    app.register_blueprint(cars_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(driver_bp)
    app.register_blueprint(profiles_bp)
    app.register_blueprint(evidence_bp)
    app.register_blueprint(evidence_review_bp)

    app.register_blueprint(admin_bp)
    app.register_blueprint(concern_progression_bp)
    app.register_blueprint(rina_operations_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(treatments_bp, url_prefix="/treatments")
    app.register_blueprint(audit_bp, url_prefix="/audit")
    app.register_blueprint(intelligence_bp, url_prefix="/intelligence")
    app.register_blueprint(health_bp, url_prefix="/health")
    app.register_blueprint(notices_bp, url_prefix="/clinical_notices")
    app.register_blueprint(health_trajectory_bp, url_prefix="/health_trajectory")
    app.register_blueprint(stewardship_bp, url_prefix="/stewardship")
    app.register_blueprint(concerns_bp)
    app.register_blueprint(assessments_bp)

    init_owner_driver_management(app)
    register_rate_limits(app)
    register_email_verification_gates(app)

    @app.get("/")
    def home():
        return {
            "status": "ok",
            "service": "Ajebo Fix Aura",
        }

    @app.route("/login", methods=["GET", "POST"])
    def login_alias():
        next_path = request.args.get("next")
        target = (
            url_for("auth.login", next=next_path)
            if next_path
            else url_for("auth.login")
        )
        return redirect(target, code=307 if request.method == "POST" else 302)

    @app.get("/version")
    def version():
        return {
            "service": "Ajebo Fix Aura",
            "commit": app.config["RUNTIME_COMMIT"],
            "environment": app.config["APP_ENV"],
            "database": db.engine.url.get_backend_name(),
        }, 200

    @app.get("/healthz")
    def healthz():
        try:
            db.session.execute(text("SELECT 1"))
            inspector = inspect(db.engine)
            tables = set(inspector.get_table_names())
            user_columns = (
                {column["name"] for column in inspector.get_columns("users")}
                if "users" in tables
                else set()
            )

            required_tables = {
                "users",
                "user_sessions",
                "client_profiles",
                "profile_audit_events",
            }
            missing_tables = required_tables - tables
            missing_columns = {"email_verified_at"} - user_columns

            if missing_tables or missing_columns:
                return {
                    "status": "not_ready",
                    "commit": app.config["RUNTIME_COMMIT"],
                    "missing_tables": sorted(missing_tables),
                    "missing_columns": sorted(missing_columns),
                }, 503

            return {
                "status": "ok",
                "commit": app.config["RUNTIME_COMMIT"],
                "database": db.engine.url.get_backend_name(),
            }, 200
        except Exception:
            app.logger.exception("Aura readiness check failed")
            return {
                "status": "not_ready",
                "commit": app.config["RUNTIME_COMMIT"],
            }, 503

    with app.app_context():
        app.logger.warning(
            "Aura runtime identity commit=%s environment=%s database=%s",
            app.config["RUNTIME_COMMIT"],
            app.config["APP_ENV"],
            db.engine.url.get_backend_name(),
        )

    return app


# Railway currently starts Aura with ``gunicorn app:app``. Expose a WSGI
# application object while keeping ``create_app`` available to tests and tools.
app = create_app()