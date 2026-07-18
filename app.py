# app.py

from datetime import timedelta
import os

from dotenv import load_dotenv
from flask import Flask
from flask_login import LoginManager
from flask_migrate import Migrate
from werkzeug.middleware.proxy_fix import ProxyFix

from extensions import db
from security.csrf import init_csrf
from services.feature_gateways import has_feature


# =====================================================
# Load environment variables FIRST
# =====================================================
load_dotenv(override=True)


# =====================================================
# AURA — PRIVATE AUTOMOTIVE HEALTH PORTAL
# Application Factory
# =====================================================


def create_app():
    app = Flask(__name__)

    # Railway and other production platforms terminate TLS at a reverse proxy.
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=1,
        x_proto=1,
        x_host=1,
        x_port=1,
    )

    environment = os.getenv("APP_ENV", "development").strip().lower()
    is_production = environment == "production"

    # =================================================
    # Configuration
    # =================================================
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "SQLALCHEMY_DATABASE_URI",
        "sqlite:///aura.db",
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

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
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
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

    # =================================================
    # Jinja Globals
    # =================================================
    app.jinja_env.globals.update(has_feature=has_feature)

    # =================================================
    # Extensions
    # =================================================
    db.init_app(app)
    Migrate(app, db)

    login_manager = LoginManager()
    login_manager.login_view = "auth.login"  # type: ignore
    login_manager.session_protection = "strong"
    login_manager.init_app(app)

    init_csrf(app)

    # =================================================
    # User Loader
    # =================================================
    from models import User

    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(User, int(user_id))
        except (TypeError, ValueError):
            return None

    # =================================================
    # Security Headers
    # =================================================
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

        # This CSP matches Aura's current Google Fonts usage and inline scripts.
        # Inline script/style allowances should be removed later with nonces.
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

    # =================================================
    # Blueprint Registration
    # =================================================
    from auth.routes import auth_bp, advisor_bp
    from cars.routes import cars_bp
    from admin.routes import admin_bp
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

    # These blueprints already define their own URL prefixes.
    app.register_blueprint(auth_bp)
    app.register_blueprint(advisor_bp)
    app.register_blueprint(cars_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(driver_bp)

    # These blueprints intentionally receive their prefix here.
    app.register_blueprint(admin_bp)
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

    @app.get("/")
    def home():
        return {
            "status": "ok",
            "service": "Ajebo Fix Aura",
        }

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}, 200

    return app


app = create_app()

if __name__ == "__main__":
    app.run(
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG") == "1",
    )
