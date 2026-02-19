from .routes.assessments import cars_assessments_bp


def register_cars_blueprints(app):
    app.register_blueprint(cars_assessments_bp)
