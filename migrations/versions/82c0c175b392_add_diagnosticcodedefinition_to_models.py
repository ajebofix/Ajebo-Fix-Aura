# migrations/versions/82c0c175b392_add_diagnosticcodedefinition_to_models.py

"""Add DiagnosticCodeDefinition to models

Revision ID: 82c0c175b392
Revises: 290c5c28dd7d
Create Date: 2026-07-10 06:05:07.756077
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "82c0c175b392"
down_revision = "290c5c28dd7d"
branch_labels = None
depends_on = None


def upgrade():
    # -----------------------------------------------------
    # Create reusable DTC definition library
    # -----------------------------------------------------
    op.create_table(
        "diagnostic_code_definitions",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "code",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "code_type",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "manufacturer",
            sa.String(length=100),
            nullable=True,
        ),
        sa.Column(
            "is_generic",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column(
            "description",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "affected_system",
            sa.String(length=100),
            nullable=True,
        ),
        sa.Column(
            "severity",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "possible_causes",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "recommended_action",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "source",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column(
            "last_verified_at",
            sa.DateTime(),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "code",
            "manufacturer",
            name="uq_diagnostic_code_manufacturer",
        ),
    )

    with op.batch_alter_table(
        "diagnostic_code_definitions",
        schema=None,
    ) as batch_op:
        batch_op.create_index(
            "idx_diagnostic_code",
            ["code"],
            unique=False,
        )

    # -----------------------------------------------------
    # Link vehicle DTC occurrences to definitions
    # -----------------------------------------------------
    with op.batch_alter_table(
        "vehicle_dtcs",
        schema=None,
    ) as batch_op:
        batch_op.add_column(
            sa.Column(
                "definition_id",
                sa.Integer(),
                nullable=True,
            )
        )

        batch_op.create_index(
            "idx_vehicle_dtc_definition",
            ["definition_id"],
            unique=False,
        )

        batch_op.create_foreign_key(
            "fk_vehicle_dtcs_definition_id",
            "diagnostic_code_definitions",
            ["definition_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade():
    # -----------------------------------------------------
    # Remove vehicle-to-definition link
    # -----------------------------------------------------
    with op.batch_alter_table(
        "vehicle_dtcs",
        schema=None,
    ) as batch_op:
        batch_op.drop_constraint(
            "fk_vehicle_dtcs_definition_id",
            type_="foreignkey",
        )

        batch_op.drop_index(
            "idx_vehicle_dtc_definition",
        )

        batch_op.drop_column(
            "definition_id",
        )

    # -----------------------------------------------------
    # Remove definition library
    # -----------------------------------------------------
    with op.batch_alter_table(
        "diagnostic_code_definitions",
        schema=None,
    ) as batch_op:
        batch_op.drop_index(
            "idx_diagnostic_code",
        )

    op.drop_table(
        "diagnostic_code_definitions",
    )