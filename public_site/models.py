"""Private-access requests, separate from authenticated vehicle records."""

from datetime import datetime

from extensions import db


class PrivateAccessRequest(db.Model):
    __tablename__ = "private_access_requests"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(254), nullable=False, index=True)
    phone = db.Column(db.String(30), nullable=True)
    vehicle = db.Column(db.String(120), nullable=True)
    request_type = db.Column(db.String(20), nullable=False)
    message = db.Column(db.String(1000), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="pending", server_default="pending")
    consented_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.CheckConstraint(
            "request_type IN ('private_access', 'assessment', 'fleet')",
            name="ck_private_access_request_type",
        ),
        db.CheckConstraint(
            "status IN ('pending', 'contacted', 'closed')",
            name="ck_private_access_request_status",
        ),
    )
