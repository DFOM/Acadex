"""Add RBAC, profile fields, registration config, and login attempts.

Revision ID: c9f8a7b6d5e4
Revises: b1c2d3e4f5a6
Create Date: 2026-03-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'c9f8a7b6d5e4'
down_revision = 'a54785eaba7a'
branch_labels = None
depends_on = None


DEFAULT_PERMISSIONS = [
    ('view_analytics', 'View analytics', 'View analytics dashboards and exports'),
    ('edit_courses', 'Edit courses', 'Edit courses, instructors, and periods'),
    ('manage_datasets', 'Manage datasets', 'Upload/delete course datasets'),
    ('manage_users', 'Manage users', 'Manage users, roles, and registration fields'),
    ('control_ai', 'Control AI features', 'Manage AI dashboards and settings'),
    ('edit_landing_page', 'Edit landing page', 'Manage announcements and landing content'),
]

DEFAULT_PERMISSION_ROWS = [
    {'key': key, 'label': label, 'description': description}
    for (key, label, description) in DEFAULT_PERMISSIONS
]


ROLE_PERMISSION_KEYS = {
    'admin': [p[0] for p in DEFAULT_PERMISSIONS],
    'moderator': ['view_analytics', 'edit_landing_page'],
    'user': [],
}


def _table_exists(insp, table_name: str) -> bool:
    return table_name in insp.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    if not _table_exists(insp, 'roles'):
        op.create_table(
            'roles',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=80), nullable=False),
            sa.Column('description', sa.String(length=255), nullable=True),
            sa.Column('is_system', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('name'),
        )

    if not _table_exists(insp, 'permissions'):
        op.create_table(
            'permissions',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('key', sa.String(length=80), nullable=False),
            sa.Column('label', sa.String(length=120), nullable=False),
            sa.Column('description', sa.String(length=255), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('key'),
        )

    if not _table_exists(insp, 'role_permissions'):
        op.create_table(
            'role_permissions',
            sa.Column('role_id', sa.Integer(), nullable=False),
            sa.Column('permission_id', sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(['permission_id'], ['permissions.id']),
            sa.ForeignKeyConstraint(['role_id'], ['roles.id']),
            sa.PrimaryKeyConstraint('role_id', 'permission_id'),
        )

    if not _table_exists(insp, 'registration_fields'):
        op.create_table(
            'registration_fields',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('field_key', sa.String(length=80), nullable=False),
            sa.Column('label', sa.String(length=120), nullable=False),
            sa.Column('input_type', sa.String(length=30), nullable=False, server_default='text'),
            sa.Column('options_json', sa.Text(), nullable=True),
            sa.Column('is_required', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('display_order', sa.Integer(), nullable=False, server_default='0'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('field_key'),
        )

    if not _table_exists(insp, 'login_attempts'):
        op.create_table(
            'login_attempts',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('identity', sa.String(length=255), nullable=False),
            sa.Column('ip_address', sa.String(length=64), nullable=True),
            sa.Column('success', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('attempted_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_login_attempts_identity'), 'login_attempts', ['identity'], unique=False)
        op.create_index(op.f('ix_login_attempts_ip_address'), 'login_attempts', ['ip_address'], unique=False)
        op.create_index(op.f('ix_login_attempts_success'), 'login_attempts', ['success'], unique=False)
        op.create_index(op.f('ix_login_attempts_attempted_at'), 'login_attempts', ['attempted_at'], unique=False)

    user_cols = {c['name'] for c in inspect(bind).get_columns('users')}
    with op.batch_alter_table('users', schema=None) as batch_op:
        if 'email' not in user_cols:
            batch_op.add_column(sa.Column('email', sa.String(length=255), nullable=True))
        if 'is_admin' not in user_cols:
            batch_op.add_column(sa.Column('is_admin', sa.Boolean(), nullable=False, server_default=sa.false()))
        if 'full_name' not in user_cols:
            batch_op.add_column(sa.Column('full_name', sa.String(length=150), nullable=True))
        if 'role_id' not in user_cols:
            batch_op.add_column(sa.Column('role_id', sa.Integer(), nullable=True))
            try:
                batch_op.create_foreign_key('fk_users_role_id_roles', 'roles', ['role_id'], ['id'])
            except Exception:
                pass
        if 'major_subject_area_id' not in user_cols:
            batch_op.add_column(sa.Column('major_subject_area_id', sa.Integer(), nullable=True))
        if 'minor_subject_area_id' not in user_cols:
            batch_op.add_column(sa.Column('minor_subject_area_id', sa.Integer(), nullable=True))
        if 'career_interests' not in user_cols:
            batch_op.add_column(sa.Column('career_interests', sa.Text(), nullable=True))
        if 'extra_profile_json' not in user_cols:
            batch_op.add_column(sa.Column('extra_profile_json', sa.Text(), nullable=True))
        if 'profile_image_path' not in user_cols:
            batch_op.add_column(sa.Column('profile_image_path', sa.String(length=255), nullable=True))
        if 'two_factor_enabled' not in user_cols:
            batch_op.add_column(sa.Column('two_factor_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
        if 'two_factor_secret' not in user_cols:
            batch_op.add_column(sa.Column('two_factor_secret', sa.String(length=255), nullable=True))

    # Best-effort compatibility fixes for mixed historical schemas.
    try:
        with op.batch_alter_table('users', schema=None) as batch_op:
            batch_op.create_foreign_key('fk_users_major_subject_area_id', 'subject_areas', ['major_subject_area_id'], ['id'])
    except Exception:
        pass
    try:
        with op.batch_alter_table('users', schema=None) as batch_op:
            batch_op.create_foreign_key('fk_users_minor_subject_area_id', 'subject_areas', ['minor_subject_area_id'], ['id'])
    except Exception:
        pass

    try:
        indexes = {ix['name'] for ix in inspect(bind).get_indexes('users')}
    except Exception:
        indexes = set()
    if op.f('ix_users_email') not in indexes and 'ix_users_email' not in indexes and 'email' in {c['name'] for c in inspect(bind).get_columns('users')}:
        try:
            op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
        except Exception:
            pass

    try:
        cat_cols = {c['name'] for c in inspect(bind).get_columns('catalog_statuses')}
        if 'dataset' not in cat_cols:
            with op.batch_alter_table('catalog_statuses', schema=None) as batch_op:
                batch_op.add_column(sa.Column('dataset', sa.String(length=150), nullable=True))
    except Exception:
        pass

    op.execute(sa.text("""
        INSERT INTO permissions (key, label, description)
        SELECT :key, :label, :description
        WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE key = :key)
    """), DEFAULT_PERMISSION_ROWS)

    role_rows = [
        {'name': 'admin', 'description': 'Full administrative access', 'is_system': True},
        {'name': 'moderator', 'description': 'Limited admin access', 'is_system': True},
        {'name': 'user', 'description': 'Default user role', 'is_system': True},
    ]
    op.execute(sa.text("""
        INSERT INTO roles (name, description, is_system)
        SELECT :name, :description, :is_system
        WHERE NOT EXISTS (SELECT 1 FROM roles WHERE name = :name)
    """), role_rows)

    for role_name, perm_keys in ROLE_PERMISSION_KEYS.items():
        for perm_key in perm_keys:
            op.execute(
                sa.text(
                    """
                    INSERT INTO role_permissions (role_id, permission_id)
                    SELECT r.id, p.id
                    FROM roles r, permissions p
                    WHERE r.name = :role_name
                      AND p.key = :perm_key
                      AND NOT EXISTS (
                        SELECT 1
                        FROM role_permissions rp
                        WHERE rp.role_id = r.id AND rp.permission_id = p.id
                      )
                    """
                ),
                {'role_name': role_name, 'perm_key': perm_key},
            )

    # Assign default role_id values.  Wrap in try/except for DB-dialect safety
    # (SQLite stores booleans as 0/1; PostgreSQL uses TRUE/FALSE).
    try:
        op.execute(
            sa.text(
                """
                UPDATE users
                SET role_id = (SELECT id FROM roles WHERE name = 'admin')
                WHERE (is_admin = TRUE OR lower(coalesce(role, '')) = 'admin')
                  AND role_id IS NULL
                """
            )
        )
    except Exception:
        pass  # Non-critical; _seed_security_defaults() handles role assignment at startup.

    try:
        op.execute(
            sa.text(
                """
                UPDATE users
                SET role_id = (SELECT id FROM roles WHERE name = 'user')
                WHERE role_id IS NULL
                """
            )
        )
    except Exception:
        pass

    reg_rows = [
        {'field_key': 'career_interests', 'label': 'Career Interests', 'input_type': 'textarea', 'options_json': None, 'is_required': False, 'is_active': True, 'display_order': 10},
        {'field_key': 'gender', 'label': 'Gender', 'input_type': 'select', 'options_json': '["Male", "Female", "Prefer not to say"]', 'is_required': False, 'is_active': True, 'display_order': 20},
    ]
    op.execute(sa.text("""
        INSERT INTO registration_fields (field_key, label, input_type, options_json, is_required, is_active, display_order)
        SELECT :field_key, :label, :input_type, :options_json, :is_required, :is_active, :display_order
        WHERE NOT EXISTS (SELECT 1 FROM registration_fields WHERE field_key = :field_key)
    """), reg_rows)


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    user_cols = {c['name'] for c in inspect(bind).get_columns('users')}
    with op.batch_alter_table('users', schema=None) as batch_op:
        if 'two_factor_secret' in user_cols:
            batch_op.drop_column('two_factor_secret')
        if 'two_factor_enabled' in user_cols:
            batch_op.drop_column('two_factor_enabled')
        if 'profile_image_path' in user_cols:
            batch_op.drop_column('profile_image_path')
        if 'career_interests' in user_cols:
            batch_op.drop_column('career_interests')
        if 'extra_profile_json' in user_cols:
            batch_op.drop_column('extra_profile_json')
        if 'role_id' in user_cols:
            batch_op.drop_constraint('fk_users_role_id_roles', type_='foreignkey')
            batch_op.drop_column('role_id')
        if 'full_name' in user_cols:
            batch_op.drop_column('full_name')

    if _table_exists(insp, 'login_attempts'):
        op.drop_index(op.f('ix_login_attempts_attempted_at'), table_name='login_attempts')
        op.drop_index(op.f('ix_login_attempts_success'), table_name='login_attempts')
        op.drop_index(op.f('ix_login_attempts_ip_address'), table_name='login_attempts')
        op.drop_index(op.f('ix_login_attempts_identity'), table_name='login_attempts')
        op.drop_table('login_attempts')

    if _table_exists(insp, 'registration_fields'):
        op.drop_table('registration_fields')

    if _table_exists(insp, 'role_permissions'):
        op.drop_table('role_permissions')

    if _table_exists(insp, 'permissions'):
        op.drop_table('permissions')

    if _table_exists(insp, 'roles'):
        op.drop_table('roles')
