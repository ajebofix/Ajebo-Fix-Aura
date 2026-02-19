from flask import Blueprint

# =====================================================
# AURA HOME
# Private Automotive Health Portal
# =====================================================

dashboard_bp = Blueprint(
    "dashboard",
    __name__,
    template_folder="../templates",
)
