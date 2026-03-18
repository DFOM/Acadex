"""add course and faculty management

Revision ID: b3d4e5f6a7b8
Revises: a9b8c7d6e5f4
Create Date: 2026-03-18
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b3d4e5f6a7b8'
down_revision = 'a9b8c7d6e5f4'
branch_labels = None
depends_on = None


def _table_exists(inspector, table_name: str) -> bool:
    try:
        return table_name in inspector.get_table_names()
    except Exception:
        return False


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not _table_exists(insp, 'courses'):
        op.create_table(
            'courses',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('course_number', sa.String(length=120), nullable=False),
            sa.Column('title', sa.String(length=300), nullable=False),
            sa.Column('credits', sa.Float(), nullable=True),
            sa.Column('day_period', sa.String(length=255), nullable=False),
            sa.Column('semester', sa.String(length=50), nullable=True),
            sa.Column('year', sa.String(length=20), nullable=True),
            sa.Column('prerequisite', sa.Text(), nullable=True),
            sa.Column('subject_area', sa.String(length=255), nullable=True),
            sa.Column('instructor', sa.String(length=255), nullable=True),
            sa.Column('source', sa.String(length=20), nullable=False, server_default='scraped'),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index('ix_courses_course_number', 'courses', ['course_number'])
        op.create_index('ix_courses_day_period', 'courses', ['day_period'])
        op.create_index('ix_courses_instructor', 'courses', ['instructor'])
        op.create_index('ix_courses_semester', 'courses', ['semester'])
        op.create_index('ix_courses_source', 'courses', ['source'])
        op.create_index('ix_courses_subject_area', 'courses', ['subject_area'])
        op.create_index('ix_courses_year', 'courses', ['year'])

    if not _table_exists(insp, 'faculty'):
        op.create_table(
            'faculty',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=120), nullable=False),
            sa.Column('email', sa.String(length=120), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.PrimaryKeyConstraint('id')
        )

    if not _table_exists(insp, 'course_faculty'):
        op.create_table(
            'course_faculty',
            sa.Column('course_id', sa.Integer(), nullable=False),
            sa.Column('faculty_id', sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(['course_id'], ['courses.id']),
            sa.ForeignKeyConstraint(['faculty_id'], ['faculty.id']),
            sa.PrimaryKeyConstraint('course_id', 'faculty_id')
        )

    if not _table_exists(insp, 'faculty_advisor_area'):
        op.create_table(
            'faculty_advisor_area',
            sa.Column('faculty_id', sa.Integer(), nullable=False),
            sa.Column('subject_area_id', sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(['faculty_id'], ['faculty.id']),
            sa.ForeignKeyConstraint(['subject_area_id'], ['subject_areas.id']),
            sa.PrimaryKeyConstraint('faculty_id', 'subject_area_id')
        )


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if _table_exists(insp, 'faculty_advisor_area'):
        op.drop_table('faculty_advisor_area')

    if _table_exists(insp, 'course_faculty'):
        op.drop_table('course_faculty')

    if _table_exists(insp, 'faculty'):
        op.drop_table('faculty')

    if _table_exists(insp, 'courses'):
        for idx in [
            'ix_courses_year',
            'ix_courses_subject_area',
            'ix_courses_source',
            'ix_courses_semester',
            'ix_courses_instructor',
            'ix_courses_day_period',
            'ix_courses_course_number',
        ]:
            try:
                op.drop_index(idx, table_name='courses')
            except Exception:
                pass
        op.drop_table('courses')
