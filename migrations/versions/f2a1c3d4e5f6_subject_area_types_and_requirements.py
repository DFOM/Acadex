"""Subject area types and requirement controls

Revision ID: f2a1c3d4e5f6
Revises: e2f6c1a0b9d1
Create Date: 2026-03-17

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f2a1c3d4e5f6'
down_revision = 'e2f6c1a0b9d1'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('subject_areas'):
        return

    cols = {c['name'] for c in inspector.get_columns('subject_areas')}
    with op.batch_alter_table('subject_areas', schema=None) as batch_op:
        if 'area_type' not in cols:
            batch_op.add_column(sa.Column('area_type', sa.String(length=20), nullable=False, server_default='generic'))
        if 'is_required' not in cols:
            batch_op.add_column(sa.Column('is_required', sa.Boolean(), nullable=False, server_default=sa.false()))
        if 'required_for_paths' not in cols:
            batch_op.add_column(sa.Column('required_for_paths', sa.Text(), nullable=True))

    # Normalize existing rows and remove server defaults.
    try:
        op.execute(sa.text("UPDATE subject_areas SET area_type = 'generic' WHERE area_type IS NULL OR TRIM(area_type) = ''"))
        op.execute(sa.text("UPDATE subject_areas SET area_type = LOWER(area_type)"))
        op.execute(sa.text("UPDATE subject_areas SET area_type = 'generic' WHERE area_type NOT IN ('major','minor','generic')"))
    except Exception:
        pass

    with op.batch_alter_table('subject_areas', schema=None) as batch_op:
        try:
            batch_op.alter_column('area_type', server_default=None)
        except Exception:
            pass
        try:
            batch_op.alter_column('is_required', server_default=None)
        except Exception:
            pass


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('subject_areas'):
        return

    cols = {c['name'] for c in inspector.get_columns('subject_areas')}
    with op.batch_alter_table('subject_areas', schema=None) as batch_op:
        if 'required_for_paths' in cols:
            batch_op.drop_column('required_for_paths')
        if 'is_required' in cols:
            batch_op.drop_column('is_required')
        if 'area_type' in cols:
            batch_op.drop_column('area_type')
