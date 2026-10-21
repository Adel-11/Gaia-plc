"""Add the normalized V2 PLC model and immutable project revisions."""
from alembic import op

# Import registers the V2 ORM tables on the shared backend.db.Base metadata.
from backend import v2_store  # noqa: F401
from backend.db import Base

revision = "0002_v2_model"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    tables = [table for name, table in Base.metadata.tables.items() if name.startswith("v2_")]
    Base.metadata.create_all(bind=op.get_bind(), tables=tables, checkfirst=True)


def downgrade():
    tables = [Base.metadata.tables[name] for name in reversed(list(Base.metadata.tables)) if name.startswith("v2_")]
    Base.metadata.drop_all(bind=op.get_bind(), tables=tables, checkfirst=True)
