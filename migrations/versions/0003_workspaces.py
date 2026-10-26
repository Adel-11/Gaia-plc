"""Named workspaces, shared types, users and semantic version history."""
from alembic import op
import sqlalchemy as sa
from backend.db import Base
from backend import v2_workspace
revision='0003_workspaces'
down_revision='0002_v2_model'
branch_labels=None
depends_on=None

def upgrade():
    bind=op.get_bind()
    def missing(table,name):return name not in {c['name'] for c in sa.inspect(bind).get_columns(table)}
    if missing('users','is_admin'):
        op.add_column('users',sa.Column('is_admin',sa.Boolean(),nullable=False,server_default=sa.true()))
    if missing('v2_project','name'):op.add_column('v2_project',sa.Column('name',sa.String(128),nullable=False,server_default='Gaia'))
    if missing('v2_project_revision','project_id'):op.add_column('v2_project_revision',sa.Column('project_id',sa.Integer(),nullable=False,server_default='1'))
    Base.metadata.create_all(bind,tables=[v2_workspace.SharedCatalog.__table__,v2_workspace.WorkspaceCounter.__table__,v2_workspace.VersionEvent.__table__],checkfirst=True)

def downgrade():
    raise RuntimeError('Restore the database backup to roll back the workspace migration safely')
