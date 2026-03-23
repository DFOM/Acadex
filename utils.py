# utils.py
from models import db, Setting, ScrapedDataset, Course, CatalogStatus, DatasetRegistry, Faculty
import re
import json
from typing import Any
from sqlalchemy import func


def safe_json_loads(value, default):
    try:
        if value is None:
            return default
        if isinstance(value, (dict, list)):
            return value
        s = str(value).strip()
        if not s:
            return default
        return json.loads(s)
    except Exception:
        return default


def safe_json_dumps(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return '{}'


def normalize_dataset_semester(value: Any) -> str:
    text = str(value or '').strip().lower()
    if text in {'spring', 'spr', 'sp'}:
        return 'spring'
    if text in {'fall', 'autumn', 'fa'}:
        return 'fall'
    if text in {'full', 'fullyear', 'full_year', 'full-year', 'full year', 'year', 'annual', 'all'}:
        return 'full_year'
    return text


def infer_dataset_type(semester: Any) -> str:
    sem = normalize_dataset_semester(semester)
    if sem == 'full_year':
        return 'full_year'
    if sem in {'spring', 'fall'}:
        return sem
    return 'term'


def resolve_dataset(year: Any, semester: Any, *, source: str | None = None) -> DatasetRegistry | None:
    year_text = str(year or '').strip().lower()
    sem_text = normalize_dataset_semester(semester)
    source_text = str(source or '').strip().lower()

    try:
        query = DatasetRegistry.query
        if source_text:
            query = query.filter(func.lower(func.coalesce(DatasetRegistry.source, '')) == source_text)

        if year_text and sem_text:
            exact = (
                query.filter(
                    func.lower(func.coalesce(DatasetRegistry.year, '')) == year_text,
                    func.lower(func.coalesce(DatasetRegistry.semester, '')) == sem_text,
                )
                .order_by(DatasetRegistry.created_at.desc(), DatasetRegistry.id.desc())
                .first()
            )
            if exact:
                return exact

        if year_text:
            full_year = (
                query.filter(
                    func.lower(func.coalesce(DatasetRegistry.year, '')) == year_text,
                    func.lower(func.coalesce(DatasetRegistry.dataset_type, 'term')) == 'full_year',
                )
                .order_by(DatasetRegistry.created_at.desc(), DatasetRegistry.id.desc())
                .first()
            )
            if full_year:
                return full_year

        return query.order_by(DatasetRegistry.created_at.desc(), DatasetRegistry.id.desc()).first()
    except Exception:
        db.session.rollback()
        return None


def resolve_dataset_name(year: Any, semester: Any, *, source: str | None = None) -> str:
    row = resolve_dataset(year, semester, source=source)
    return str(getattr(row, 'name', '') or '').strip()


def extract_dataset_context(*values: Any) -> tuple[str | None, str | None, str]:
    merged = ' '.join(str(v or '') for v in values if str(v or '').strip())
    merged_lower = merged.lower()

    year_value = None
    year_match = re.search(r'\b(20\d{2})\b', merged)
    if year_match:
        year_value = year_match.group(1)

    semester_value = ''
    if re.search(r'\b(spring|spr|sp)\b', merged_lower):
        semester_value = 'spring'
    elif re.search(r'\b(fall|autumn|fa)\b', merged_lower):
        semester_value = 'fall'
    elif re.search(r'\b(full\s*year|fullyear|full[_\- ]year|annual|all\s*courses|all)\b', merged_lower):
        semester_value = 'full_year'

    dataset_type = infer_dataset_type(semester_value)
    return year_value, (semester_value or None), dataset_type


def normalize_faculty_name(name: str) -> str:
    cleaned = re.sub(r'\s+', ' ', str(name or '').strip())
    cleaned = re.sub(r'^(dr|prof|professor|mr|ms|mrs)\.?\s+', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+,', ',', cleaned)
    cleaned = cleaned.strip(' -;|/')
    return cleaned


def extract_faculty_names(raw_value: Any) -> list[str]:
    text = str(raw_value or '').strip()
    if not text:
        return []

    placeholder = {'tba', 'tbd', 'staff', 'n/a', 'na', 'none', 'unknown'}
    if text.lower() in placeholder:
        return []

    parts = re.split(r'\s*(?:,|;|\||/|&|\band\b)\s*', text, flags=re.IGNORECASE)
    names: list[str] = []
    seen: set[str] = set()
    for part in parts:
        name = normalize_faculty_name(part)
        if not name:
            continue
        key = name.lower()
        if key in placeholder or key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def sync_course_faculty(course: Course, faculty_value: Any, *, fallback_text: Any = None) -> list[str]:
    names = extract_faculty_names(faculty_value)
    if not names:
        names = extract_faculty_names(fallback_text)

    if not names:
        course.faculty = []
        course.instructor = None
        return []

    lowered = [n.lower() for n in names]
    existing_rows = Faculty.query.filter(func.lower(Faculty.name).in_(lowered)).all()
    existing_map = {(r.name or '').strip().lower(): r for r in existing_rows}

    linked_rows: list[Faculty] = []
    for name in names:
        key = name.lower()
        row = existing_map.get(key)
        if not row:
            row = Faculty(name=name)
            db.session.add(row)
            existing_map[key] = row
        linked_rows.append(row)

    course.faculty = linked_rows
    course.instructor = '; '.join(names)
    return names


def ensure_default_landing_sections():
    """Ensure core landing page sections exist.

    Returns sections ordered by display_order.
    Safe to call on every request (does minimal work).
    """
    defaults = [
        {
            'name': 'hero',
            'enabled': True,
            'display_order': 10,
            'content': {
                'headline': 'Master Your Academic Journey',
                'subtext': (
                    'Build your perfect schedule, track your degree progress, and explore courses as a guest. '
                    'Ready for more? Create a free account to permanently save your plans, calculate GPA, and unlock analytics.'
                ),
                'primary_label': 'Start Planning',
                'secondary_label': 'Register for Premium',
            },
        },
        {
            'name': 'features',
            'enabled': True,
            'display_order': 20,
            'content': {
                'title': 'Why Acadex',
                'cards': [
                    {
                        'icon': 'calendar',
                        'title': 'Schedule Builder',
                        'description': 'Plan semesters quickly with a clean, modern grid editor.',
                    },
                    {
                        'icon': 'sparkles',
                        'title': 'Analytics-Ready',
                        'description': 'Understand your workload patterns with admin-grade insights.',
                    },
                    {
                        'icon': 'shield-check',
                        'title': 'Built for Students',
                        'description': 'Guest mode for quick exploration, accounts for permanence.',
                    },
                ],
            },
        },
        {'name': 'announcements', 'enabled': True, 'display_order': 30, 'content': None},
        {'name': 'info_grid', 'enabled': True, 'display_order': 40, 'content': None},
    ]

    try:
        # Local import avoids circulars during app init.
        from models import LandingPageSection

        existing = {s.name: s for s in LandingPageSection.query.all()}
        changed = False
        for item in defaults:
            if item['name'] in existing:
                continue
            section = LandingPageSection(
                name=item['name'],
                enabled=bool(item.get('enabled', True)),
                display_order=int(item.get('display_order', 0)),
                content_json=safe_json_dumps(item['content']) if item.get('content') is not None else None,
            )
            db.session.add(section)
            changed = True

        if changed:
            db.session.commit()

        return LandingPageSection.query.order_by(LandingPageSection.display_order.asc(), LandingPageSection.id.asc()).all()
    except Exception:
        # Pre-migration or DB not ready: return defaults as lightweight objects.
        from types import SimpleNamespace

        return [
            SimpleNamespace(
                id=None,
                name=item['name'],
                enabled=item.get('enabled', True),
                display_order=item.get('display_order', 0),
                content_json=safe_json_dumps(item['content']) if item.get('content') is not None else None,
            )
            for item in defaults
        ]


def format_display_course_number(raw: str) -> str:
    """Create a user-friendly course identifier.

    Examples:
    - EFAE010-S1-Fa -> EFAE010 (Sec 1)
    - PSYC/DATA190-Sp -> PSYC/DATA 190
    """
    s = str(raw or '').strip()
    if not s:
        return ''

    # Remove version suffixes.
    s = re.sub(r'-V\d+\b', '', s, flags=re.I)

    # Extract section if present.
    section = None
    m = re.search(r'-S(\d+)\b', s, flags=re.I)
    if m:
        section = m.group(1)

    # Drop trailing term markers like -Sp / -Fa / -Spring / -Fall.
    s = re.sub(r'-(Sp|Fa|Spring|Fall)\b.*$', '', s, flags=re.I)

    # Drop section markers and anything after.
    s = re.sub(r'-S\d+\b.*$', '', s, flags=re.I)

    # Insert a space between letters and digits (DATA190 -> DATA 190).
    def _space_alpha_num(token: str) -> str:
        return re.sub(r'([A-Za-z])(?=\d)', r'\1 ', token)

    parts = s.split('/')
    parts = [_space_alpha_num(p.strip()) for p in parts if p.strip()]
    s = '/'.join(parts)

    if section:
        return f"{s} (Sec {section})"
    return s

def get_available_dataset_years() -> list[int]:
    years: set[int] = set()

    def _add_year(value) -> None:
        text = str(value or '').strip()
        if not text:
            return
        direct = re.fullmatch(r'\d{4}', text)
        if direct:
            year_val = int(text)
            if 2000 <= year_val <= 2199:
                years.add(year_val)
            return
        for match in re.findall(r'\b(20\d{2})\b', text):
            try:
                year_val = int(match)
            except Exception:
                continue
            if 2000 <= year_val <= 2199:
                years.add(year_val)

    try:
        rows = ScrapedDataset.query.filter_by(status='completed').all()
        for row in rows:
            _add_year(getattr(row, 'year', None))
            _add_year(getattr(row, 'name', None))
    except Exception:
        pass

    try:
        local_rows = Course.query.all()
        for row in local_rows:
            _add_year(getattr(row, 'year', None))
            _add_year(getattr(row, 'dataset_name', None))
    except Exception:
        pass

    try:
        status_rows = CatalogStatus.query.all()
        for row in status_rows:
            _add_year(getattr(row, 'term', None))
            _add_year(getattr(row, 'dataset', None))
    except Exception:
        pass

    try:
        reg_rows = DatasetRegistry.query.all()
        for row in reg_rows:
            _add_year(getattr(row, 'name', None))
            _add_year(getattr(row, 'table_name', None))
    except Exception:
        pass

    return sorted(years)

def is_valid_table_name(name: str) -> bool:
    """Basic validation for SQL table identifiers used by the app.

    Only allow ASCII letters, digits and underscores to avoid SQL injection
    risks when identifiers are interpolated into SQL statements.
    """
    if not name or not isinstance(name, str):
        return False
    return bool(re.match(r'^[A-Za-z0-9_]+$', name))

def get_app_setting(key, default=""):
    """Fetches a setting from the database using SQLAlchemy ORM."""
    try:
        setting = Setting.query.get(key)
        return setting.value if setting else default
    except Exception:
        return default

def update_app_setting(key, value):
    """Updates or creates a setting using SQLAlchemy ORM."""
    setting = Setting.query.get(key)
    if not setting:
        setting = Setting(key=key, value=value)
        db.session.add(setting)
    else:
        setting.value = value
    db.session.commit()

def calculate_gpa(courses):
    """Calculates GPA based on the standard 4.0 scale."""
    if not courses:
        return 0.0

    grade_points = {'A': 4.0, 'B': 3.0, 'C': 2.0, 'D': 1.0, 'F': 0.0}
    total_points = 0.0
    total_credits = 0.0

    for course in courses:
        grade = (course.get('grade') or '').strip().upper()
        try:
            credits = float(course.get('Credits') or 0)
        except (ValueError, TypeError):
            credits = 0.0

        if grade in grade_points and credits > 0:
            total_points += grade_points[grade] * credits
            total_credits += credits

    return total_points / total_credits if total_credits > 0 else 0.0