"""Add rating dimension columns

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-06-14

"""
from alembic import op
import sqlalchemy as sa

revision = 'a2b3c4d5e6f7'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade():
    # ── CourseRating dimension columns ──
    with op.batch_alter_table('course_ratings') as batch_op:
        batch_op.add_column(sa.Column('content_quality', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('workload', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('usefulness', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('materials_quality', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('difficulty', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('would_take_again', sa.Boolean(), nullable=True))

    # ── FacultyRating dimension columns ──
    with op.batch_alter_table('faculty_ratings') as batch_op:
        batch_op.add_column(sa.Column('teaching_quality', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('communication', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('availability', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('grading_fairness', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('engagement', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('difficulty', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('would_recommend', sa.Boolean(), nullable=True))


def downgrade():
    with op.batch_alter_table('faculty_ratings') as batch_op:
        batch_op.drop_column('would_recommend')
        batch_op.drop_column('difficulty')
        batch_op.drop_column('engagement')
        batch_op.drop_column('grading_fairness')
        batch_op.drop_column('availability')
        batch_op.drop_column('communication')
        batch_op.drop_column('teaching_quality')

    with op.batch_alter_table('course_ratings') as batch_op:
        batch_op.drop_column('would_take_again')
        batch_op.drop_column('difficulty')
        batch_op.drop_column('materials_quality')
        batch_op.drop_column('usefulness')
        batch_op.drop_column('workload')
        batch_op.drop_column('content_quality')
