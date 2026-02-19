from flask_login import current_user


def require_admin():
    """
    Authority gate.
    Confirms authenticated user has administrative privileges.
    Silent by design — caller decides response.
    """

    if not current_user.is_authenticated:
        return False

    if not hasattr(current_user, "role"):
        return False

    return current_user.role == "admin"
