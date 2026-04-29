"""bridge legacy alembic revision id

Revision ID: 8c3042c1f5c0
Revises: 20260420_02
Create Date: 2026-04-20 23:58:00.000000

"""

# revision identifiers, used by Alembic.
revision = "8c3042c1f5c0"
down_revision = "20260420_02"
branch_labels = None
depends_on = None


def upgrade():
    # Some local databases still point at the removed legacy revision id.
    # Keep this bridge as a no-op so they can advance onto the current chain.
    pass


def downgrade():
    pass
