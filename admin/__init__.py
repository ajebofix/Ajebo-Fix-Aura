from .routes.assessments import admin_assessments_bp


def register_admin_blueprints(app):
    app.register_blueprint(admin_assessments_bp)
