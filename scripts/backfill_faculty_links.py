import re

from app import app
from models import db, Course, Faculty


def _normalize_faculty_name(name: str) -> str:
    cleaned = re.sub(r'\s+', ' ', str(name or '').strip())
    cleaned = re.sub(r'^(dr|prof|professor|mr|ms|mrs)\.?\s+', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+,', ',', cleaned)
    cleaned = cleaned.strip(' -;|/')
    return cleaned


def _extract_faculty_names(raw_value: str | None) -> list[str]:
    text = str(raw_value or '').strip()
    if not text:
        return []

    placeholder = {'tba', 'tbd', 'staff', 'n/a', 'na', 'none', 'unknown'}
    if text.lower() in placeholder:
        return []

    parts = re.split(r'\s*(?:;|\||/|&|\band\b)\s*', text, flags=re.IGNORECASE)
    names: list[str] = []
    seen: set[str] = set()
    for part in parts:
        name = _normalize_faculty_name(part)
        if not name:
            continue
        key = name.lower()
        if key in placeholder or key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def _get_or_create_faculty(name: str, cache: dict[str, Faculty]) -> Faculty:
    key = name.lower()
    existing = cache.get(key)
    if existing:
        return existing

    row = Faculty.query.filter(db.func.lower(Faculty.name) == key).first()
    if not row:
        row = Faculty()
        row.name = name
        db.session.add(row)
        db.session.flush()
    cache[key] = row
    return row


def main() -> None:
    with app.app_context():
        cache: dict[str, Faculty] = {}
        updated_courses = 0
        created_faculty = 0

        for course in Course.query.order_by(Course.id.asc()).all():
            if course.faculty:
                for person in course.faculty:
                    cache[(person.name or '').strip().lower()] = person
                continue

            parsed_names = _extract_faculty_names(course.instructor)
            if not parsed_names:
                continue

            linked_rows: list[Faculty] = []
            for name in parsed_names:
                before_count = len(cache)
                row = _get_or_create_faculty(name, cache)
                linked_rows.append(row)
                if len(cache) > before_count:
                    created_faculty += 1

            course.faculty = linked_rows
            course.instructor = '; '.join(parsed_names)
            updated_courses += 1

        db.session.commit()
        print(f"Backfill complete. Courses updated: {updated_courses}, faculty created: {created_faculty}")


if __name__ == '__main__':
    main()
