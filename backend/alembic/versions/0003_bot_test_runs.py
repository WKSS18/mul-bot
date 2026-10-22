"""Add independent Bot test runs.

Revision ID: 0003_bot_test_runs
Revises: 0002_room_archive
"""
from alembic import op

from app.db import BotTestRun

revision = "0003_bot_test_runs"
down_revision = "0002_room_archive"
branch_labels = None
depends_on = None


def upgrade():
    BotTestRun.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade():
    BotTestRun.__table__.drop(bind=op.get_bind(), checkfirst=True)
