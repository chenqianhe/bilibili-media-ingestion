"""remove rights feature

Revision ID: 20260421_01
Revises: 20260420_03
Create Date: 2026-04-21 09:30:00.000000

"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260421_01"
down_revision = "20260420_03"
branch_labels = None
depends_on = None

_RIGHTS_AUDIT_ACTIONS = (
    "content_right.created",
    "content_right.reviewed",
    "ingest_job.download_blocked",
    "ingest_job.resumed_after_policy_change",
    "ingest_job.resumed_after_rights_approval",
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _scrub_progress(progress: Any, *, transitioned_at: datetime) -> dict[str, Any]:
    normalized = dict(progress) if isinstance(progress, dict) else {}
    normalized.pop("rights", None)

    metadata = normalized.get("metadata")
    if isinstance(metadata, dict):
        metadata = dict(metadata)
        metadata.pop("rights_status", None)
        normalized["metadata"] = metadata

    normalized["last_transition_at"] = transitioned_at.isoformat()
    return normalized


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())
    transitioned_at = _now_utc()

    if "ingest_jobs" in table_names:
        ingest_jobs = sa.table(
            "ingest_jobs",
            sa.column("id", sa.Uuid()),
            sa.column("status", sa.String(length=64)),
            sa.column("phase", sa.String(length=255)),
            sa.column("progress", sa.JSON()),
            sa.column("started_at", sa.DateTime(timezone=True)),
            sa.column("finished_at", sa.DateTime(timezone=True)),
            sa.column("error_code", sa.String(length=128)),
            sa.column("error_message", sa.String()),
        )

        rows = bind.execute(
            sa.select(
                ingest_jobs.c.id,
                ingest_jobs.c.status,
                ingest_jobs.c.progress,
            )
        ).fetchall()

        for row in rows:
            progress = _scrub_progress(row.progress, transitioned_at=transitioned_at)
            values: dict[str, Any] = {"progress": progress}

            if row.status == "blocked_by_policy":
                metadata = progress.get("metadata")
                if isinstance(metadata, dict):
                    progress["current_step"] = "metadata_ready"
                    progress["next_step"] = "downloader_worker"
                    values.update(
                        {
                            "status": "metadata_ready",
                            "phase": "metadata stored; ready for download worker",
                            "finished_at": None,
                        }
                    )
                else:
                    progress["current_step"] = "metadata_pending"
                    progress["next_step"] = "metadata_worker"
                    values.update(
                        {
                            "status": "pending",
                            "phase": "queued for metadata ingestion",
                            "started_at": None,
                            "finished_at": None,
                        }
                    )

                values["error_code"] = None
                values["error_message"] = None
                values["progress"] = progress

            bind.execute(
                sa.update(ingest_jobs)
                .where(ingest_jobs.c.id == row.id)
                .values(**values)
            )

    if "audit_events" in table_names:
        audit_events = sa.table(
            "audit_events",
            sa.column("action", sa.String(length=128)),
        )
        bind.execute(
            sa.delete(audit_events).where(
                audit_events.c.action.in_(_RIGHTS_AUDIT_ACTIONS)
            )
        )

    if "videos" in table_names:
        video_indexes = {index["name"] for index in inspector.get_indexes("videos")}
        video_columns = {column["name"] for column in inspector.get_columns("videos")}
        if "ix_videos_rights_status" in video_indexes:
            op.drop_index(op.f("ix_videos_rights_status"), table_name="videos")
        if "rights_status" in video_columns:
            op.drop_column("videos", "rights_status")

    if "content_rights" in table_names:
        content_right_indexes = {
            index["name"] for index in inspector.get_indexes("content_rights")
        }
        for index_name in (
            "ix_content_rights_status",
            "ix_content_rights_owner_mid",
            "ix_content_rights_created_at",
            "ix_content_rights_bvid",
        ):
            if index_name in content_right_indexes:
                op.drop_index(op.f(index_name), table_name="content_rights")
        op.drop_table("content_rights")


def downgrade():
    op.create_table(
        "content_rights",
        sa.Column("source_type", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("bvid", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=True),
        sa.Column("owner_mid", sa.BigInteger(), nullable=True),
        sa.Column("grant_type", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("evidence_url", sqlmodel.sql.sqltypes.AutoString(length=1024), nullable=True),
        sa.Column("evidence_note", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("approved_by", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_content_rights_bvid"),
        "content_rights",
        ["bvid"],
        unique=False,
    )
    op.create_index(
        op.f("ix_content_rights_created_at"),
        "content_rights",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_content_rights_owner_mid"),
        "content_rights",
        ["owner_mid"],
        unique=False,
    )
    op.create_index(
        op.f("ix_content_rights_status"),
        "content_rights",
        ["status"],
        unique=False,
    )

    op.add_column(
        "videos",
        sa.Column(
            "rights_status",
            sqlmodel.sql.sqltypes.AutoString(length=32),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.create_index(
        op.f("ix_videos_rights_status"),
        "videos",
        ["rights_status"],
        unique=False,
    )
    op.alter_column("videos", "rights_status", server_default=None)
