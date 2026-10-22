"""Add durable room read positions.

Revision ID: 0004_read_positions
Revises: 0003_bot_test_runs
"""
from alembic import op

from app.db import RoomReadPosition

revision = "0004_read_positions"
down_revision = "0003_bot_test_runs"
branch_labels = None
depends_on = None


def upgrade():
    RoomReadPosition.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade():
    RoomReadPosition.__table__.drop(bind=op.get_bind(), checkfirst=True)
