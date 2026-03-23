"""Add course instance identity fields to schedule_courses

Revision ID: c2d4e6f8a9b0
Revises: b8d9e0f1a2b3
Create Date: 2026-03-24

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c2d4e6f8a9b0'
down_revision = 'b8d9e0f1a2b3'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('schedule_courses'):
        return

    cols = {c['name'] for c in inspector.get_columns('schedule_courses')}
    indexes = {idx.get('name') for idx in inspector.get_indexes('schedule_courses')}

    # NOTE:
    # These columns are added as nullable in this migration so existing write paths
    # (which currently only persist course_number + grade) do not break mid-rollout.
    # A later migration can enforce stricter NOT NULL constraints after backfill.
    with op.batch_alter_table('schedule_courses', schema=None) as batch_op:
        if 'day_period' not in cols:
            batch_op.add_column(sa.Column('day_period', sa.String(length=255), nullable=True))
        if 'semester' not in cols:
            batch_op.add_column(sa.Column('semester', sa.String(length=50), nullable=True))
        if 'year' not in cols:
            batch_op.add_column(sa.Column('year', sa.String(length=20), nullable=True))
        if 'dataset_name' not in cols:
            batch_op.add_column(sa.Column('dataset_name', sa.String(length=150), nullable=True))
        if 'source' not in cols:
            batch_op.add_column(sa.Column('source', sa.String(length=20), nullable=True))

        if 'ix_schedule_courses_course_term' not in indexes:
            batch_op.create_index(
                'ix_schedule_courses_course_term',
                ['course_number', 'semester', 'year'],
                unique=False,
            )
        if 'ix_schedule_courses_identity' not in indexes:
            batch_op.create_index(
                'ix_schedule_courses_identity',
                ['course_number', 'day_period', 'semester', 'year', 'dataset_name'],
                unique=False,
            )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('schedule_courses'):
        return

    cols = {c['name'] for c in inspector.get_columns('schedule_courses')}
    indexes = {idx.get('name') for idx in inspector.get_indexes('schedule_courses')}

    with op.batch_alter_table('schedule_courses', schema=None) as batch_op:
        if 'ix_schedule_courses_identity' in indexes:
            batch_op.drop_index('ix_schedule_courses_identity')
        if 'ix_schedule_courses_course_term' in indexes:
            batch_op.drop_index('ix_schedule_courses_course_term')

        if 'source' in cols:
            batch_op.drop_column('source')
        if 'dataset_name' in cols:
            batch_op.drop_column('dataset_name')
        if 'year' in cols:
            batch_op.drop_column('year')
        if 'semester' in cols:
            batch_op.drop_column('semester')
        if 'day_period' in cols:
            batch_op.drop_column('day_period')
