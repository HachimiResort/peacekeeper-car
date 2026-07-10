"""Initial mission-api schema.

Revision ID: 0001_initial
Revises:
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "robots",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("base_url", sa.String(length=512), nullable=False, unique=True),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("capabilities", postgresql.JSONB(), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "missions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("mission_type", sa.String(length=64), nullable=False),
        sa.Column("robot_id", sa.String(length=64), sa.ForeignKey("robots.id"), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("request_payload", postgresql.JSONB(), nullable=False),
        sa.Column("result_payload", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_missions_type", "missions", ["mission_type"])
    op.create_index("ix_missions_robot", "missions", ["robot_id"])
    op.create_index("ix_missions_state", "missions", ["state"])
    op.create_table(
        "events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("robot_id", sa.String(length=64), sa.ForeignKey("robots.id"), nullable=False),
        sa.Column("mission_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("missions.id"), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_events_robot", "events", ["robot_id"])
    op.create_index("ix_events_type", "events", ["event_type"])
    op.create_table(
        "alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id"), unique=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("confirmed_by", sa.String(length=128), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=True),
    )
    op.create_table(
        "maps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("logical_name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("yaml_sha256", sa.String(length=64), nullable=False),
        sa.Column("image_sha256", sa.String(length=64), nullable=False),
        sa.Column("bundle_sha256", sa.String(length=64), nullable=False),
        sa.Column("resolution", sa.Float(), nullable=False),
        sa.Column("origin", postgresql.JSONB(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=256), nullable=False, unique=True),
        sa.Column("source_robot_id", sa.String(length=64), sa.ForeignKey("robots.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("logical_name", "version", name="uq_map_name_version"),
        sa.UniqueConstraint("logical_name", "bundle_sha256", name="uq_map_name_bundle"),
    )
    op.create_index("ix_maps_name", "maps", ["logical_name"])
    op.create_index("ix_maps_hash", "maps", ["bundle_sha256"])
    op.create_table(
        "map_deployments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("map_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("maps.id"), nullable=False),
        sa.Column("robot_id", sa.String(length=64), sa.ForeignKey("robots.id"), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("installed_name", sa.String(length=192), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_deployments_map", "map_deployments", ["map_id"])
    op.create_index("ix_deployments_robot", "map_deployments", ["robot_id"])


def downgrade() -> None:
    op.drop_table("map_deployments")
    op.drop_table("maps")
    op.drop_table("alerts")
    op.drop_table("events")
    op.drop_table("missions")
    op.drop_table("robots")
