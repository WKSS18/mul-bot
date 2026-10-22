"""Add room archive state to earlier development databases.

Revision ID: 0002_room_archive
Revises: 0001_initial
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_room_archive"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("rooms")}
    if "archived_at" not in columns:
        op.add_column("rooms", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("rooms")}
    if "archived_at" in columns:
        op.drop_column("rooms", "archived_at")
