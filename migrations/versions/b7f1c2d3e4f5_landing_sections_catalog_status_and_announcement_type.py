"""Landing sections, catalog status, announcement types

Revision ID: b7f1c2d3e4f5
Revises: 9b2b8b8b6f2a
Create Date: 2026-03-17

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7f1c2d3e4f5'
down_revision = '9b2b8b8b6f2a'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # 1) Extend announcements: type + updated_at
    if inspector.has_table('announcements'):
        ann_cols = {c['name'] for c in inspector.get_columns('announcements')}
        with op.batch_alter_table('announcements', schema=None) as batch_op:
            if 'ann_type' not in ann_cols:
                batch_op.add_column(sa.Column('ann_type', sa.String(length=20), nullable=False, server_default='info'))
            if 'updated_at' not in ann_cols:
                batch_op.add_column(sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')))

        # Backfill updated_at with date_posted where possible.
        try:
            op.execute(sa.text("UPDATE announcements SET updated_at = COALESCE(updated_at, date_posted)"))
        except Exception:
            pass

    # 2) Landing page sections
    if not inspector.has_table('landing_page_sections'):
        op.create_table(
            'landing_page_sections',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=50), nullable=False),
            sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('display_order', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('content_json', sa.Text(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('name'),
        )

    # 3) Course catalog statuses
    if not inspector.has_table('catalog_statuses'):
        op.create_table(
            'catalog_statuses',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('term', sa.String(length=100), nullable=False),
            sa.Column('status', sa.String(length=20), nullable=False, server_default='available'),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('term'),
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table('catalog_statuses'):
        op.drop_table('catalog_statuses')

    if inspector.has_table('landing_page_sections'):
        op.drop_table('landing_page_sections')

    if inspector.has_table('announcements'):
        ann_cols = {c['name'] for c in inspector.get_columns('announcements')}
        with op.batch_alter_table('announcements', schema=None) as batch_op:
            if 'updated_at' in ann_cols:
                batch_op.drop_column('updated_at')
            if 'ann_type' in ann_cols:
                batch_op.drop_column('ann_type')
