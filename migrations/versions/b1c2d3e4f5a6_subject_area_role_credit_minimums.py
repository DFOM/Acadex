"""Add subject area role-specific credit minimums

Revision ID: b1c2d3e4f5a6
Revises: a7c9d1e2f3b4
Create Date: 2026-03-17

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b1c2d3e4f5a6'
down_revision = 'a7c9d1e2f3b4'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('subject_areas'):
        return

    cols = {c['name'] for c in inspector.get_columns('subject_areas')}
    with op.batch_alter_table('subject_areas', schema=None) as batch_op:
        if 'min_major_credits' not in cols:
            batch_op.add_column(sa.Column('min_major_credits', sa.Float(), nullable=False, server_default='0'))
        if 'min_minor_credits' not in cols:
            batch_op.add_column(sa.Column('min_minor_credits', sa.Float(), nullable=False, server_default='0'))

    # Backfill from existing benchmark where available.
    try:
        op.execute(sa.text("UPDATE subject_areas SET min_major_credits = COALESCE(min_depth_credits, 0) WHERE area_type IN ('major','both')"))
        op.execute(sa.text("UPDATE subject_areas SET min_minor_credits = COALESCE(min_depth_credits, 0) WHERE area_type IN ('minor','both')"))
    except Exception:
        pass

    with op.batch_alter_table('subject_areas', schema=None) as batch_op:
        try:
            batch_op.alter_column('min_major_credits', server_default=None)
        except Exception:
            pass
        try:
            batch_op.alter_column('min_minor_credits', server_default=None)
        except Exception:
            pass


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('subject_areas'):
        return

    cols = {c['name'] for c in inspector.get_columns('subject_areas')}
    with op.batch_alter_table('subject_areas', schema=None) as batch_op:
        if 'min_minor_credits' in cols:
            batch_op.drop_column('min_minor_credits')
        if 'min_major_credits' in cols:
            batch_op.drop_column('min_major_credits')
