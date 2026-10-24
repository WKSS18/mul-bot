"""Store invitation token for recipient inbox delivery."""
from alembic import op
import sqlalchemy as sa

revision = "0005_invitation_tokens"
down_revision = "0004_read_positions"
branch_labels = None
depends_on = None

def upgrade():
    # 0001 builds from the current ORM metadata, so newer installs may already
    # contain this column when the incremental migration is reached.
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("invitations")}
    if "token_plain" not in columns:
        op.add_column("invitations", sa.Column("token_plain", sa.String(length=255), nullable=True))

def downgrade():
    op.drop_column("invitations", "token_plain")
