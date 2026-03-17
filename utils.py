# utils.py
from sqlalchemy import inspect
from models import db, Setting
import re
import json


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


def format_course_catalog_name(raw: str) -> str:
    s = str(raw or '').strip()
    if not s:
        return ''
    return s.replace('.csv', '').replace('_', ' ')


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


def normalize_semester_name(value: str) -> str:
    v = str(value or '').strip().lower()
    if v in {'fall', 'fa'}:
        return 'Fall'
    if v in {'spring', 'sp'}:
        return 'Spring'
    return str(value or '').strip()


def semester_label_for_dataset(year, semester: str) -> str:
    """Return the dataset's Semester column label for a term.

    Our uploaded full-year datasets store values like "Spring Semester 2025".
    """
    sem = normalize_semester_name(semester)
    try:
        y = int(year)
    except Exception:
        y = year
    if sem in {'Fall', 'Spring'}:
        return f"{sem} Semester {y}"
    # Best-effort fallback.
    return f"{sem} {y}".strip()


def table_has_column(table_name: str, column_name: str) -> bool:
    try:
        inspector = inspect(db.engine)
        cols = {c['name'] for c in inspector.get_columns(table_name)}
        return column_name in cols
    except Exception:
        return False


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

def get_course_data_tables():
    """Dynamically finds all uploaded CSV tables, ignoring the core system tables."""
    try:
        inspector = inspect(db.engine)
        all_tables = inspector.get_table_names()
        # Core system tables to ignore when looking for CSV datasets
        core_tables = [
            'users', 'schedules', 'schedule_courses', 'subject_areas', 
            'instructors', 'periods', 'settings', 'alembic_version',
            'announcements',
            'ai_events',
            'behavior_events',
            'notifications',
            'landing_page_sections',
            'catalog_statuses',
        ]
        
        return [t for t in all_tables if t not in core_tables and not t.startswith('sqlite_')]
    except Exception as e:
        print(f"Error inspecting tables: {e}")
        return []


def get_available_dataset_years() -> list[int]:
    years = set()
    for table_name in get_course_data_tables():
        for match in re.findall(r'(?:19|20)\d{2}', str(table_name)):
            try:
                years.add(int(match))
            except Exception:
                continue
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

def get_best_course_table(year, semester):
    """Finds the most relevant dataset for a given year/semester."""
    tables = get_course_data_tables()
    if not tables:
        return None, False
    
    year_str = str(year)
    sem_norm = normalize_semester_name(semester)
    sem_lower = str(sem_norm).lower()

    year_tables = [t for t in tables if year_str in str(t)]
    if not year_tables:
        return tables[-1], True

    # Semester-specific detection/matching
    sem_aliases = {
        'fall': ['fall', 'fa'],
        'spring': ['spring', 'sp'],
    }
    any_sem_specific_year = [
        t for t in year_tables
        if any(a in str(t).lower() for a in sem_aliases['fall'] + sem_aliases['spring'])
    ]

    wanted_aliases = sem_aliases.get(sem_lower, [sem_lower])
    sem_specific_matches = [t for t in year_tables if any(a in str(t).lower() for a in wanted_aliases)]
    if sem_specific_matches:
        return sem_specific_matches[0], False

    # If we have semester-specific tables for the year but none match the requested semester,
    # do NOT fall back to the wrong semester.
    if any_sem_specific_year and sem_norm in {'Fall', 'Spring'}:
        return None, False

    # Full-year datasets should be filtered by Semester column.
    full_year_tables = [t for t in year_tables if ('full' in str(t).lower() or 'all_courses' in str(t).lower())]
    if full_year_tables:
        return full_year_tables[0], True

    # Fallback: use a year-level table and apply Semester filtering if possible.
    return year_tables[0], True

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