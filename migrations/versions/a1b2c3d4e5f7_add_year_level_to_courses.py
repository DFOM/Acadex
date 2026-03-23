"""Add year_level to courses

Revision ID: a1b2c3d4e5f7
Revises: f9a0b1c2d3e4
Create Date: 2026-03-23

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f7'
down_revision = 'f9a0b1c2d3e4'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('courses'):
        return

    cols = {c['name'] for c in inspector.get_columns('courses')}
    indexes = {idx.get('name') for idx in inspector.get_indexes('courses')}

    with op.batch_alter_table('courses', schema=None) as batch_op:
        if 'year_level' not in cols:
            batch_op.add_column(sa.Column('year_level', sa.Integer(), nullable=True))

        if 'ix_courses_year_level' not in indexes:
            batch_op.create_index('ix_courses_year_level', ['year_level'], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('courses'):
        return

    cols = {c['name'] for c in inspector.get_columns('courses')}
    indexes = {idx.get('name') for idx in inspector.get_indexes('courses')}

    with op.batch_alter_table('courses', schema=None) as batch_op:
        if 'ix_courses_year_level' in indexes:
            batch_op.drop_index('ix_courses_year_level')
        if 'year_level' in cols:
            batch_op.drop_column('year_level')
