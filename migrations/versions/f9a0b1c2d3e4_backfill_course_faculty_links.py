"""Backfill course faculty relations from legacy instructor text.

Revision ID: f9a0b1c2d3e4
Revises: f1b2c3d4e5f6
Create Date: 2026-03-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
import re

revision = 'f9a0b1c2d3e4'
down_revision = 'f1b2c3d4e5f6'
branch_labels = None
depends_on = None


def _extract_faculty_names(raw_value: str | None) -> list[str]:
    text = str(raw_value or '').strip()
    if not text:
        return []

    placeholder = {'tba', 'tbd', 'staff', 'n/a', 'na', 'none', 'unknown'}
    if text.strip().lower() in placeholder:
        return []

    parts = re.split(r'\s*(?:;|\||/|&|\band\b)\s*', text, flags=re.IGNORECASE)
    normalized: list[str] = []
    seen: set[str] = set()
    for part in parts:
        name = re.sub(r'\s+', ' ', str(part or '').strip())
        name = re.sub(r'^(dr|prof|professor|mr|ms|mrs)\.?\s+', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s+,', ',', name)
        name = name.strip(' -;|/')
        if not name:
            continue
        key = name.lower()
        if key in placeholder or key in seen:
            continue
        seen.add(key)
        normalized.append(name)
    return normalized


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if 'courses' not in tables or 'faculty' not in tables or 'course_faculty' not in tables:
        return

    course_columns = {c['name'] for c in inspector.get_columns('courses')}
    if 'instructor' not in course_columns:
        return

    rows = bind.execute(sa.text("SELECT id, instructor FROM courses")).mappings().all()
    if not rows:
        return

    faculty_by_key: dict[str, int] = {}
    for row in bind.execute(sa.text("SELECT id, name FROM faculty")).mappings().all():
        name = str(row.get('name') or '').strip()
        if name:
            faculty_by_key[name.lower()] = int(row['id'])

    existing_links = {
        (int(r['course_id']), int(r['faculty_id']))
        for r in bind.execute(sa.text("SELECT course_id, faculty_id FROM course_faculty")).mappings().all()
    }

    for course in rows:
        course_id = int(course['id'])
        names = _extract_faculty_names(course.get('instructor'))
        for name in names:
            key = name.lower()
            faculty_id = faculty_by_key.get(key)
            if faculty_id is None:
                inserted = bind.execute(
                    sa.text("INSERT INTO faculty (name, email, created_at, updated_at) VALUES (:name, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) RETURNING id"),
                    {'name': name},
                ).scalar()
                if inserted is None:
                    continue
                faculty_id = int(inserted)
                faculty_by_key[key] = faculty_id

            link_key = (course_id, faculty_id)
            if link_key in existing_links:
                continue

            bind.execute(
                sa.text("INSERT INTO course_faculty (course_id, faculty_id) VALUES (:course_id, :faculty_id)"),
                {'course_id': course_id, 'faculty_id': faculty_id},
            )
            existing_links.add(link_key)


def downgrade() -> None:
    # Data backfill migration is intentionally irreversible.
    pass
