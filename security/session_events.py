"""Database-level security events for Aura sessions."""

from datetime import datetime

from sqlalchemy import event, inspect, update

from models import User
from security.session_registry import UserSession


@event.listens_for(User, "after_update")
def revoke_sessions_after_password_change(_mapper, connection, target):
    """Revoke every active session whenever a password hash changes."""

    history = inspect(target).attrs.password_hash.history
    if not history.has_changes():
        return

    connection.execute(
        update(UserSession)
        .where(
            UserSession.user_id == target.id,
            UserSession.revoked_at.is_(None),
        )
        .values(
            revoked_at=datetime.utcnow(),
            revoked_reason="password_changed",
        )
    )
