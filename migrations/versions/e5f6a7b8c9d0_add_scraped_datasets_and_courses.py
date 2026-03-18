"""Add scraped_datasets and scraped_courses tables.

Revision ID: e5f6a7b8c9d0
Revises: d1e2f3a4b5c6
Create Date: 2026-03-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = 'e5f6a7b8c9d0'
down_revision = 'd1e2f3a4b5c6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = inspect(bind).get_table_names()

    # ── scraped_datasets ─────────────────────────────────────────────
    if 'scraped_datasets' not in existing_tables:
        op.create_table(
            'scraped_datasets',
            sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
            sa.Column('name', sa.String(length=150), nullable=False),
            sa.Column('year', sa.String(length=20), nullable=False),
            sa.Column('semester', sa.String(length=50), nullable=False),
            sa.Column('status', sa.String(length=20), nullable=False, server_default='idle'),
            sa.Column('selected_fields_json', sa.Text(), nullable=True),
            sa.Column('total_courses', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('current_index', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('last_course', sa.String(length=255), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
        )

    # ── scraped_courses ──────────────────────────────────────────────
    if 'scraped_courses' not in existing_tables:
        op.create_table(
            'scraped_courses',
            sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
            sa.Column(
                'dataset_id', sa.Integer(),
                sa.ForeignKey('scraped_datasets.id', ondelete='CASCADE'),
                nullable=False,
            ),
            sa.Column('data_json', sa.Text(), nullable=False, server_default='{}'),
            sa.Column('created_at', sa.DateTime(), nullable=False),
        )
        op.create_index(
            'ix_scraped_courses_dataset_id',
            'scraped_courses',
            ['dataset_id'],
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing_tables = inspect(bind).get_table_names()

    if 'scraped_courses' in existing_tables:
        op.drop_index('ix_scraped_courses_dataset_id', table_name='scraped_courses')
        op.drop_table('scraped_courses')

    if 'scraped_datasets' in existing_tables:
        op.drop_table('scraped_datasets')
