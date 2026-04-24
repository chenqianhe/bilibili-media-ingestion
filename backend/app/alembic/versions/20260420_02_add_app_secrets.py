"""add app secrets

Revision ID: 20260420_02
Revises: 20260420_01
Create Date: 2026-04-20 22:05:00.000000

"""

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260420_02"
down_revision = "20260420_01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "app_secrets",
        sa.Column("key", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("encrypted_value", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "updated_by",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index(
        op.f("ix_app_secrets_updated_at"),
        "app_secrets",
        ["updated_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_app_secrets_updated_by"),
        "app_secrets",
        ["updated_by"],
        unique=False,
    )


def downgrade():
    op.drop_index(op.f("ix_app_secrets_updated_by"), table_name="app_secrets")
    op.drop_index(op.f("ix_app_secrets_updated_at"), table_name="app_secrets")
    op.drop_table("app_secrets")
