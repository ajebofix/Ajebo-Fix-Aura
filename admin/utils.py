# admin/utils.py

from functools import wraps
from flask import abort
from flask_login import current_user


# =====================================================
# AURA — ADVISOR AUTHORITY REQUIREMENT
# =====================================================


def advisor_required(view_func):
    """
    Ensures the request is handled by an authorized Aura advisor.

    This guard represents clinical authority,
    not elevated privilege.
    """

    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        # Must be authenticated
        if not current_user.is_authenticated:
            abort(401)

        # Must be assigned advisor role
        # (internally still 'admin' for V1 compatibility)
        if current_user.role != "admin":
            abort(403)

        return view_func(*args, **kwargs)

    return wrapped_view
