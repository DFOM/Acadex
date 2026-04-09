"""Add dynamic course metadata schema and visibility controls

Revision ID: e7f2a1b3c4d5
Revises: c2d4e6f8a9b0
Create Date: 2026-04-09

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e7f2a1b3c4d5'
down_revision = 'c2d4e6f8a9b0'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table('courses'):
        cols = {c['name'] for c in inspector.get_columns('courses')}
        with op.batch_alter_table('courses', schema=None) as batch_op:
            if 'metadata_json' not in cols:
                batch_op.add_column(sa.Column('metadata_json', sa.JSON(), nullable=True))

    if inspector.has_table('dataset_registry'):
        cols = {c['name'] for c in inspector.get_columns('dataset_registry')}
        with op.batch_alter_table('dataset_registry', schema=None) as batch_op:
            if 'metadata_schema_json' not in cols:
                batch_op.add_column(sa.Column('metadata_schema_json', sa.JSON(), nullable=True))

    if not inspector.has_table('course_metadata_fields'):
        op.create_table(
            'course_metadata_fields',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('field_key', sa.String(length=120), nullable=False),
            sa.Column('label', sa.String(length=180), nullable=False),
            sa.Column('visibility', sa.String(length=20), nullable=False, server_default='student'),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('field_key', name='uq_course_metadata_fields_field_key'),
        )
        op.create_index('ix_course_metadata_fields_field_key', 'course_metadata_fields', ['field_key'], unique=True)
        op.create_index('ix_course_metadata_fields_visibility', 'course_metadata_fields', ['visibility'], unique=False)
        op.create_index('ix_course_metadata_fields_is_active', 'course_metadata_fields', ['is_active'], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table('course_metadata_fields'):
        indexes = {idx.get('name') for idx in inspector.get_indexes('course_metadata_fields')}
        if 'ix_course_metadata_fields_is_active' in indexes:
            op.drop_index('ix_course_metadata_fields_is_active', table_name='course_metadata_fields')
        if 'ix_course_metadata_fields_visibility' in indexes:
            op.drop_index('ix_course_metadata_fields_visibility', table_name='course_metadata_fields')
        if 'ix_course_metadata_fields_field_key' in indexes:
            op.drop_index('ix_course_metadata_fields_field_key', table_name='course_metadata_fields')
        op.drop_table('course_metadata_fields')

    if inspector.has_table('dataset_registry'):
        cols = {c['name'] for c in inspector.get_columns('dataset_registry')}
        with op.batch_alter_table('dataset_registry', schema=None) as batch_op:
            if 'metadata_schema_json' in cols:
                batch_op.drop_column('metadata_schema_json')

    if inspector.has_table('courses'):
        cols = {c['name'] for c in inspector.get_columns('courses')}
        with op.batch_alter_table('courses', schema=None) as batch_op:
            if 'metadata_json' in cols:
                batch_op.drop_column('metadata_json')
