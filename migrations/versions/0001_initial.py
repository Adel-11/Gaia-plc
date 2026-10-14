"""Create project state, users, and import previews."""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("project_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"))
    op.create_table("users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("username"))
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_table("import_previews",
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("state_payload", sa.Text(), nullable=False),
        sa.Column("diff_payload", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token"))
    op.create_index("ix_import_previews_owner_id", "import_previews", ["owner_id"])
    op.create_index("ix_import_previews_expires_at", "import_previews", ["expires_at"])

def downgrade():
    op.drop_index("ix_import_previews_expires_at", table_name="import_previews")
    op.drop_index("ix_import_previews_owner_id", table_name="import_previews")
    op.drop_table("import_previews")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
    op.drop_table("project_state")
