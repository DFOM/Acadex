"""Add is_admin flag

Revision ID: 7c279c649dcc
Revises: 42fd5af6a379
Create Date: 2026-03-17 01:54:10.912365

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7c279c649dcc'
down_revision = '42fd5af6a379'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('users'):
        return

    user_cols = {c['name'] for c in inspector.get_columns('users')}
    if 'is_admin' not in user_cols:
        with op.batch_alter_table('users', schema=None) as batch_op:
            batch_op.add_column(sa.Column('is_admin', sa.Boolean(), nullable=False, server_default=sa.false()))

        # Backfill existing admins based on legacy role column.
        try:
            op.execute(sa.text("UPDATE users SET is_admin = 1 WHERE role = 'admin'"))
        except Exception:
            # If role column doesn't exist or DB doesn't support this SQL, ignore.
            pass

        # Remove server default after backfill so future migrations don't preserve it.
        with op.batch_alter_table('users', schema=None) as batch_op:
            batch_op.alter_column('is_admin', server_default=None)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table('users'):
        return

    user_cols = {c['name'] for c in inspector.get_columns('users')}
    if 'is_admin' in user_cols:
        with op.batch_alter_table('users', schema=None) as batch_op:
            batch_op.drop_column('is_admin')
