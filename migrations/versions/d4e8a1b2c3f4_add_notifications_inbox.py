"""Add notifications inbox

Revision ID: d4e8a1b2c3f4
Revises: c1a4f2e9d0b3
Create Date: 2026-03-17

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4e8a1b2c3f4'
down_revision = 'c1a4f2e9d0b3'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('notifications'):
        op.create_table(
            'notifications',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('notif_type', sa.String(length=30), nullable=False),
            sa.Column('title', sa.String(length=200), nullable=False),
            sa.Column('body', sa.Text(), nullable=False),
            sa.Column('url', sa.String(length=300), nullable=True),
            sa.Column('is_read', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('read_at', sa.DateTime(), nullable=True),
            sa.Column('metadata_json', sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(['user_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
        )

        op.create_index(op.f('ix_notifications_user_id'), 'notifications', ['user_id'], unique=False)
        op.create_index(op.f('ix_notifications_notif_type'), 'notifications', ['notif_type'], unique=False)
        op.create_index(op.f('ix_notifications_is_read'), 'notifications', ['is_read'], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table('notifications'):
        for idx in (
            'ix_notifications_is_read',
            'ix_notifications_notif_type',
            'ix_notifications_user_id',
        ):
            try:
                op.drop_index(op.f(idx), table_name='notifications')
            except Exception:
                try:
                    op.drop_index(idx, table_name='notifications')
                except Exception:
                    pass

        op.drop_table('notifications')
