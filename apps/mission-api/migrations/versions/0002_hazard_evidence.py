"""Hazard event idempotency and evidence storage.

Revision ID: 0002_hazard_evidence
Revises: 0001_initial
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_hazard_evidence"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("events", sa.Column("event_key", sa.String(length=64), nullable=True))
    op.execute("UPDATE events SET event_key = id::text WHERE event_key IS NULL")
    op.alter_column("events", "event_key", nullable=False)
    op.create_index("ix_events_event_key", "events", ["event_key"], unique=True)
    op.add_column("alerts", sa.Column("action", sa.String(length=32), nullable=True))
    op.create_table(
        "event_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("storage_key", sa.String(length=256), nullable=False, unique=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("event_id", "kind", name="uq_event_evidence_kind"),
    )
    op.create_index("ix_event_evidence_event", "event_evidence", ["event_id"])


def downgrade() -> None:
    op.drop_table("event_evidence")
    op.drop_column("alerts", "action")
    op.drop_index("ix_events_event_key", table_name="events")
    op.drop_column("events", "event_key")
