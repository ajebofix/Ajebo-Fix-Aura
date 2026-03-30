from .modules.assessments import assessments_bp


def register_admin_blueprints(app):
    app.register_blueprint(assessments_bp)
