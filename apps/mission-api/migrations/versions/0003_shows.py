"""music show scores

Revision ID: 0003_shows
Revises: 0002_hazard_evidence
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_shows"; down_revision = "0002_hazard_evidence"; branch_labels = None; depends_on = None
def upgrade():
    op.create_table("shows", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("name", sa.String(128), nullable=False), sa.Column("score", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
def downgrade(): op.drop_table("shows")
