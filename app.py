# app.py

from flask import Flask
from flask_login import LoginManager
from flask_migrate import Migrate
from dotenv import load_dotenv
import os

from extensions import db

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

    # =================================================
    # Configuration (EXPLICIT — NO setdefault)
    # =================================================

    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "SQLALCHEMY_DATABASE_URI", "sqlite:///aura.db"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # ---- HARD FAIL IF SECRET KEY IS MISSING ----
    if not app.config["SECRET_KEY"]:
        raise RuntimeError("SECRET_KEY is not set. Check your .env file.")

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
    app.config["MAIL_DEBUG"] = True

    app.config["SERVER_NAME"] = os.getenv("SERVER_NAME")
    app.config["PREFERRED_URL_SCHEME"] = os.getenv("PREFERRED_URL_SCHEME")

    print("MAIL USER:", app.config["MAIL_USERNAME"])
    print("MAIL PASS LEN:", len(app.config["MAIL_PASSWORD"] or ""))

    # print("SECRET KEY LOADED")

    # =================================================
    # Jinja Globals
    # =================================================
    app.jinja_env.globals.update(has_feature=has_feature)

    # =================================================
    # Extensions (Single Source of Truth)
    # =================================================

    db.init_app(app)
    Migrate(app, db)

    login_manager = LoginManager()
    login_manager.login_view = "auth.login"  # type: ignore
    login_manager.session_protection = "strong"
    login_manager.init_app(app)

    # =================================================
    # User Loader
    # =================================================

    from models import User

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    # =================================================
    # Security Headers
    # =================================================

    @app.after_request
    def disable_caching(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
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

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(advisor_bp)

    app.register_blueprint(cars_bp, url_prefix="/cars")
    app.register_blueprint(admin_bp)

    app.register_blueprint(chat_bp)

    app.register_blueprint(treatments_bp, url_prefix="/treatments")
    app.register_blueprint(audit_bp, url_prefix="/audit")

    app.register_blueprint(intelligence_bp, url_prefix="/intelligence")
    app.register_blueprint(health_bp, url_prefix="/health")
    app.register_blueprint(notices_bp, url_prefix="/clinical_notices")
    app.register_blueprint(health_trajectory_bp, url_prefix="/health_trajectory")

    app.register_blueprint(stewardship_bp, url_prefix="/stewardship")
    app.register_blueprint(dashboard_bp, url_prefix="/dashboard")

    app.register_blueprint(concerns_bp)

    app.register_blueprint(assessments_bp)

    app.register_blueprint(driver_bp)

    @app.route("/")
    def home():
        return {
            "status": "Ajebo Fix Aura is LIVE 🚀",
            "available_routes": [
                "/auth",
                "/cars",
                "/chat",
                "/health",
                "/dashboard",
                "/intelligence",
            ],
        }

    return app


app = create_app()
print(app.url_map)
