"""store image asset ids

Revision ID: 20260420_03
Revises: 20260420_02
Create Date: 2026-04-20 23:45:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260420_03"
down_revision = "8c3042c1f5c0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("uploaders", sa.Column("avatar_asset_id", sa.Uuid(), nullable=True))
    op.create_index(
        op.f("ix_uploaders_avatar_asset_id"),
        "uploaders",
        ["avatar_asset_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_uploaders_avatar_asset_id_media_assets",
        "uploaders",
        "media_assets",
        ["avatar_asset_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column("videos", sa.Column("cover_asset_id", sa.Uuid(), nullable=True))
    op.create_index(
        op.f("ix_videos_cover_asset_id"),
        "videos",
        ["cover_asset_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_videos_cover_asset_id_media_assets",
        "videos",
        "media_assets",
        ["cover_asset_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.alter_column(
        "video_comment_images",
        "source_url",
        existing_type=sa.String(length=1024),
        nullable=True,
    )


def downgrade():
    op.alter_column(
        "video_comment_images",
        "source_url",
        existing_type=sa.String(length=1024),
        nullable=False,
    )

    op.drop_constraint(
        "fk_videos_cover_asset_id_media_assets",
        "videos",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_videos_cover_asset_id"), table_name="videos")
    op.drop_column("videos", "cover_asset_id")

    op.drop_constraint(
        "fk_uploaders_avatar_asset_id_media_assets",
        "uploaders",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_uploaders_avatar_asset_id"), table_name="uploaders")
    op.drop_column("uploaders", "avatar_asset_id")
