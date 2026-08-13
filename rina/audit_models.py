"""Privacy-safe audit model for material Rina orchestration outcomes."""

from __future__ import annotations

from datetime import datetime

from extensions import db


class RinaAIAuditEvent(db.Model):
    __tablename__ = "rina_ai_audit_events"

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.String(64), nullable=False, unique=True, index=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    car_id = db.Column(
        db.Integer,
        db.ForeignKey("cars.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    authority = db.Column(db.String(32), nullable=True)
    state = db.Column(db.String(32), nullable=False)
    outcome = db.Column(db.String(32), nullable=False)
    action_family = db.Column(db.String(64), nullable=False, default="respond")

    provider = db.Column(db.String(32), nullable=True)
    provider_model = db.Column(db.String(96), nullable=True)
    provider_status = db.Column(db.String(32), nullable=False)
    provider_request_id = db.Column(db.String(128), nullable=True)

    evidence_refs = db.Column(db.JSON, nullable=True)
    audit_metadata = db.Column(db.JSON, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_safe_dict(self) -> dict[str, object]:
        """Expose only metadata deliberately allowed in the AI audit surface."""

        return {
            "id": self.id,
            "request_id": self.request_id,
            "user_id": self.user_id,
            "car_id": self.car_id,
            "authority": self.authority,
            "state": self.state,
            "outcome": self.outcome,
            "action_family": self.action_family,
            "provider": self.provider,
            "provider_model": self.provider_model,
            "provider_status": self.provider_status,
            "provider_request_id": self.provider_request_id,
            "evidence_refs": self.evidence_refs or [],
            "audit_metadata": self.audit_metadata or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
