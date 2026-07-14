"""Voice conversation audit tables.

Revision ID: 0004_conversations
Revises: 0003_shows
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_conversations"
down_revision = "0003_shows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("robot_id", sa.String(length=64), sa.ForeignKey("robots.id"), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_conversation_sessions_robot", "conversation_sessions", ["robot_id"])
    op.create_index("ix_conversation_sessions_state", "conversation_sessions", ["state"])
    op.create_table(
        "conversation_turns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversation_sessions.id"), nullable=False),
        sa.Column("robot_id", sa.String(length=64), sa.ForeignKey("robots.id"), nullable=False),
        sa.Column("transcript", sa.Text(), nullable=False),
        sa.Column("reply", sa.Text(), nullable=True),
        sa.Column("tool_calls", postgresql.JSONB(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_conversation_turns_session", "conversation_turns", ["session_id"])
    op.create_index("ix_conversation_turns_robot", "conversation_turns", ["robot_id"])


def downgrade() -> None:
    op.drop_table("conversation_turns")
    op.drop_table("conversation_sessions")
