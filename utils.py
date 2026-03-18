# utils.py
from models import db, Setting, ScrapedDataset, Course
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
    years = set()
    try:
        rows = ScrapedDataset.query.filter_by(status='completed').all()
        for row in rows:
            try:
                y = int(str(row.year or '').strip())
                years.add(y)
            except Exception:
                continue
    except Exception:
        pass

    try:
        local_rows = Course.query.filter(Course.year.isnot(None)).all()
        for row in local_rows:
            try:
                y = int(str(row.year or '').strip())
                years.add(y)
            except Exception:
                continue
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