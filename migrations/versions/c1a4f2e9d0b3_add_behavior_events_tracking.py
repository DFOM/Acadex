"""Add behavior events tracking

Revision ID: c1a4f2e9d0b3
Revises: b7f1c2d3e4f5
Create Date: 2026-03-17

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c1a4f2e9d0b3'
down_revision = 'b7f1c2d3e4f5'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Always-on behavior event logging (privacy-safe; supports guest sessions)
    if not inspector.has_table('behavior_events'):
        op.create_table(
            'behavior_events',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=True),
            sa.Column('session_id', sa.String(length=255), nullable=True),
            sa.Column('schedule_id', sa.Integer(), nullable=True),
            sa.Column('event_type', sa.String(length=50), nullable=False),
            sa.Column('route', sa.String(length=200), nullable=True),
            sa.Column('metadata_json', sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(['schedule_id'], ['schedules.id']),
            sa.ForeignKeyConstraint(['user_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
        )

        op.create_index(op.f('ix_behavior_events_user_id'), 'behavior_events', ['user_id'], unique=False)
        op.create_index(op.f('ix_behavior_events_session_id'), 'behavior_events', ['session_id'], unique=False)
        op.create_index(op.f('ix_behavior_events_schedule_id'), 'behavior_events', ['schedule_id'], unique=False)
        op.create_index(op.f('ix_behavior_events_event_type'), 'behavior_events', ['event_type'], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table('behavior_events'):
        for idx in (
            'ix_behavior_events_event_type',
            'ix_behavior_events_schedule_id',
            'ix_behavior_events_session_id',
            'ix_behavior_events_user_id',
        ):
            try:
                op.drop_index(op.f(idx), table_name='behavior_events')
            except Exception:
                # Some backends may not use naming convention.
                try:
                    op.drop_index(idx, table_name='behavior_events')
                except Exception:
                    pass

        op.drop_table('behavior_events')
