from datetime import datetime
from flask_login import login_required
from app import db
from flask_login import current_user

from flask import (
    Blueprint,
)
from flask_login import login_required, current_user

from app import db
from models import (
    VehicleAssessment,
    Consultation,
    CarOwnership,
)


# =====================================================
# BLUEPRINT
# =====================================================

admin_assessments_bp = Blueprint(
    "admin_assessments",
    __name__,
    url_prefix="/admin/assessments",
)


@admin_assessments_bp.route("/test")
def test_route():
    return "Admin Assessments Working"
