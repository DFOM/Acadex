"""add dataset_name and last_updated to courses

Revision ID: c6d7e8f9a0b1
Revises: b3d4e5f6a7b8
Create Date: 2026-03-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'c6d7e8f9a0b1'
down_revision = 'b3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    if 'courses' not in insp.get_table_names():
        return

    cols = {c['name'] for c in insp.get_columns('courses')}

    if 'dataset_name' not in cols:
        op.add_column('courses', sa.Column('dataset_name', sa.String(length=150), nullable=True))
    if 'last_updated' not in cols:
        op.add_column(
            'courses',
            sa.Column('last_updated', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )

    insp = inspect(bind)
    indexes = {idx['name'] for idx in insp.get_indexes('courses')}
    if 'ix_courses_dataset_name' not in indexes:
        op.create_index('ix_courses_dataset_name', 'courses', ['dataset_name'])

    op.execute(sa.text('UPDATE courses SET last_updated = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)'))


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    if 'courses' not in insp.get_table_names():
        return

    indexes = {idx['name'] for idx in insp.get_indexes('courses')}
    if 'ix_courses_dataset_name' in indexes:
        op.drop_index('ix_courses_dataset_name', table_name='courses')

    cols = {c['name'] for c in insp.get_columns('courses')}
    if 'last_updated' in cols:
        op.drop_column('courses', 'last_updated')
    if 'dataset_name' in cols:
        op.drop_column('courses', 'dataset_name')
