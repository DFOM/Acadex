"""add dataset registry for unified control

Revision ID: f0a1b2c3d4e5
Revises: c6d7e8f9a0b1
Create Date: 2026-03-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'f0a1b2c3d4e5'
down_revision = 'c6d7e8f9a0b1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    if 'dataset_registry' not in insp.get_table_names():
        op.create_table(
            'dataset_registry',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=150), nullable=False),
            sa.Column('source', sa.String(length=20), nullable=False),
            sa.Column('table_name', sa.String(length=150), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('table_name'),
        )
        op.create_index('ix_dataset_registry_name', 'dataset_registry', ['name'], unique=False)
        op.create_index('ix_dataset_registry_source', 'dataset_registry', ['source'], unique=False)
        op.create_index('ix_dataset_registry_created_at', 'dataset_registry', ['created_at'], unique=False)
        op.create_index('ix_dataset_registry_is_active', 'dataset_registry', ['is_active'], unique=False)
        op.create_index('ix_dataset_registry_table_name', 'dataset_registry', ['table_name'], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    if 'dataset_registry' not in insp.get_table_names():
        return

    for idx_name in [
        'ix_dataset_registry_table_name',
        'ix_dataset_registry_is_active',
        'ix_dataset_registry_created_at',
        'ix_dataset_registry_source',
        'ix_dataset_registry_name',
    ]:
        try:
            op.drop_index(idx_name, table_name='dataset_registry')
        except Exception:
            pass
    op.drop_table('dataset_registry')
