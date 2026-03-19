"""add context columns to dataset registry

Revision ID: f1b2c3d4e5f6
Revises: f0a1b2c3d4e5
Create Date: 2026-03-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'f1b2c3d4e5f6'
down_revision = 'f0a1b2c3d4e5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    if 'dataset_registry' not in insp.get_table_names():
        return

    cols = {c['name'] for c in insp.get_columns('dataset_registry')}

    if 'year' not in cols:
        op.add_column('dataset_registry', sa.Column('year', sa.String(length=20), nullable=True))
    if 'semester' not in cols:
        op.add_column('dataset_registry', sa.Column('semester', sa.String(length=50), nullable=True))
    if 'dataset_type' not in cols:
        op.add_column('dataset_registry', sa.Column('dataset_type', sa.String(length=20), nullable=False, server_default='term'))

    idx_names = {idx['name'] for idx in insp.get_indexes('dataset_registry')}
    if 'ix_dataset_registry_year' not in idx_names:
        op.create_index('ix_dataset_registry_year', 'dataset_registry', ['year'], unique=False)
    if 'ix_dataset_registry_semester' not in idx_names:
        op.create_index('ix_dataset_registry_semester', 'dataset_registry', ['semester'], unique=False)
    if 'ix_dataset_registry_dataset_type' not in idx_names:
        op.create_index('ix_dataset_registry_dataset_type', 'dataset_registry', ['dataset_type'], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    if 'dataset_registry' not in insp.get_table_names():
        return

    for idx_name in [
        'ix_dataset_registry_dataset_type',
        'ix_dataset_registry_semester',
        'ix_dataset_registry_year',
    ]:
        try:
            op.drop_index(idx_name, table_name='dataset_registry')
        except Exception:
            pass

    cols = {c['name'] for c in insp.get_columns('dataset_registry')}
    if 'dataset_type' in cols:
        op.drop_column('dataset_registry', 'dataset_type')
    if 'semester' in cols:
        op.drop_column('dataset_registry', 'semester')
    if 'year' in cols:
        op.drop_column('dataset_registry', 'year')
