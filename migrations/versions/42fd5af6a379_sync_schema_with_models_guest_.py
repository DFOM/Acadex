"""Sync schema with models (guest+announcements)

Revision ID: 42fd5af6a379
Revises: fb071ac05963
Create Date: 2026-03-17 01:45:12.627563

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '42fd5af6a379'
down_revision = 'fb071ac05963'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # 1) Announcements table (landing page)
    if not inspector.has_table('announcements'):
        op.create_table(
            'announcements',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('title', sa.String(length=150), nullable=False),
            sa.Column('content', sa.Text(), nullable=False),
            sa.Column('date_posted', sa.DateTime(), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )

    # 2) Guest schedules: schedules.user_id nullable + schedules.session_id
    if inspector.has_table('schedules'):
        schedule_cols = {c['name'] for c in inspector.get_columns('schedules')}
        with op.batch_alter_table('schedules', schema=None) as batch_op:
            if 'user_id' in schedule_cols:
                batch_op.alter_column('user_id', existing_type=sa.Integer(), nullable=True)
            if 'session_id' not in schedule_cols:
                batch_op.add_column(sa.Column('session_id', sa.String(length=255), nullable=True))

    # 3) Settings value: allow long landing-page text
    if inspector.has_table('settings'):
        settings_cols = {c['name'] for c in inspector.get_columns('settings')}
        if 'value' in settings_cols:
            with op.batch_alter_table('settings', schema=None) as batch_op:
                batch_op.alter_column(
                    'value',
                    existing_type=sa.String(length=255),
                    type_=sa.Text(),
                    existing_nullable=True,
                )

    # 4) User demographics: add expected_graduation (registration form + model)
    if inspector.has_table('users'):
        user_cols = {c['name'] for c in inspector.get_columns('users')}
        if 'expected_graduation' not in user_cols:
            with op.batch_alter_table('users', schema=None) as batch_op:
                batch_op.add_column(sa.Column('expected_graduation', sa.Integer(), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table('users'):
        user_cols = {c['name'] for c in inspector.get_columns('users')}
        if 'expected_graduation' in user_cols:
            with op.batch_alter_table('users', schema=None) as batch_op:
                batch_op.drop_column('expected_graduation')

    if inspector.has_table('settings'):
        settings_cols = {c['name'] for c in inspector.get_columns('settings')}
        if 'value' in settings_cols:
            with op.batch_alter_table('settings', schema=None) as batch_op:
                batch_op.alter_column(
                    'value',
                    existing_type=sa.Text(),
                    type_=sa.String(length=255),
                    existing_nullable=True,
                )

    if inspector.has_table('schedules'):
        schedule_cols = {c['name'] for c in inspector.get_columns('schedules')}
        with op.batch_alter_table('schedules', schema=None) as batch_op:
            if 'session_id' in schedule_cols:
                batch_op.drop_column('session_id')
            if 'user_id' in schedule_cols:
                batch_op.alter_column('user_id', existing_type=sa.Integer(), nullable=False)

    if inspector.has_table('announcements'):
        op.drop_table('announcements')
