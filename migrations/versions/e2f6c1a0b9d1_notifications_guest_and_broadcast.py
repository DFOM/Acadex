"""Notifications: guest + broadcast support

Revision ID: e2f6c1a0b9d1
Revises: d4e8a1b2c3f4
Create Date: 2026-03-17

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e2f6c1a0b9d1'
down_revision = 'd4e8a1b2c3f4'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('notifications'):
        return

    cols = {c['name']: c for c in inspector.get_columns('notifications')}
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        if 'session_id' not in cols:
            batch_op.add_column(sa.Column('session_id', sa.String(length=255), nullable=True))

        # Allow broadcast (no user_id/session_id) and guest-session notifications.
        if 'user_id' in cols and cols['user_id'].get('nullable') is False:
            batch_op.alter_column('user_id', existing_type=sa.Integer(), nullable=True)

    # Add index for session_id if missing
    try:
        indexes = {ix['name'] for ix in inspector.get_indexes('notifications')}
    except Exception:
        indexes = set()

    if op.f('ix_notifications_session_id') not in indexes and 'ix_notifications_session_id' not in indexes:
        op.create_index(op.f('ix_notifications_session_id'), 'notifications', ['session_id'], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('notifications'):
        return

    # Drop index if present
    try:
        op.drop_index(op.f('ix_notifications_session_id'), table_name='notifications')
    except Exception:
        try:
            op.drop_index('ix_notifications_session_id', table_name='notifications')
        except Exception:
            pass

    cols = {c['name']: c for c in inspector.get_columns('notifications')}
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        if 'session_id' in cols:
            batch_op.drop_column('session_id')

        # Best-effort revert: make user_id non-null again if possible.
        # (May fail if broadcast/session notifications exist.)
        try:
            if 'user_id' in cols and cols['user_id'].get('nullable') is True:
                batch_op.alter_column('user_id', existing_type=sa.Integer(), nullable=False)
        except Exception:
            pass
