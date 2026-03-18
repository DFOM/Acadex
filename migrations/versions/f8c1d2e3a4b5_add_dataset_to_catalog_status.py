"""Add dataset column to catalog_statuses.

Revision ID: f8c1d2e3a4b5
Revises: e5f6a7b8c9d0
Create Date: 2026-03-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = 'f8c1d2e3a4b5'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = inspector.get_table_names()
    if 'catalog_statuses' not in tables:
        return

    cols = {c['name'] for c in inspector.get_columns('catalog_statuses')}
    if 'dataset' not in cols:
        op.add_column('catalog_statuses', sa.Column('dataset', sa.String(length=150), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = inspector.get_table_names()
    if 'catalog_statuses' not in tables:
        return

    cols = {c['name'] for c in inspector.get_columns('catalog_statuses')}
    if 'dataset' in cols:
        op.drop_column('catalog_statuses', 'dataset')
