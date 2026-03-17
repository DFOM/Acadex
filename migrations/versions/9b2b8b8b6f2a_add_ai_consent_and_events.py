"""Add AI consent and events

Revision ID: 9b2b8b8b6f2a
Revises: 7c279c649dcc
Create Date: 2026-03-17

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9b2b8b8b6f2a'
down_revision = '7c279c649dcc'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # 1) Per-user AI opt-in flag
    if inspector.has_table('users'):
        user_cols = {c['name'] for c in inspector.get_columns('users')}
        if 'ai_opt_in' not in user_cols:
            with op.batch_alter_table('users', schema=None) as batch_op:
                batch_op.add_column(sa.Column('ai_opt_in', sa.Boolean(), nullable=False, server_default=sa.false()))

            # Remove server default so it doesn't linger in schema diffs.
            with op.batch_alter_table('users', schema=None) as batch_op:
                batch_op.alter_column('ai_opt_in', server_default=None)

    # 2) AI events table (privacy-safe behavioral logging)
    if not inspector.has_table('ai_events'):
        op.create_table(
            'ai_events',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('schedule_id', sa.Integer(), nullable=True),
            sa.Column('event_type', sa.String(length=50), nullable=False),
            sa.Column('route', sa.String(length=200), nullable=True),
            sa.Column('metadata_json', sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(['schedule_id'], ['schedules.id']),
            sa.ForeignKeyConstraint(['user_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_ai_events_user_id'), 'ai_events', ['user_id'], unique=False)
        op.create_index(op.f('ix_ai_events_schedule_id'), 'ai_events', ['schedule_id'], unique=False)
        op.create_index(op.f('ix_ai_events_event_type'), 'ai_events', ['event_type'], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table('ai_events'):
        try:
            op.drop_index(op.f('ix_ai_events_event_type'), table_name='ai_events')
        except Exception:
            pass
        try:
            op.drop_index(op.f('ix_ai_events_schedule_id'), table_name='ai_events')
        except Exception:
            pass
        try:
            op.drop_index(op.f('ix_ai_events_user_id'), table_name='ai_events')
        except Exception:
            pass
        op.drop_table('ai_events')

    if inspector.has_table('users'):
        user_cols = {c['name'] for c in inspector.get_columns('users')}
        if 'ai_opt_in' in user_cols:
            with op.batch_alter_table('users', schema=None) as batch_op:
                batch_op.drop_column('ai_opt_in')
