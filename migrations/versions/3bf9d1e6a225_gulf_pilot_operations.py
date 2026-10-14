"""Add admin-only Gulf pilot operations records.

Revision ID: 3bf9d1e6a225
Revises: a13f7b6c2d90
Create Date: 2026-10-14

No customer records are backfilled and the Gulf feature stays off by default.
"""
from alembic import op
import sqlalchemy as sa

revision = "3bf9d1e6a225"
down_revision = "a13f7b6c2d90"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "gulf_technicians",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("phone_cipher", sa.Text()),
        sa.Column("qualified_classes", sa.String(100), nullable=False),
        sa.Column("is_intern", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "gulf_shifts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("technician_id", sa.Integer(), sa.ForeignKey("gulf_technicians.id"), nullable=False),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("ends_at", sa.DateTime(), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("recorded_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
    )
    op.create_index("ix_gulf_shifts_technician_id", "gulf_shifts", ["technician_id"])
    op.create_table(
        "gulf_busy_slots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("technician_id", sa.Integer(), sa.ForeignKey("gulf_technicians.id")),
        sa.Column("bay", sa.String(32)),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("ends_at", sa.DateTime(), nullable=False),
        sa.Column("is_oil_change", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(100), nullable=False),
        sa.Column("recorded_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
    )
    op.create_table(
        "gulf_day_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service_day", sa.Date(), nullable=False),
        sa.Column("daily_cap", sa.Integer(), nullable=False),
        sa.Column("concurrent_cap", sa.Integer(), nullable=False),
        sa.Column("saturday_verified", sa.Boolean(), nullable=False),
        sa.Column("sunday_open", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(240), nullable=False),
        sa.Column("approved_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.UniqueConstraint("service_day", name="uq_gulf_day_plans_service_day"),
    )
    op.create_table(
        "gulf_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("voucher_digest", sa.String(64), nullable=False),
        sa.Column("voucher_cipher", sa.Text(), nullable=False),
        sa.Column("voucher_tail", sa.String(8), nullable=False),
        sa.Column("name_cipher", sa.Text(), nullable=False),
        sa.Column("phone_cipher", sa.Text(), nullable=False),
        sa.Column("plate_cipher", sa.Text(), nullable=False),
        sa.Column("make", sa.String(70), nullable=False),
        sa.Column("model", sa.String(90), nullable=False),
        sa.Column("model_year", sa.Integer()),
        sa.Column("fuel", sa.String(30)),
        sa.Column("service_class", sa.String(20), nullable=False),
        sa.Column("requested_day", sa.Date()),
        sa.Column("state", sa.String(25), nullable=False),
        sa.Column("voucher_verified_at", sa.DateTime()),
        sa.Column("verification_reference", sa.String(120)),
        sa.Column("technical_approved_at", sa.DateTime()),
        sa.Column("approved_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("oil_spec", sa.String(100)),
        sa.Column("oil_sku", sa.String(100)),
        sa.Column("oil_litres", sa.String(12)),
        sa.Column("filter_part", sa.String(100)),
        sa.Column("dispatch_state", sa.String(20), nullable=False),
        sa.Column("dispatch_reference", sa.String(100)),
        sa.Column("products_received_at", sa.DateTime()),
        sa.Column("slot_start", sa.DateTime()),
        sa.Column("slot_end", sa.DateTime()),
        sa.Column("hold_expires_at", sa.DateTime()),
        sa.Column("technician_id", sa.Integer(), sa.ForeignKey("gulf_technicians.id")),
        sa.Column("bay", sa.String(32)),
        sa.Column("customer_confirmed", sa.Boolean(), nullable=False),
        sa.Column("commission_kobo", sa.Integer()),
        sa.Column("labour_fee_kobo", sa.Integer()),
        sa.Column("actual_start", sa.DateTime()),
        sa.Column("actual_finish", sa.DateTime()),
        sa.Column("odometer_km", sa.Integer()),
        sa.Column("oil_batch", sa.String(80)),
        sa.Column("work_result", sa.String(500)),
        sa.Column("inspection", sa.JSON()),
        sa.Column("handover_at", sa.DateTime()),
        sa.Column("claim_state", sa.String(20), nullable=False),
        sa.Column("claim_submitted_at", sa.DateTime()),
        sa.Column("paid_kobo", sa.Integer(), nullable=False),
        sa.Column("commission_paid_kobo", sa.Integer(), nullable=False),
        sa.Column("payment_reference", sa.String(100)),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("voucher_digest", name="uq_gulf_jobs_voucher_digest"),
    )
    op.create_index("ix_gulf_jobs_state", "gulf_jobs", ["state"])
    op.create_table(
        "gulf_photos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("gulf_jobs.id"), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("object_key", sa.String(300), nullable=False),
        sa.Column("mime_type", sa.String(40), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("object_key", name="uq_gulf_photos_object_key"),
    )
    op.create_index("ix_gulf_photos_job_id", "gulf_photos", ["job_id"])
    op.create_table(
        "gulf_payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("gulf_jobs.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("amount_kobo", sa.Integer(), nullable=False),
        sa.Column("reference", sa.String(100), nullable=False),
        sa.Column("recorded_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("job_id", "kind", "reference", name="uq_gulf_payment_reference"),
    )
    op.create_index("ix_gulf_payments_job_id", "gulf_payments", ["job_id"])
    op.create_table(
        "gulf_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("gulf_jobs.id")),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(65), nullable=False),
        sa.Column("note", sa.String(240)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_gulf_audit_job_id", "gulf_audit", ["job_id"])


def downgrade():
    connection = op.get_bind()
    for table in (
        "gulf_jobs", "gulf_photos", "gulf_audit", "gulf_payments",
        "gulf_technicians", "gulf_shifts", "gulf_busy_slots", "gulf_day_plans",
    ):
        if connection.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar():
            raise RuntimeError("Gulf pilot data exists; refusing destructive downgrade.")
    op.drop_index("ix_gulf_payments_job_id", table_name="gulf_payments")
    op.drop_table("gulf_payments")
    op.drop_index("ix_gulf_audit_job_id", table_name="gulf_audit")
    op.drop_table("gulf_audit")
    op.drop_index("ix_gulf_photos_job_id", table_name="gulf_photos")
    op.drop_table("gulf_photos")
    op.drop_index("ix_gulf_jobs_state", table_name="gulf_jobs")
    op.drop_table("gulf_jobs")
    op.drop_table("gulf_day_plans")
    op.drop_table("gulf_busy_slots")
    op.drop_index("ix_gulf_shifts_technician_id", table_name="gulf_shifts")
    op.drop_table("gulf_shifts")
    op.drop_table("gulf_technicians")
