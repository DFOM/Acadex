"""User email/major/minor and subject area active flag

Revision ID: a7c9d1e2f3b4
Revises: f2a1c3d4e5f6
Create Date: 2026-03-17

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7c9d1e2f3b4'
down_revision = 'f2a1c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table('users'):
        cols = {c['name'] for c in inspector.get_columns('users')}
        with op.batch_alter_table('users', schema=None) as batch_op:
            if 'email' not in cols:
                batch_op.add_column(sa.Column('email', sa.String(length=255), nullable=True))
            if 'major_subject_area_id' not in cols:
                batch_op.add_column(sa.Column('major_subject_area_id', sa.Integer(), nullable=True))
            if 'minor_subject_area_id' not in cols:
                batch_op.add_column(sa.Column('minor_subject_area_id', sa.Integer(), nullable=True))

        # Add unique index for email if missing
        try:
            indexes = {ix['name'] for ix in inspector.get_indexes('users')}
        except Exception:
            indexes = set()
        if op.f('ix_users_email') not in indexes and 'ix_users_email' not in indexes:
            try:
                op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
            except Exception:
                pass

        # Add foreign keys best-effort (sqlite may skip on batch)
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

    if inspector.has_table('subject_areas'):
        cols = {c['name'] for c in inspector.get_columns('subject_areas')}
        with op.batch_alter_table('subject_areas', schema=None) as batch_op:
            if 'is_active' not in cols:
                batch_op.add_column(sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()))

        with op.batch_alter_table('subject_areas', schema=None) as batch_op:
            try:
                batch_op.alter_column('is_active', server_default=None)
            except Exception:
                pass


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table('subject_areas'):
        cols = {c['name'] for c in inspector.get_columns('subject_areas')}
        with op.batch_alter_table('subject_areas', schema=None) as batch_op:
            if 'is_active' in cols:
                batch_op.drop_column('is_active')

    if inspector.has_table('users'):
        try:
            op.drop_index(op.f('ix_users_email'), table_name='users')
        except Exception:
            try:
                op.drop_index('ix_users_email', table_name='users')
            except Exception:
                pass

        cols = {c['name'] for c in inspector.get_columns('users')}
        with op.batch_alter_table('users', schema=None) as batch_op:
            if 'minor_subject_area_id' in cols:
                batch_op.drop_column('minor_subject_area_id')
            if 'major_subject_area_id' in cols:
                batch_op.drop_column('major_subject_area_id')
            if 'email' in cols:
                batch_op.drop_column('email')
