"""Heal missing user columns that may not have been applied by prior migration.

If the auto-stamp recovery in app.py stamped the DB to head without running
c9f8a7b6d5e4, the users table will be missing role_id, full_name, and other
profile columns. This migration adds them idempotently via inspect checks.

Revision ID: d1e2f3a4b5c6
Revises: c9f8a7b6d5e4
Create Date: 2026-03-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = 'd1e2f3a4b5c6'
down_revision = 'c9f8a7b6d5e4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    user_cols = {c['name'] for c in inspect(bind).get_columns('users')}

    with op.batch_alter_table('users', schema=None) as batch_op:
        if 'full_name' not in user_cols:
            batch_op.add_column(sa.Column('full_name', sa.String(length=150), nullable=True))
        if 'role_id' not in user_cols:
            batch_op.add_column(sa.Column('role_id', sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                'fk_users_role_id_roles', 'roles', ['role_id'], ['id']
            )
        if 'career_interests' not in user_cols:
            batch_op.add_column(sa.Column('career_interests', sa.Text(), nullable=True))
        if 'extra_profile_json' not in user_cols:
            batch_op.add_column(sa.Column('extra_profile_json', sa.Text(), nullable=True))
        if 'profile_image_path' not in user_cols:
            batch_op.add_column(sa.Column('profile_image_path', sa.String(length=255), nullable=True))
        if 'two_factor_enabled' not in user_cols:
            batch_op.add_column(
                sa.Column('two_factor_enabled', sa.Boolean(), nullable=False,
                          server_default=sa.false())
            )
        if 'two_factor_secret' not in user_cols:
            batch_op.add_column(sa.Column('two_factor_secret', sa.String(length=255), nullable=True))

    # Assign default role_id for any users that don't have one yet.
    # Admins → admin role, everyone else → user role.
    op.execute(
        sa.text("""
            UPDATE users
            SET role_id = (SELECT id FROM roles WHERE name = 'admin')
            WHERE role_id IS NULL
              AND (is_admin = TRUE OR lower(coalesce(role, '')) = 'admin')
              AND EXISTS (SELECT 1 FROM roles WHERE name = 'admin')
        """)
    )
    op.execute(
        sa.text("""
            UPDATE users
            SET role_id = (SELECT id FROM roles WHERE name = 'user')
            WHERE role_id IS NULL
              AND EXISTS (SELECT 1 FROM roles WHERE name = 'user')
        """)
    )


def downgrade() -> None:
    bind = op.get_bind()
    user_cols = {c['name'] for c in inspect(bind).get_columns('users')}

    with op.batch_alter_table('users', schema=None) as batch_op:
        if 'two_factor_secret' in user_cols:
            batch_op.drop_column('two_factor_secret')
        if 'two_factor_enabled' in user_cols:
            batch_op.drop_column('two_factor_enabled')
        if 'profile_image_path' in user_cols:
            batch_op.drop_column('profile_image_path')
        if 'extra_profile_json' in user_cols:
            batch_op.drop_column('extra_profile_json')
        if 'career_interests' in user_cols:
            batch_op.drop_column('career_interests')
        if 'role_id' in user_cols:
            batch_op.drop_constraint('fk_users_role_id_roles', type_='foreignkey')
            batch_op.drop_column('role_id')
        if 'full_name' in user_cols:
            batch_op.drop_column('full_name')
