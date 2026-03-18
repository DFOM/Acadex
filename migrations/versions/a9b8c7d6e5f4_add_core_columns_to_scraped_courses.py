"""add core columns to scraped_courses

Revision ID: a9b8c7d6e5f4
Revises: f8c1d2e3a4b5
Create Date: 2026-03-18
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a9b8c7d6e5f4'
down_revision = 'f8c1d2e3a4b5'
branch_labels = None
depends_on = None


def _has_column(inspector, table_name: str, col_name: str) -> bool:
    try:
        cols = {c['name'] for c in inspector.get_columns(table_name)}
        return col_name in cols
    except Exception:
        return False


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not _has_column(insp, 'scraped_courses', 'course_number'):
        op.add_column('scraped_courses', sa.Column('course_number', sa.String(length=120), nullable=True))
    if not _has_column(insp, 'scraped_courses', 'course_title'):
        op.add_column('scraped_courses', sa.Column('course_title', sa.String(length=300), nullable=True))
    if not _has_column(insp, 'scraped_courses', 'credits'):
        op.add_column('scraped_courses', sa.Column('credits', sa.Float(), nullable=True))
    if not _has_column(insp, 'scraped_courses', 'instructor'):
        op.add_column('scraped_courses', sa.Column('instructor', sa.String(length=255), nullable=True))
    if not _has_column(insp, 'scraped_courses', 'day_and_period'):
        op.add_column('scraped_courses', sa.Column('day_and_period', sa.String(length=255), nullable=True))
    if not _has_column(insp, 'scraped_courses', 'area_of_study'):
        op.add_column('scraped_courses', sa.Column('area_of_study', sa.String(length=255), nullable=True))
    if not _has_column(insp, 'scraped_courses', 'prerequisite'):
        op.add_column('scraped_courses', sa.Column('prerequisite', sa.Text(), nullable=True))
    if not _has_column(insp, 'scraped_courses', 'additional_attributes_json'):
        op.add_column('scraped_courses', sa.Column('additional_attributes_json', sa.Text(), nullable=True))

    # indexes
    existing_indexes = {idx['name'] for idx in insp.get_indexes('scraped_courses')}
    if 'ix_scraped_courses_course_number' not in existing_indexes:
        op.create_index('ix_scraped_courses_course_number', 'scraped_courses', ['course_number'])
    if 'ix_scraped_courses_course_title' not in existing_indexes:
        op.create_index('ix_scraped_courses_course_title', 'scraped_courses', ['course_title'])
    if 'ix_scraped_courses_instructor' not in existing_indexes:
        op.create_index('ix_scraped_courses_instructor', 'scraped_courses', ['instructor'])
    if 'ix_scraped_courses_day_and_period' not in existing_indexes:
        op.create_index('ix_scraped_courses_day_and_period', 'scraped_courses', ['day_and_period'])
    if 'ix_scraped_courses_area_of_study' not in existing_indexes:
        op.create_index('ix_scraped_courses_area_of_study', 'scraped_courses', ['area_of_study'])


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_indexes = {idx['name'] for idx in insp.get_indexes('scraped_courses')}

    for idx_name in [
        'ix_scraped_courses_area_of_study',
        'ix_scraped_courses_day_and_period',
        'ix_scraped_courses_instructor',
        'ix_scraped_courses_course_title',
        'ix_scraped_courses_course_number',
    ]:
        if idx_name in existing_indexes:
            op.drop_index(idx_name, table_name='scraped_courses')

    cols = {c['name'] for c in insp.get_columns('scraped_courses')}
    for col_name in [
        'additional_attributes_json',
        'prerequisite',
        'area_of_study',
        'day_and_period',
        'instructor',
        'credits',
        'course_title',
        'course_number',
    ]:
        if col_name in cols:
            op.drop_column('scraped_courses', col_name)
