"""Create isolated Prince workshop pilot tables (inactive by default).

Revision ID: b71e5ca90d24
Revises: a6e4d9b2c731
Create Date: 2026-11-21

No old Aura user, car, evidence, invoice, or ownership data is migrated.
"""
from alembic import op
import sqlalchemy as sa

revision = "b71e5ca90d24"
down_revision = "a6e4d9b2c731"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "prince_tenants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("intake_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("approved_privacy_notice_version", sa.String(48)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "prince_memberships",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("prince_tenants.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_prince_member_tenant_user"),
        sa.CheckConstraint("role IN ('principal', 'operator', 'pilot_admin')", name="ck_prince_member_role"),
    )
    op.create_index("ix_prince_memberships_tenant_id", "prince_memberships", ["tenant_id"])
    op.create_index("ix_prince_memberships_user_id", "prince_memberships", ["user_id"])
    op.create_table(
        "prince_customers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("prince_tenants.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("full_name", sa.String(120), nullable=False),
        sa.Column("phone_ciphertext", sa.Text()),
        sa.Column("email_ciphertext", sa.Text()),
        sa.Column("address_ciphertext", sa.Text()),
        sa.Column("intake_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("privacy_notice_version", sa.String(48), nullable=False),
        sa.Column("participation_confirmed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("id", "tenant_id", name="uq_prince_customer_id_tenant"),
    )
    op.create_index("ix_prince_customers_tenant_id", "prince_customers", ["tenant_id"])
    op.create_table(
        "prince_vehicles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("prince_tenants.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("brand", sa.String(100), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("year", sa.Integer()),
        sa.Column("vin", sa.String(50)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("id", "tenant_id", name="uq_prince_vehicle_id_tenant"),
        sa.UniqueConstraint("tenant_id", "vin", name="uq_prince_tenant_vin"),
        sa.ForeignKeyConstraint(
            ["customer_id", "tenant_id"],
            ["prince_customers.id", "prince_customers.tenant_id"],
            name="fk_prince_vehicle_customer_tenant", ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_prince_vehicles_tenant_id", "prince_vehicles", ["tenant_id"])
    op.create_table(
        "prince_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("prince_tenants.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), nullable=False),
        sa.Column("assigned_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reported_concern", sa.Text()),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'awaiting_approval', 'approved', 'in_progress', 'closed')",
            name="ck_prince_job_status",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id", "tenant_id"],
            ["prince_customers.id", "prince_customers.tenant_id"],
            name="fk_prince_job_customer_tenant", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["vehicle_id", "tenant_id"],
            ["prince_vehicles.id", "prince_vehicles.tenant_id"],
            name="fk_prince_job_vehicle_tenant", ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_prince_jobs_tenant_id", "prince_jobs", ["tenant_id"])
    op.create_index("ix_prince_jobs_assigned_user_id", "prince_jobs", ["assigned_user_id"])
    op.create_table(
        "prince_audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("prince_tenants.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("action", sa.String(48), nullable=False),
        sa.Column("target_kind", sa.String(48), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_prince_audit_events_tenant_id", "prince_audit_events", ["tenant_id"])


def downgrade():
    tables = (
        "prince_audit_events", "prince_jobs", "prince_vehicles",
        "prince_customers", "prince_memberships", "prince_tenants",
    )
    connection = op.get_bind()
    for table in tables:
        if connection.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar():
            raise RuntimeError("Prince pilot data exists: refusing destructive downgrade.")
    op.drop_index("ix_prince_audit_events_tenant_id", table_name="prince_audit_events")
    op.drop_table("prince_audit_events")
    op.drop_index("ix_prince_jobs_assigned_user_id", table_name="prince_jobs")
    op.drop_index("ix_prince_jobs_tenant_id", table_name="prince_jobs")
    op.drop_table("prince_jobs")
    op.drop_index("ix_prince_vehicles_tenant_id", table_name="prince_vehicles")
    op.drop_table("prince_vehicles")
    op.drop_index("ix_prince_customers_tenant_id", table_name="prince_customers")
    op.drop_table("prince_customers")
    op.drop_index("ix_prince_memberships_user_id", table_name="prince_memberships")
    op.drop_index("ix_prince_memberships_tenant_id", table_name="prince_memberships")
    op.drop_table("prince_memberships")
    op.drop_table("prince_tenants")
