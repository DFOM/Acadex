"""Stub bridge for missing revision applied to production DB.

Revision ID: a54785eaba7a
Revises: b1c2d3e4f5a6
Create Date: 2026-03-18
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a54785eaba7a'
down_revision = 'b1c2d3e4f5a6'
branch_labels = None
depends_on = None


def upgrade():
    # This revision was applied directly to the production database
    # but the migration file was lost. The schema changes it introduced
    # have already been applied; this stub allows Alembic to resume
    # the migration chain from this point.
    pass


def downgrade():
    pass
