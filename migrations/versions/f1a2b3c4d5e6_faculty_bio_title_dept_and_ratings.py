"""Faculty bio/title/department fields and course/faculty rating models

Revision ID: f1a2b3c4d5e6
Revises: e7f2a1b3c4d5
Create Date: 2026-04-10

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f1a2b3c4d5e6'
down_revision = 'e7f2a1b3c4d5'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # ── Faculty: add title, department, bio ──
    if inspector.has_table('faculty'):
        cols = {c['name'] for c in inspector.get_columns('faculty')}
        with op.batch_alter_table('faculty', schema=None) as batch_op:
            if 'title' not in cols:
                batch_op.add_column(sa.Column('title', sa.String(200), nullable=True))
            if 'department' not in cols:
                batch_op.add_column(sa.Column('department', sa.String(200), nullable=True))
            if 'bio' not in cols:
                batch_op.add_column(sa.Column('bio', sa.Text(), nullable=True))

    # ── CourseRating table ──
    if not inspector.has_table('course_ratings'):
        op.create_table(
            'course_ratings',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False, index=True),
            sa.Column('course_number', sa.String(120), nullable=False, index=True),
            sa.Column('score', sa.Integer(), nullable=False),
            sa.Column('comment', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
            sa.UniqueConstraint('user_id', 'course_number', name='uq_course_rating_user_course'),
        )

    # ── FacultyRating table ──
    if not inspector.has_table('faculty_ratings'):
        op.create_table(
            'faculty_ratings',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False, index=True),
            sa.Column('faculty_id', sa.Integer(), sa.ForeignKey('faculty.id'), nullable=False, index=True),
            sa.Column('score', sa.Integer(), nullable=False),
            sa.Column('comment', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
            sa.UniqueConstraint('user_id', 'faculty_id', name='uq_faculty_rating_user_faculty'),
        )


def downgrade():
    op.drop_table('faculty_ratings')
    op.drop_table('course_ratings')
    with op.batch_alter_table('faculty', schema=None) as batch_op:
        batch_op.drop_column('bio')
        batch_op.drop_column('department')
        batch_op.drop_column('title')
