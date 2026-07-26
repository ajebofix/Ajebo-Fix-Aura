"""add client profiles

Revision ID: c19f2a8b6d41
Revises: 7f3a9c2d5e81
Create Date: 2026-07-26 02:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "c19f2a8b6d41"
down_revision = "7f3a9c2d5e81"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "client_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("profile_photo_key", sa.String(length=255), nullable=True),
        sa.Column("occupation", sa.String(length=120), nullable=True),
        sa.Column("organisation", sa.String(length=120), nullable=True),
        sa.Column("gender", sa.String(length=30), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("state_region", sa.String(length=120), nullable=True),
        sa.Column(
            "country",
            sa.String(length=120),
            server_default="Nigeria",
            nullable=False,
        ),
        sa.Column("home_address_ciphertext", sa.Text(), nullable=True),
        sa.Column("office_address_ciphertext", sa.Text(), nullable=True),
        sa.Column("preferred_communication", sa.String(length=30), nullable=True),
        sa.Column(
            "preferred_communication_time",
            sa.String(length=120),
            nullable=True,
        ),
        sa.Column("care_preference", sa.Text(), nullable=True),
        sa.Column("preferred_language", sa.String(length=80), nullable=True),
        sa.Column(
            "timezone",
            sa.String(length=80),
            server_default="Africa/Lagos",
            nullable=False,
        ),
        sa.Column("emergency_contact_name_ciphertext", sa.Text(), nullable=True),
        sa.Column("emergency_contact_phone_ciphertext", sa.Text(), nullable=True),
        sa.Column(
            "marketing_consent",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "gender IS NULL OR gender IN "
            "('female', 'male', 'non_binary', 'prefer_not_to_say')",
            name="ck_client_profiles_gender",
        ),
        sa.CheckConstraint(
            "preferred_communication IS NULL OR preferred_communication IN "
            "('whatsapp', 'phone', 'email', 'sms')",
            name="ck_client_profiles_preferred_communication",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_client_profiles_user_id"),
    )


def downgrade():
    op.drop_table("client_profiles")
