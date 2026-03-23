"""Add level to schedules

Revision ID: b8d9e0f1a2b3
Revises: a1b2c3d4e5f7
Create Date: 2026-03-23

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b8d9e0f1a2b3'
down_revision = 'a1b2c3d4e5f7'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('schedules'):
        return

    cols = {c['name'] for c in inspector.get_columns('schedules')}
    indexes = {idx.get('name') for idx in inspector.get_indexes('schedules')}

    with op.batch_alter_table('schedules', schema=None) as batch_op:
        if 'level' not in cols:
            batch_op.add_column(sa.Column('level', sa.Integer(), nullable=True))
        if 'ix_schedules_level' not in indexes:
            batch_op.create_index('ix_schedules_level', ['level'], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('schedules'):
        return

    cols = {c['name'] for c in inspector.get_columns('schedules')}
    indexes = {idx.get('name') for idx in inspector.get_indexes('schedules')}

    with op.batch_alter_table('schedules', schema=None) as batch_op:
        if 'ix_schedules_level' in indexes:
            batch_op.drop_index('ix_schedules_level')
        if 'level' in cols:
            batch_op.drop_column('level')
