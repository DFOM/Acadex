# Routes/student_routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session, current_app, send_file
from flask_login import login_required, current_user
from sqlalchemy import text, or_, and_, inspect, func
import uuid
import re
import json
import ast
import os
from datetime import datetime
from typing import Optional, Dict, Any, Set, Iterable
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash, generate_password_hash
from models import db, User, Schedule, ScheduleCourse, Period, Setting, Announcement, AIEvent, BehaviorEvent, Notification, SubjectArea, Course, DatasetRegistry
from utils import (
    get_available_dataset_years,
    calculate_gpa,
    format_display_course_number,
    resolve_dataset,
    resolve_dataset_name,
    normalize_dataset_semester,
)
from services.eligibility import normalize_course_code_for_prereqs
from services.conflicts import parse_day_period_blocks
from services.ai.recommendations import recommend_courses
from services.ai.generator import generate_schedule_courses
from services.prereqs import dependency_graph_for_course
from config.graduation_reqs import (
    get_general_requirements,
    get_major_requirements,
    get_minor_requirements,
    PATHWAY_TITLE_TO_AREA,
    MINOR_REQUIREMENTS,
)

student_bp = Blueprint('student', __name__)


def _allowed_profile_image(filename: str) -> bool:
    ext = (filename.rsplit('.', 1)[-1] if '.' in filename else '').strip().lower()
    return ext in {'png', 'jpg', 'jpeg', 'webp', 'gif'}


def get_str_setting(key: str, default: str = '') -> str:
    try:
        setting = Setting.query.get(key)
        return str(setting.value) if setting and setting.value is not None else default
    except Exception:
        return default


def _setting_truthy(value: str) -> bool:
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'on'}


def get_bool_setting(key: str, default: bool = False) -> bool:
    try:
        setting = Setting.query.get(key)
        if not setting:
            return default
        return _setting_truthy(setting.value)
    except Exception:
        return default


def get_float_setting(key: str, default: float = 0.0) -> float:
    try:
        setting = Setting.query.get(key)
        if not setting or setting.value in (None, ''):
            return float(default)
        return float(setting.value)
    except Exception:
        return float(default)


def _dataset_registry_supported() -> bool:
    try:
        resolve_dataset('', '')
        return True
    except Exception:
        db.session.rollback()
        return False


def _normalize_year_text(value: Any) -> str:
    return str(value or '').strip().lower()


def _normalize_semester_text(value: Any) -> str:
    return normalize_dataset_semester(value)


def _parse_schedule_level(value: Any) -> int | None:
    try:
        parsed = int(str(value or '').strip())
    except Exception:
        return None
    return parsed if parsed in {1, 2, 3, 4} else None


def _is_full_year_semester(value: Any) -> bool:
    sem = _normalize_semester_text(value)
    return sem == 'full_year'


def _resolve_registry_entry_for_context(year: Any, semester: Any, *, source: str | None = None) -> DatasetRegistry | None:
    return resolve_dataset(year, semester, source=source)


def _resolve_contextual_dataset_name(year: Any, semester: Any, *, source: str | None = None) -> str:
    return resolve_dataset_name(year, semester, source=source)


def _current_year_setting_value() -> int:
    current_year_setting = Setting.query.get('current_year')
    if current_year_setting and str(current_year_setting.value or '').isdigit():
        return int(current_year_setting.value)
    return datetime.utcnow().year


def _registry_terms_index() -> dict[int, set[str]]:
    index: dict[int, set[str]] = {}
    try:
        rows = DatasetRegistry.query.order_by(DatasetRegistry.created_at.desc(), DatasetRegistry.id.desc()).all()
    except Exception:
        db.session.rollback()
        return index

    for row in rows:
        semester = normalize_dataset_semester(getattr(row, 'semester', None))
        if semester not in {'spring', 'fall'}:
            continue
        year_text = str(getattr(row, 'year', '') or '').strip()
        if not year_text.isdigit():
            continue
        year_val = int(year_text)
        if year_val < 2000 or year_val > 2199:
            continue
        index.setdefault(year_val, set()).add(semester)
    return index


def _eligible_term_index(term_index: dict[int, set[str]], schedule_type: str, current_year: int) -> dict[int, set[str]]:
    if schedule_type == 'past':
        return {y: set(v) for y, v in term_index.items() if y < current_year}
    max_future_year = current_year + 5
    return {y: set(v) for y, v in term_index.items() if current_year <= y <= max_future_year}


def _resolve_term_with_fallback(
    requested_year: int,
    requested_semester: str,
    schedule_type: str,
    *,
    current_year: int,
    term_index: dict[int, set[str]],
) -> tuple[int | None, str | None, str | None]:
    sem = normalize_dataset_semester(requested_semester)
    if sem not in {'spring', 'fall'}:
        return None, None, None

    eligible = _eligible_term_index(term_index, schedule_type, current_year)
    if sem in eligible.get(requested_year, set()):
        return requested_year, sem, None

    if schedule_type != 'future':
        return None, None, None

    max_future_year = current_year + 5
    candidate_years = sorted(y for y in eligible.keys() if requested_year <= y <= max_future_year)
    for year_val in candidate_years:
        if sem in eligible.get(year_val, set()):
            return year_val, sem, f'No {sem.title()} catalog was found for {requested_year}. Using {sem.title()} {year_val}.'

    for year_val in candidate_years:
        semesters = sorted(eligible.get(year_val, set()))
        if semesters:
            chosen_sem = semesters[0]
            return year_val, chosen_sem, f'No {sem.title()} catalog was found for {requested_year}. Using {chosen_sem.title()} {year_val}.'

    return None, None, None


def _course_offering_cycle(row: Dict[str, Any]) -> str | None:
    text_blob = ' '.join(
        [
            str(row.get('Course_Title') or ''),
            str(row.get('Prerequisite') or ''),
            str(row.get('Area_of_Study') or ''),
            str(row.get('Day_and_Period') or ''),
        ]
    ).lower()
    if re.search(r'\b(offered?|available)\s+(in\s+)?odd\s+years?\b|\bodd\s+years?\s+only\b', text_blob):
        return 'odd'
    if re.search(r'\b(offered?|available)\s+(in\s+)?even\s+years?\b|\beven\s+years?\s+only\b', text_blob):
        return 'even'
    return None


def _apply_offering_cycle_filter(rows: list[Dict[str, Any]], year: Any) -> list[Dict[str, Any]]:
    try:
        year_val = int(str(year or '').strip())
    except Exception:
        return rows

    parity = 'even' if year_val % 2 == 0 else 'odd'
    filtered: list[Dict[str, Any]] = []
    for row in rows:
        cycle = _course_offering_cycle(row)
        if cycle is None or cycle == parity:
            filtered.append(row)
    return filtered


def _humanize_day_and_period(value: Any) -> str:
    raw = str(value or '').strip()
    if not raw:
        return ''

    matches = re.findall(r'(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s*[-:]?\s*(\d+)', raw, flags=re.IGNORECASE)
    if matches:
        return ', '.join(f"{d.title()} Period {int(p)}" for d, p in matches)

    blocks = sorted(parse_day_period_blocks(raw))
    if not blocks:
        return raw

    readable = []
    for block in blocks:
        day = str(block[:3] or '').title()
        period = str(block[3:] or '').strip()
        if day and period:
            readable.append(f"{day} Period {period}")
    return ', '.join(readable) if readable else raw


def _normalize_instructor(value: Any) -> str:
    def _clean_token(token: Any) -> str:
        text = str(token or '').strip()
        if not text:
            return ''
        text = text.strip('[]').strip()
        text = re.sub(r'^["\']+|["\']+$', '', text).strip()
        text = re.sub(r'\s+', ' ', text).strip()
        if text.lower() in {'tba', 'n/a', 'na', 'none', 'unknown'}:
            return ''
        return text

    if isinstance(value, (list, tuple, set)):
        names = []
        seen = set()
        for item in value:
            cleaned = _clean_token(item)
            lowered = cleaned.lower()
            if cleaned and lowered not in seen:
                seen.add(lowered)
                names.append(cleaned)
        return ', '.join(names)

    instructor = str(value or '').strip()
    if not instructor:
        return ''

    parsed_candidates = []
    try:
        parsed_candidates.append(json.loads(instructor))
    except Exception:
        parsed_candidates.append(None)
    try:
        parsed_candidates.append(ast.literal_eval(instructor))
    except Exception:
        parsed_candidates.append(None)

    for parsed in parsed_candidates:
        if isinstance(parsed, (list, tuple, set)):
            return _normalize_instructor(parsed)

    if instructor.startswith('[') and instructor.endswith(']'):
        parts = [p for p in re.split(r'\s*,\s*', instructor.strip('[]')) if p]
        return _normalize_instructor(parts)

    cleaned = _clean_token(instructor)
    return cleaned


def _parse_hhmm_to_minutes(value: Any) -> int | None:
    text = str(value or '').strip()
    if not text:
        return None
    match = re.fullmatch(r'(\d{1,2}):(\d{2})', text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return hour * 60 + minute


def _period_time_map() -> dict[str, tuple[int, int]]:
    period_map: dict[str, tuple[int, int]] = {}
    try:
        rows = Period.query.all()
    except Exception:
        db.session.rollback()
        return period_map

    for row in rows:
        period_name = str(getattr(row, 'name', '') or '').strip()
        if not period_name:
            continue
        start = _parse_hhmm_to_minutes(getattr(row, 'start_time', None))
        end = _parse_hhmm_to_minutes(getattr(row, 'end_time', None))
        if start is None or end is None or end <= start:
            continue
        period_map[period_name] = (start, end)
    return period_map


def _day_period_structured_blocks(day_and_period: Any) -> list[tuple[str, str]]:
    text = str(day_and_period or '').strip()
    if not text or text.upper() == 'TBA':
        return []

    tokens = re.split(r'[\s,;]+', text)
    blocks: list[tuple[str, str]] = []
    current_day = ''
    for token in tokens:
        if not token:
            continue
        day_match = re.search(r'(Mon|Tue|Wed|Thu|Fri|Sat|Sun)', token, re.IGNORECASE)
        if day_match:
            current_day = day_match.group(1)[:3].title()
        num_match = re.search(r'(\d+)', token)
        if current_day and num_match:
            blocks.append((current_day, num_match.group(1)))
    return blocks


def _time_windows_from_day_period(day_and_period: Any, period_map: dict[str, tuple[int, int]]) -> list[tuple[str, int, int]]:
    windows: list[tuple[str, int, int]] = []
    for day, period_name in _day_period_structured_blocks(day_and_period):
        slot = period_map.get(period_name)
        if not slot:
            continue
        windows.append((day, slot[0], slot[1]))
    return windows


def _windows_overlap(left_windows: list[tuple[str, int, int]], right_windows: list[tuple[str, int, int]]) -> bool:
    if not left_windows or not right_windows:
        return False
    for left_day, left_start, left_end in left_windows:
        for right_day, right_start, right_end in right_windows:
            if left_day != right_day:
                continue
            if left_start < right_end and right_start < left_end:
                return True
    return False


def should_collect_ai_events() -> bool:
    if not current_user.is_authenticated:
        return False
    # Background AI learning should continue regardless of AI recommendation toggle.
    # Admin can disable user-facing AI features, but event learning remains active.
    return True


def log_ai_event_safe(event_type: str, schedule_id: Optional[int] = None, metadata: Optional[Dict[str, Any]] = None) -> None:
    """Best-effort AI event logging.

    This must never break core UX, so it catches/rolls back failures.
    """
    if not should_collect_ai_events():
        return

    try:
        metadata_json = json.dumps(metadata or {}, default=str) if metadata is not None else None
        db.session.add(
            AIEvent(
                user_id=current_user.id,
                schedule_id=schedule_id,
                event_type=event_type,
                route=request.path,
                metadata_json=metadata_json,
            )
        )
        db.session.commit()
    except Exception:
        db.session.rollback()


def _normalized_behavior_tracking_level() -> str:
    level = (get_str_setting('behavior_tracking_level', 'standard') or 'standard').strip().lower()
    if level not in {'minimal', 'standard', 'detailed'}:
        return 'standard'
    return level


def should_collect_behavior_events() -> bool:
    # Always-on behavior tracking supports continuous model learning.
    # We allow both authenticated users and guest sessions.
    return True

def get_guest_id():
    if 'guest_id' not in session:
        session['guest_id'] = str(uuid.uuid4())
    return session['guest_id']


def _guest_seen_notification_ids() -> Set[int]:
    try:
        raw = session.get('guest_seen_notif_ids') or []
        ids = set()
        for x in raw:
            try:
                ids.add(int(x))
            except Exception:
                continue
        return ids
    except Exception:
        return set()


def _set_guest_seen_notification_ids(ids: Set[int]) -> None:
    try:
        # Keep cookie size under control.
        session['guest_seen_notif_ids'] = list(sorted(list(ids)))[-200:]
    except Exception:
        pass


def _notifications_base_query_for_request():
    """Return a query for notifications scoped to the current request.

    - Authenticated users see per-user notifications.
    - Guests see broadcast notifications plus any session-scoped notifications.
    """
    if current_user.is_authenticated:
        return Notification.query.filter(Notification.user_id == current_user.id)

    guest_id = get_guest_id()
    return Notification.query.filter(
        or_(
            Notification.session_id == guest_id,
            and_(Notification.user_id.is_(None), Notification.session_id.is_(None)),
        )
    )


def _serialize_notification(n: Notification) -> Dict[str, Any]:
    created = None
    try:
        created = n.created_at.isoformat() if n.created_at else None
    except Exception:
        created = None

    is_broadcast = (getattr(n, 'user_id', None) is None) and (getattr(n, 'session_id', None) is None)
    if current_user.is_authenticated:
        is_read = bool(getattr(n, 'is_read', False))
    else:
        if is_broadcast:
            is_read = n.id in _guest_seen_notification_ids()
        else:
            is_read = bool(getattr(n, 'is_read', False))

    return {
        'id': n.id,
        'notif_type': getattr(n, 'notif_type', None),
        'title': getattr(n, 'title', ''),
        'body': getattr(n, 'body', ''),
        'url': getattr(n, 'url', None),
        'created_at': created,
        'is_read': bool(is_read),
        'is_broadcast': bool(is_broadcast),
    }


def _scrub_behavior_metadata(level: str, metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not metadata:
        return {}

    # Minimal: store nothing beyond event_type + route.
    if level == 'minimal':
        return {}

    allowed_standard = {
        'course_number',
        'entry_id',
        'result_count',
        'schedule_type',
        'year',
        'semester',
        'action',
    }
    allowed_detailed = allowed_standard | {
        'query',
        'query_len',
        'source',
    }
    allowed = allowed_detailed if level == 'detailed' else allowed_standard

    scrubbed: Dict[str, Any] = {}
    for k, v in (metadata or {}).items():
        if k in allowed:
            scrubbed[k] = v

    # If query exists at standard level, store only length.
    if level != 'detailed' and 'query' in (metadata or {}):
        try:
            scrubbed['query_len'] = len(str((metadata or {}).get('query') or ''))
        except Exception:
            scrubbed['query_len'] = None
        scrubbed.pop('query', None)

    return scrubbed


def log_behavior_event_safe(
    event_type: str,
    schedule_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Best-effort behavior tracking.

    This logger must never break core UX. It is also designed to be safe
    pre-migration (e.g., table missing) by catching and rolling back.
    """
    if not should_collect_behavior_events():
        return

    try:
        level = _normalized_behavior_tracking_level()
        scrubbed = _scrub_behavior_metadata(level, metadata)
        metadata_json = json.dumps(scrubbed, default=str) if scrubbed else None

        user_id = current_user.id if current_user.is_authenticated else None
        sess_id = None if current_user.is_authenticated else get_guest_id()

        db.session.add(
            BehaviorEvent(
                user_id=user_id,
                session_id=sess_id,
                schedule_id=schedule_id,
                event_type=event_type,
                route=request.path,
                metadata_json=metadata_json,
            )
        )
        db.session.commit()
    except Exception:
        db.session.rollback()


def _add_display_fields(course: Dict[str, Any]) -> Dict[str, Any]:
    # Mutates/returns the input dict.
    title = course.get('Course_Title') or course.get('course_title') or ''
    raw_number = course.get('Course_Number') or course.get('course_number') or ''
    course['display_title'] = str(title)
    course['display_course_number'] = format_display_course_number(raw_number)
    return course


def _normalize_course_number(course_number: Any) -> str:
    return str(course_number or '').strip().upper()


def _source_priority(source: str | None) -> int:
    src = str(source or '').strip().lower()
    if src == 'manual':
        return 3
    if src == 'csv':
        return 2
    return 1


def _catalog_resolution_key(course_number: Any, day_period: Any, semester: Any, year: Any) -> tuple[str, str, str, str]:
    return (
        _normalize_course_number(course_number),
        str(day_period or '').strip().lower(),
        str(semester or '').strip().lower(),
        str(year or '').strip().lower(),
    )


def _manual_or_csv_course_to_catalog_row(row: Course) -> Dict[str, Any]:
    instructor = _normalize_instructor(row.instructor)
    return {
        'Course_Number': str(row.course_number or '').strip(),
        'Course_Title': str(row.title or '').strip(),
        'Credits': float(row.credits or 0.0),
        'Instructor': instructor,
        'Day_and_Period': _humanize_day_and_period(row.day_period),
        'Area_of_Study': str(row.subject_area or '').strip(),
        'Prerequisite': str(row.prerequisite or '').strip(),
        'dataset_id': None,
        'dataset_name': row.dataset_name,
        'semester': str(row.semester or '').strip(),
        'year': str(row.year or '').strip(),
        'Year_Level': row.year_level,
        'year_level': row.year_level,
        'source': str(row.source or '').strip().lower() or 'manual',
        'updated_ts': row.last_updated or row.updated_at or row.created_at,
    }


# ─── Prefix → Subject Area mapping (for cross-listed courses) ───
_PREFIX_TO_AREA: Dict[str, str] = {
    'ARTS': 'Interdisciplinary Arts - Arts',
    'DATA': 'Data Science',
    'ECON': 'Global Business and Economics',
    'EFAE': 'English for Academic Excellence',
    'FNDN': 'Foundation Courses',
    'HIST': 'Japan Studies - History',
    'INTA': 'Interdisciplinary Arts - Interdisciplinary Arts',
    'JPNA': 'Japan Studies - Japan Studies',
    'JPNL': 'Japan Studies - Japanese Language',
    'LANG': 'Interdisciplinary Arts - Language Arts',
    'MUSC': 'Interdisciplinary Arts - Music',
    'PART': 'Interdisciplinary Arts - Performing Arts',
    'PSCI': 'Political Science',
    'PSYC': 'Psychology',
    'QREA': 'Quantitative Reasoning and Natural Sciences',
    'SCNS': 'Quantitative Reasoning and Natural Sciences',
    'SOCI': 'Sociology',
    'SPRT': 'Health and Physical Education',
}


def _derive_areas_from_course_number(course_number: str) -> list[str]:
    """Derive all subject areas from a course number's prefixes (e.g. ECON/DATA291 → GBE + Data Science)."""
    raw = re.sub(r'[\d\s]', '', str(course_number or '')).strip().upper()
    prefixes = [p.strip() for p in raw.split('/') if p.strip()]
    areas: list[str] = []
    seen: set[str] = set()
    for prefix in prefixes:
        area = _PREFIX_TO_AREA.get(prefix)
        if area and area not in seen:
            areas.append(area)
            seen.add(area)
    return areas


def _normalize_semester_label(raw_semester: str) -> str:
    """Extract Spring/Fall from semester strings like 'Spring Semester 2025'."""
    s = str(raw_semester or '').strip().lower()
    if 'spring' in s:
        return 'Spring'
    if 'fall' in s:
        return 'Fall'
    return ''


def _build_combined_course_catalog() -> list[Dict[str, Any]]:
    """Build a deduplicated course catalog from ALL datasets.

    Groups by normalized course_number (ignoring semester/period), picks the
    best row for display, collects all semesters into a 'semesters' label,
    and derives subject areas from course number prefixes.
    """
    all_courses = Course.query.all()
    candidates = [_manual_or_csv_course_to_catalog_row(r) for r in all_courses]

    # Group by normalized course number
    groups: Dict[str, list[Dict[str, Any]]] = {}
    for row in candidates:
        key = _normalize_course_number(row.get('Course_Number'))
        if not key:
            continue
        groups.setdefault(key, []).append(row)

    bad_areas = {'1year', '2year', '3year', '4year'}

    combined: list[Dict[str, Any]] = []
    for key, rows in groups.items():
        # Pick the best row (latest, highest priority source)
        rows.sort(
            key=lambda r: (
                _source_priority(r.get('source')),
                r.get('updated_ts') or datetime.min,
            ),
            reverse=True,
        )
        # Pick a winner that has a valid instructor
        winner = rows[0]
        for r in rows:
            inst = _normalize_instructor(r.get('Instructor'))
            if inst:
                winner = r
                break

        # Collect semesters
        sem_set: set[str] = set()
        for r in rows:
            label = _normalize_semester_label(r.get('semester'))
            if label:
                sem_set.add(label)
        if 'Spring' in sem_set and 'Fall' in sem_set:
            semesters_label = 'Both'
        elif 'Spring' in sem_set:
            semesters_label = 'Spring'
        elif 'Fall' in sem_set:
            semesters_label = 'Fall'
        else:
            semesters_label = ''

        # Collect all areas from CSV rows (ignoring bad/misaligned ones)
        csv_areas: set[str] = set()
        for r in rows:
            area = str(r.get('Area_of_Study') or '').strip()
            if area and area.lower() not in bad_areas and not re.match(r'^[A-Z]+\s[A-Za-z]+$', area):
                csv_areas.add(area)

        # Derive areas from course number prefixes
        prefix_areas = _derive_areas_from_course_number(winner.get('Course_Number'))

        # Merge: prefix-derived areas + CSV areas (prefix areas take priority for ordering)
        all_areas: list[str] = list(prefix_areas)
        for a in sorted(csv_areas):
            if a not in all_areas:
                all_areas.append(a)

        primary_area = all_areas[0] if all_areas else str(winner.get('Area_of_Study') or 'General')

        # Collect year level — pick from any row that has a valid one
        year_level = None
        for r in rows:
            yl = r.get('Year_Level') or r.get('year_level')
            if yl is not None:
                try:
                    year_level = int(yl)
                    break
                except (ValueError, TypeError):
                    pass

        entry = _add_display_fields(dict(winner))
        entry['Area_of_Study'] = primary_area
        entry['areas'] = all_areas
        entry['semesters'] = semesters_label
        entry['year_level'] = year_level
        entry['Year_Level'] = year_level
        entry['Instructor'] = _normalize_instructor(winner.get('Instructor')) or 'TBA'
        combined.append(entry)

    combined.sort(
        key=lambda r: (
            str(r.get('Area_of_Study') or ''),
            str(r.get('Course_Number') or ''),
        )
    )
    return combined


def _active_subject_areas() -> Dict[str, SubjectArea]:
    rows = SubjectArea.query.filter_by(is_active=True).all()
    return {(r.name or '').strip().lower(): r for r in rows if (r.name or '').strip()}


def _resolve_user_area_names() -> Dict[str, str]:
    names = {'major': '', 'minor': ''}
    if not current_user.is_authenticated:
        return names
    if getattr(current_user, 'major_subject_area_id', None):
        m = SubjectArea.query.get(current_user.major_subject_area_id)
        names['major'] = (m.name or '').strip() if m else ''
    if getattr(current_user, 'minor_subject_area_id', None):
        m = SubjectArea.query.get(current_user.minor_subject_area_id)
        names['minor'] = (m.name or '').strip() if m else ''
    return names


def _student_has_major_or_minor(user_id: int) -> bool:
    user = User.query.get(user_id)
    if not user:
        return False
    return bool(getattr(user, 'major_subject_area_id', None) or getattr(user, 'minor_subject_area_id', None))


def _student_has_past_schedule_history(user_id: int) -> bool:
    count = db.session.execute(
        text("SELECT COUNT(*) FROM schedules WHERE user_id = :uid AND schedule_type = 'past'"),
        {'uid': user_id},
    ).scalar() or 0
    return int(count) > 0


def _student_can_receive_ai_suggestions(user_id: int) -> tuple[bool, str | None]:
    if _student_has_major_or_minor(user_id):
        return True, None
    if _student_has_past_schedule_history(user_id):
        return True, None
    return False, 'Suggestions require a declared major/minor or at least one completed past schedule.'


def _grade_to_points(grade: str) -> float | None:
    g = str(grade or '').strip().upper()
    points = {'A': 4.0, 'B': 3.0, 'C': 2.0, 'D': 1.0, 'F': 0.0}
    return points.get(g)


def _safe_json_load_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}

def _catalog_identity_key(course_number: Any, day_period: Any, semester: Any, year: Any, dataset_name: Any) -> tuple[str, str, str, str, str]:
    return (
        _normalize_course_number(course_number),
        str(day_period or '').strip().lower(),
        normalize_dataset_semester(semester),
        _normalize_year_text(year),
        str(dataset_name or '').strip().lower(),
    )

def _schedule_course_rows_for_schedule(schedule_id: int) -> list[Dict[str, Any]]:
    try:
        cols = {c['name'] for c in inspect(db.engine).get_columns('schedule_courses')}
    except Exception:
        db.session.rollback()
        cols = set()

    select_parts = [
        'sc.id as entry_id',
        'sc.grade as grade',
        'sc.course_number as course_number',
    ]

    for name in ['day_period', 'semester', 'year', 'dataset_name', 'source']:
        if name in cols:
            select_parts.append(f'sc.{name} as {name}')
        else:
            select_parts.append(f'NULL as {name}')

    query = text(
        f'''
        SELECT {', '.join(select_parts)}
        FROM schedule_courses sc
        WHERE sc.schedule_id = :sid
        '''
    )

    try:
        rows = db.session.execute(query, {'sid': schedule_id}).fetchall()
        return [dict(r._mapping) for r in rows]
    except Exception:
        db.session.rollback()
        return []


def _hydrate_schedule_courses(schedule: Schedule, schedule_rows: list[Dict[str, Any]], catalog_rows: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    catalog_by_identity: Dict[tuple[str, str, str, str, str], Dict[str, Any]] = {}
    catalog_by_number: Dict[str, Dict[str, Any]] = {}

    for row in catalog_rows:
        mapped = dict(row)
        identity_key = _catalog_identity_key(
            mapped.get('Course_Number'),
            mapped.get('Day_and_Period'),
            mapped.get('semester') or schedule.semester,
            mapped.get('year') or schedule.year,
            mapped.get('dataset_name'),
        )
        catalog_by_identity.setdefault(identity_key, mapped)

        number_key = _normalize_course_number(mapped.get('Course_Number'))
        if number_key and number_key not in catalog_by_number:
            catalog_by_number[number_key] = mapped

    meta_index = _build_course_metadata_index()
    courses: list[Dict[str, Any]] = []

    for row in schedule_rows:
        course_number = row.get('course_number')
        day_period = row.get('day_period')
        semester = row.get('semester') or schedule.semester
        year = row.get('year') or schedule.year
        dataset_name = row.get('dataset_name')

        identity_key = _catalog_identity_key(course_number, day_period, semester, year, dataset_name)
        payload = dict(catalog_by_identity.get(identity_key) or {})

        if not payload and day_period:
            term_key = _catalog_identity_key(course_number, day_period, schedule.semester, schedule.year, dataset_name)
            payload = dict(catalog_by_identity.get(term_key) or {})

        code = _normalize_course_number(course_number)
        if not payload:
            payload = dict(catalog_by_number.get(code) or {})

        if not payload:
            meta = meta_index.get(code, {})
            payload = {
                'Course_Number': course_number,
                'Course_Title': meta.get('title') or course_number,
                'Credits': float(meta.get('credits') or 0.0),
                'Instructor': meta.get('instructor') or '',
                'Day_and_Period': day_period or meta.get('day_and_period') or '',
                'Area_of_Study': meta.get('area') or '',
                'Prerequisite': '',
            }

        resolved_day_period = str(day_period or payload.get('Day_and_Period') or 'TBA').strip() or 'TBA'
        resolved_semester = normalize_dataset_semester(semester or payload.get('semester') or schedule.semester)
        resolved_year = str(year or payload.get('year') or schedule.year).strip()
        resolved_dataset_name = str(dataset_name or payload.get('dataset_name') or '').strip()

        payload['entry_id'] = row.get('entry_id')
        payload['grade'] = row.get('grade')
        payload['identity_day_period'] = resolved_day_period
        payload['identity_semester'] = resolved_semester
        payload['identity_year'] = resolved_year
        payload['identity_dataset_name'] = resolved_dataset_name
        payload['identity_source'] = row.get('source')
        payload['Day_and_Period'] = payload.get('Day_and_Period') or resolved_day_period
        courses.append(_add_display_fields(payload))

    return courses


def _update_learning_profile_snapshot_safe(user_id: int) -> None:
    """Best-effort per-user learning snapshot used by background AI analysis."""
    try:
        progress = _degree_progress_context(user_id)
        snapshot = {
            'updated_at': datetime.utcnow().isoformat(),
            'user_id': int(user_id),
            'major_minor_gate': _student_can_receive_ai_suggestions(user_id)[0],
            'missing_credits_by_area': progress.get('missing_by_area', {}),
            'remaining_to_graduate': progress.get('remaining_to_graduate', 0.0),
            'history_counts': progress.get('history_counts', {}),
            'earned_credits': progress.get('earned_credits', 0.0),
            'in_progress_credits': progress.get('in_progress_credits', 0.0),
            'projected_credits': progress.get('projected_credits', 0.0),
        }

        key = f'ai_learning_profile_{int(user_id)}'
        setting = Setting.query.get(key)
        if not setting:
            setting = Setting(key=key, value=json.dumps(snapshot, default=str))
            db.session.add(setting)
        else:
            setting.value = json.dumps(snapshot, default=str)
        db.session.commit()
    except Exception:
        db.session.rollback()


def _build_learning_signals(
    user_id: int,
    catalog_courses: Iterable[Dict[str, Any]],
    *,
    major_subject_area_id: int | None,
    minor_subject_area_id: int | None,
) -> Dict[str, Dict[str, float]]:
    peer_popularity_by_course: Dict[str, float] = {}
    behavior_affinity_by_course: Dict[str, float] = {}
    ai_affinity_by_course: Dict[str, float] = {}
    faculty_success_by_name: Dict[str, float] = {}
    course_avg_grade_points: Dict[str, float] = {}

    try:
        peer_where = ['u.id != :uid']
        params: Dict[str, Any] = {'uid': user_id}
        if major_subject_area_id and minor_subject_area_id:
            peer_where.append('(u.major_subject_area_id = :major_id OR u.minor_subject_area_id = :minor_id)')
            params['major_id'] = int(major_subject_area_id)
            params['minor_id'] = int(minor_subject_area_id)
        elif major_subject_area_id:
            peer_where.append('(u.major_subject_area_id = :major_id OR u.minor_subject_area_id = :major_id)')
            params['major_id'] = int(major_subject_area_id)
        elif minor_subject_area_id:
            peer_where.append('(u.major_subject_area_id = :minor_id OR u.minor_subject_area_id = :minor_id)')
            params['minor_id'] = int(minor_subject_area_id)

        peer_sql = f'''
            SELECT sc.course_number as course_number, COUNT(*) as cnt
            FROM schedule_courses sc
            JOIN schedules s ON s.id = sc.schedule_id
            JOIN users u ON u.id = s.user_id
            WHERE {' AND '.join(peer_where)}
            GROUP BY sc.course_number
        '''
        rows = db.session.execute(text(peer_sql), params).fetchall()
        for row in rows:
            cnum = _normalize_course_number(row.course_number)
            if cnum:
                peer_popularity_by_course[cnum] = float(row.cnt or 0.0)
    except Exception:
        db.session.rollback()

    try:
        peer_user_ids = db.session.execute(
            text(
                '''
                SELECT u.id
                FROM users u
                WHERE u.id != :uid
                  AND (
                    (:major_id IS NOT NULL AND (u.major_subject_area_id = :major_id OR u.minor_subject_area_id = :major_id))
                    OR
                    (:minor_id IS NOT NULL AND (u.major_subject_area_id = :minor_id OR u.minor_subject_area_id = :minor_id))
                  )
                '''
            ),
            {
                'uid': user_id,
                'major_id': major_subject_area_id,
                'minor_id': minor_subject_area_id,
            },
        ).fetchall()
        peer_ids = {int(r[0]) for r in peer_user_ids if r and r[0] is not None}

        if peer_ids:
            behavior_rows = BehaviorEvent.query.filter(
                BehaviorEvent.user_id.in_(peer_ids),
                BehaviorEvent.event_type == 'schedule_course_added',
            ).all()
            for ev in behavior_rows:
                meta = _safe_json_load_dict(ev.metadata_json)
                cnum = _normalize_course_number(meta.get('course_number'))
                if cnum:
                    behavior_affinity_by_course[cnum] = float(behavior_affinity_by_course.get(cnum, 0.0)) + 1.0

            ai_rows = AIEvent.query.filter(
                AIEvent.user_id.in_(peer_ids),
                AIEvent.event_type == 'course_added',
            ).all()
            for ev in ai_rows:
                meta = _safe_json_load_dict(ev.metadata_json)
                cnum = _normalize_course_number(meta.get('course_number'))
                if cnum:
                    ai_affinity_by_course[cnum] = float(ai_affinity_by_course.get(cnum, 0.0)) + 1.0
    except Exception:
        db.session.rollback()

    try:
        grade_rows = db.session.execute(
            text(
                '''
                SELECT sc.course_number, sc.grade
                FROM schedule_courses sc
                JOIN schedules s ON s.id = sc.schedule_id
                WHERE s.schedule_type = 'past'
                  AND sc.grade IS NOT NULL
                  AND sc.grade != ''
                '''
            )
        ).fetchall()

        metadata = _build_course_metadata_index()
        course_sum: Dict[str, float] = {}
        course_count: Dict[str, int] = {}
        faculty_sum: Dict[str, float] = {}
        faculty_count: Dict[str, int] = {}

        for row in grade_rows:
            points = _grade_to_points(row.grade)
            if points is None:
                continue
            cnum = _normalize_course_number(row.course_number)
            if not cnum:
                continue

            course_sum[cnum] = float(course_sum.get(cnum, 0.0)) + float(points)
            course_count[cnum] = int(course_count.get(cnum, 0)) + 1

            instructor = str((metadata.get(cnum) or {}).get('instructor') or '').strip().lower()
            if instructor:
                faculty_sum[instructor] = float(faculty_sum.get(instructor, 0.0)) + float(points)
                faculty_count[instructor] = int(faculty_count.get(instructor, 0)) + 1

        for cnum, total in course_sum.items():
            cnt = int(course_count.get(cnum, 0))
            if cnt > 0:
                course_avg_grade_points[cnum] = round(float(total) / float(cnt), 3)

        for inst, total in faculty_sum.items():
            cnt = int(faculty_count.get(inst, 0))
            if cnt > 0:
                faculty_success_by_name[inst] = round(float(total) / float(cnt), 3)
    except Exception:
        db.session.rollback()

    # Keep only catalog-relevant course keys for compact payload/scoring.
    catalog_keys = {
        _normalize_course_number(c.get('Course_Number') or c.get('course_number'))
        for c in catalog_courses
    }
    catalog_keys.discard('')

    peer_popularity_by_course = {k: v for k, v in peer_popularity_by_course.items() if k in catalog_keys}
    behavior_affinity_by_course = {k: v for k, v in behavior_affinity_by_course.items() if k in catalog_keys}
    ai_affinity_by_course = {k: v for k, v in ai_affinity_by_course.items() if k in catalog_keys}
    course_avg_grade_points = {k: v for k, v in course_avg_grade_points.items() if k in catalog_keys}

    return {
        'peer_popularity_by_course': peer_popularity_by_course,
        'behavior_affinity_by_course': behavior_affinity_by_course,
        'ai_affinity_by_course': ai_affinity_by_course,
        'faculty_success_by_name': faculty_success_by_name,
        'course_avg_grade_points': course_avg_grade_points,
    }


def _load_scraped_catalog_for_term(year: Any, semester: Any) -> tuple[list[Dict[str, Any]], int | None]:
    year_text = str(year or '').strip()
    sem_text = normalize_dataset_semester(semester)
    contextual_dataset = resolve_dataset(year_text, sem_text)
    contextual_dataset_name = str(getattr(contextual_dataset, 'name', '') or '').strip()

    local_query = Course.query
    if contextual_dataset_name:
        local_query = local_query.filter(
            or_(
                func.lower(func.coalesce(Course.dataset_name, '')) == contextual_dataset_name.lower(),
                func.lower(func.coalesce(Course.source, '')) == 'manual',
            )
        )
    else:
        if year_text:
            local_query = local_query.filter(Course.year == year_text)
        if sem_text:
            local_query = local_query.filter(func.lower(Course.semester) == sem_text)
    local_rows = local_query.all()

    candidates: list[Dict[str, Any]] = [_manual_or_csv_course_to_catalog_row(r) for r in local_rows]

    dataset_id = int(getattr(contextual_dataset, 'id', 0) or 0) or None

    filtered_candidates: list[Dict[str, Any]] = []
    skipped_missing_instructor = 0
    for row in candidates:
        instructor = _normalize_instructor(row.get('Instructor'))
        if not instructor:
            skipped_missing_instructor += 1
            continue
        row['Instructor'] = instructor
        filtered_candidates.append(row)

    if skipped_missing_instructor:
        current_app.logger.warning(
            'Catalog resolution skipped %s course rows without valid instructor (year=%s semester=%s).',
            skipped_missing_instructor,
            year_text or 'latest',
            sem_text or 'latest',
        )

    if not filtered_candidates:
        return [], dataset_id

    winner_by_key: Dict[tuple[str, str, str, str], Dict[str, Any]] = {}
    for row in filtered_candidates:
        key = _catalog_resolution_key(
            row.get('Course_Number'),
            row.get('Day_and_Period'),
            row.get('semester') or semester,
            row.get('year') or year,
        )
        existing = winner_by_key.get(key)
        if not existing:
            winner_by_key[key] = row
            continue

        left_rank = _source_priority(row.get('source'))
        right_rank = _source_priority(existing.get('source'))
        if left_rank > right_rank:
            winner_by_key[key] = row
            continue
        if left_rank < right_rank:
            continue

        left_ts = row.get('updated_ts') or datetime.min
        right_ts = existing.get('updated_ts') or datetime.min
        if left_ts >= right_ts:
            winner_by_key[key] = row

    resolved = list(winner_by_key.values())
    resolved.sort(
        key=lambda item: (
            str(item.get('Course_Number') or ''),
            str(item.get('Day_and_Period') or ''),
        )
    )
    resolved = _apply_offering_cycle_filter(resolved, year_text)
    return resolved, dataset_id


def _build_course_metadata_index() -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    candidates = [_manual_or_csv_course_to_catalog_row(r) for r in Course.query.all()]
    candidates.sort(
        key=lambda row: (
            _source_priority(row.get('source')),
            row.get('updated_ts') or datetime.min,
        ),
        reverse=True,
    )

    for mapped in candidates:
        course_number = _normalize_course_number(mapped.get('Course_Number'))
        if not course_number or course_number in index:
            continue
        try:
            credits = float(mapped.get('Credits') or 0.0)
        except Exception:
            credits = 0.0
        index[course_number] = {
            'title': str(mapped.get('Course_Title') or '').strip(),
            'instructor': str(mapped.get('Instructor') or '').strip(),
            'area': str(mapped.get('Area_of_Study') or '').strip(),
            'day_and_period': str(mapped.get('Day_and_Period') or '').strip(),
            'credits': max(0.0, credits),
        }
    return index


def _catalog_rows_with_local_course_ids(catalog_rows: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    rows = [dict(r or {}) for r in catalog_rows]

    local_rows = Course.query.order_by(Course.last_updated.desc(), Course.id.desc()).all()
    local_identity_to_id: Dict[tuple[str, str, str, str], int] = {}
    local_number_to_id: Dict[str, int] = {}

    for row in local_rows:
        key = _catalog_resolution_key(row.course_number, row.day_period, row.semester, row.year)
        local_identity_to_id.setdefault(key, int(row.id))

        number_key = _normalize_course_number(row.course_number)
        if number_key and number_key not in local_number_to_id:
            local_number_to_id[number_key] = int(row.id)

    enriched: list[Dict[str, Any]] = []
    for row in rows:
        mapped = dict(row)
        if mapped.get('course_id') is not None:
            enriched.append(mapped)
            continue

        key = _catalog_resolution_key(
            mapped.get('Course_Number'),
            mapped.get('Day_and_Period'),
            mapped.get('semester'),
            mapped.get('year'),
        )
        course_id = local_identity_to_id.get(key)
        if course_id is None:
            course_id = local_number_to_id.get(_normalize_course_number(mapped.get('Course_Number')))
        mapped['course_id'] = course_id
        enriched.append(mapped)

    return enriched


def _build_prereq_title_lookup() -> Dict[str, str]:
    """Build normalized prerequisite token -> display title mapping."""
    metadata = _build_course_metadata_index()
    lookup: Dict[str, str] = {}
    for course_number, info in metadata.items():
        title = str((info or {}).get('title') or '').strip()
        if not title:
            continue

        normalized_number = normalize_course_code_for_prereqs(course_number)
        if normalized_number:
            lookup[normalized_number] = title

        normalized_title = normalize_course_code_for_prereqs(title)
        if normalized_title and normalized_title not in lookup:
            lookup[normalized_title] = title

    return lookup


def _load_schedule_courses_fallback(schedule_id: int) -> list[Dict[str, Any]]:
    """Fallback schedule course loader when source catalog tables are unavailable."""
    rows = _schedule_course_rows_for_schedule(schedule_id)
    if not rows:
        return []

    metadata = _build_course_metadata_index()
    payload: list[Dict[str, Any]] = []
    for row in rows:
        cnum = _normalize_course_number(row.get('course_number'))
        meta = metadata.get(cnum, {})
        item = {
            'entry_id': row.get('entry_id'),
            'grade': row.get('grade'),
            'Course_Number': cnum,
            'Course_Title': (meta.get('title') or 'Required course title unavailable').strip(),
            'Credits': meta.get('credits', 0.0),
            'Instructor': (meta.get('instructor') or 'TBA').strip() or 'TBA',
            'Day_and_Period': str(row.get('day_period') or 'TBA').strip() or 'TBA',
            'Area_of_Study': (meta.get('area') or '').strip(),
            'Prerequisite': '',
            'identity_day_period': row.get('day_period'),
            'identity_semester': row.get('semester'),
            'identity_year': row.get('year'),
            'identity_dataset_name': row.get('dataset_name'),
            'identity_source': row.get('source'),
        }
        payload.append(_add_display_fields(item))
    return payload


def _completed_passing_course_numbers(user_id: int) -> Set[str]:
    rows = db.session.execute(
        text(
            '''
            SELECT sc.course_number
            FROM schedule_courses sc
            JOIN schedules s ON s.id = sc.schedule_id
            WHERE s.user_id = :uid
              AND s.schedule_type = 'past'
              AND sc.grade IS NOT NULL
              AND sc.grade != ''
              AND UPPER(sc.grade) != 'F'
            '''
        ),
        {'uid': user_id},
    ).fetchall()
    return {_normalize_course_number(r[0]) for r in rows if r and r[0]}


def _completed_passing_prereq_identifiers(user_id: int) -> Set[str]:
    code_set = _completed_passing_course_numbers(user_id)
    metadata = _build_course_metadata_index()

    identifiers: Set[str] = set()
    for code in code_set:
        norm_code = normalize_course_code_for_prereqs(code)
        if norm_code:
            identifiers.add(norm_code)
        title = str((metadata.get(code) or {}).get('title') or '').strip()
        norm_title = normalize_course_code_for_prereqs(title)
        if norm_title:
            identifiers.add(norm_title)

    return identifiers


def _split_prereq_terms(prereq_str: str) -> list[str]:
    text_value = str(prereq_str or '').strip()
    if not text_value:
        return []

    standardized = re.sub(r'\bAND/OR\b', ' OR ', text_value, flags=re.IGNORECASE)
    standardized = re.sub(r'\s*&\s*', ' AND ', standardized)
    standardized = re.sub(r'\s*\|\s*', ' OR ', standardized)
    parts = re.split(r'\bAND\b|\bOR\b|\(|\)|,|;', standardized, flags=re.IGNORECASE)
    terms: list[str] = []
    for part in parts:
        term = str(part or '').strip()
        if term:
            terms.append(term)
    return terms


def _looks_like_trackable_course_term(term: str) -> bool:
    return bool(re.search(r'[A-Za-z]{2,}\s*[-]?\s*\d{2,}', str(term or '')))


def _build_prerequisite_status(
    prereq_str: str,
    completed_passing_identifiers: Iterable[str],
    *,
    major_name: str | None = None,
    minor_name: str | None = None,
    prereq_title_lookup: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    prereq_raw = str(prereq_str or '').strip()
    none_like = prereq_raw.lower() in {'', 'none', 'n/a', 'na'}
    if none_like:
        return {
            'has_prerequisites': False,
            'state': 'none',
            'summary': 'No prerequisites.',
            'warning_message': None,
            'items': [],
            'satisfied_count': 0,
            'missing_count': 0,
            'untrackable_count': 0,
            'total_count': 0,
        }

    completed_set = {
        normalize_course_code_for_prereqs(v)
        for v in completed_passing_identifiers
        if str(v or '').strip()
    }
    title_lookup = prereq_title_lookup or {}

    major_norm = str(major_name or '').strip().lower()
    minor_norm = str(minor_name or '').strip().lower()

    items = []
    trackable_count = 0
    satisfied_count = 0
    missing_count = 0
    untrackable_count = 0

    for term in _split_prereq_terms(prereq_raw):
        term_lower = term.lower()
        if 'major' in term_lower:
            if major_norm:
                items.append(
                    {
                        'term': term,
                        'status': 'satisfied',
                        'reason': 'Satisfied from your declared major.',
                    }
                )
                satisfied_count += 1
            else:
                items.append(
                    {
                        'term': term,
                        'status': 'untrackable',
                        'reason': 'Requires a declared major; cannot verify from past courses alone.',
                    }
                )
                untrackable_count += 1
            continue

        if 'minor' in term_lower:
            if minor_norm:
                items.append(
                    {
                        'term': term,
                        'status': 'satisfied',
                        'reason': 'Satisfied from your declared minor.',
                    }
                )
                satisfied_count += 1
            else:
                items.append(
                    {
                        'term': term,
                        'status': 'untrackable',
                        'reason': 'Requires a declared minor; cannot verify from past courses alone.',
                    }
                )
                untrackable_count += 1
            continue

        # Check if this term is trackable — either looks like a course code (XX123)
        # or matches a known course title in the catalog.
        normalized_term = normalize_course_code_for_prereqs(term)
        is_code_like = _looks_like_trackable_course_term(term)
        is_known_title = bool(normalized_term and normalized_term in title_lookup)

        if is_code_like or is_known_title:
            trackable_count += 1
            display_term = title_lookup.get(normalized_term, term) if normalized_term else term
            if normalized_term and normalized_term in completed_set:
                items.append(
                    {
                        'term': display_term,
                        'status': 'satisfied',
                        'reason': 'Found in your completed past schedules with a passing grade.',
                    }
                )
                satisfied_count += 1
            else:
                items.append(
                    {
                        'term': display_term,
                        'status': 'missing',
                        'reason': 'Not found in your completed past schedules.',
                    }
                )
                missing_count += 1
            continue

        items.append(
            {
                'term': term,
                'status': 'untrackable',
                'reason': 'Cannot be verified automatically (e.g., placement/external requirement).',
            }
        )
        untrackable_count += 1

    if missing_count == 0 and untrackable_count == 0 and trackable_count > 0:
        state = 'satisfied'
        summary = 'All prerequisites are satisfied from past schedules.'
        warning_message = None
    else:
        state = 'warning'
        summary = f'{satisfied_count}/{len(items)} prerequisite items verified.'
        warning_message = 'Some prerequisites are missing or cannot be verified automatically, but you can still add this course.'

    return {
        'has_prerequisites': True,
        'state': state,
        'summary': summary,
        'warning_message': warning_message,
        'items': items,
        'satisfied_count': int(satisfied_count),
        'missing_count': int(missing_count),
        'untrackable_count': int(untrackable_count),
        'total_count': int(len(items)),
    }


def _degree_progress_context(user_id: int) -> Dict[str, Any]:
    metadata = _build_course_metadata_index()
    completed_codes = _completed_passing_course_numbers(user_id)
    graduation_required_credits = get_float_setting('graduation_credits', 124.0)
    if graduation_required_credits <= 0:
        graduation_required_credits = 124.0

    rows = db.session.execute(
        text(
            '''
            SELECT s.schedule_type, sc.course_number, sc.grade
            FROM schedule_courses sc
            JOIN schedules s ON s.id = sc.schedule_id
            WHERE s.user_id = :uid
            '''
        ),
        {'uid': user_id},
    ).fetchall()
    earned_credits = 0.0
    in_progress_credits = 0.0

    earned_by_area: Dict[str, float] = {}
    history_counts: Dict[str, int] = {}

    for row in rows:
        schedule_type = (row.schedule_type or '').strip().lower()
        code = _normalize_course_number(row.course_number)
        if not code:
            continue
        meta = metadata.get(code, {})
        area = (meta.get('area') or '').strip()
        credits = float(meta.get('credits') or 0.0)

        grade = (row.grade or '').strip().upper()
        if schedule_type == 'past' and grade and grade != 'F':
            earned_credits += credits
            if area:
                earned_by_area[area] = earned_by_area.get(area, 0.0) + credits
                history_counts[area] = history_counts.get(area, 0) + 1

        if schedule_type == 'future':
            in_progress_credits += credits

    requirements: Dict[str, float] = {}
    user_areas = _resolve_user_area_names()
    active_areas = _active_subject_areas()

    major_name = (user_areas.get('major') or '').strip().lower()
    minor_name = (user_areas.get('minor') or '').strip().lower()

    if major_name and major_name in active_areas:
        major_area = active_areas[major_name]
        major_req = float(getattr(major_area, 'min_major_credits', 0.0) or 0.0)
        if major_req <= 0:
            major_req = float(getattr(major_area, 'min_depth_credits', 0.0) or 0.0)
        requirements[major_area.name] = max(float(requirements.get(major_area.name, 0.0)), major_req)

    if minor_name and minor_name in active_areas:
        minor_area = active_areas[minor_name]
        minor_req = float(getattr(minor_area, 'min_minor_credits', 0.0) or 0.0)
        if minor_req <= 0:
            minor_req = float(getattr(minor_area, 'min_depth_credits', 0.0) or 0.0)
        requirements[minor_area.name] = max(float(requirements.get(minor_area.name, 0.0)), minor_req)

    if not requirements:
        for area in active_areas.values():
            requirements[area.name] = float(getattr(area, 'min_depth_credits', 0.0) or 0.0)

    missing_by_area: Dict[str, float] = {}
    for area_name, req in requirements.items():
        have = float(earned_by_area.get(area_name, 0.0))
        gap = max(0.0, req - have)
        if gap > 0:
            missing_by_area[area_name] = gap

    total_projected_credits = earned_credits + in_progress_credits
    remaining_to_graduate = max(0.0, graduation_required_credits - total_projected_credits)

    completed_prereq_identifiers = _completed_passing_prereq_identifiers(user_id)

    return {
        'completed': completed_codes,
        'completed_prereq_identifiers': completed_prereq_identifiers,
        'earned_by_area': earned_by_area,
        'missing_by_area': missing_by_area,
        'history_counts': history_counts,
        'earned_credits': round(earned_credits, 2),
        'in_progress_credits': round(in_progress_credits, 2),
        'projected_credits': round(total_projected_credits, 2),
        'graduation_required_credits': round(graduation_required_credits, 2),
        'remaining_to_graduate': round(remaining_to_graduate, 2),
    }


def _graduation_requirements_context(user_id: int, entry_year_override: int | None = None,
                                     major_option: int = 1) -> Dict[str, Any]:
    """Build a comprehensive graduation requirements checklist for the dashboard."""
    metadata = _build_course_metadata_index()
    user_areas = _resolve_user_area_names()
    major_name = (user_areas.get('major') or '').strip()
    minor_name = (user_areas.get('minor') or '').strip()

    # Determine entry year: explicit override > user profile > default 2025
    if entry_year_override:
        entry_year = entry_year_override
    else:
        user = User.query.get(user_id)
        expected_grad = getattr(user, 'expected_graduation', None) if user else None
        if expected_grad and expected_grad <= 2028:
            entry_year = expected_grad - 4  # 4-year program
        else:
            entry_year = 2025

    # ── Build title lookup: map lowered title → best (cnum, grade) ──
    # Also build a *prefix* index so "Expository Research Writing" matches
    # catalog entries like "Expository Research Writing Sec 1".
    # ── Completed courses with grades ──
    rows = db.session.execute(
        text('''
            SELECT sc.course_number, sc.grade
            FROM schedule_courses sc
            JOIN schedules s ON s.id = sc.schedule_id
            WHERE s.user_id = :uid
              AND s.schedule_type = 'past'
              AND sc.grade IS NOT NULL
              AND sc.grade != ''
        '''),
        {'uid': user_id},
    ).fetchall()

    completed = {}          # course_number -> grade (best)
    completed_titles = {}   # title_lower -> (course_number, grade)
    grade_rank = {'A+': 12, 'A': 11, 'A-': 10, 'B+': 9, 'B': 8, 'B-': 7,
                  'C+': 6, 'C': 5, 'C-': 4, 'D+': 3, 'D': 2, 'D-': 1, 'F': 0}

    for r in rows:
        cnum = _normalize_course_number(r[0])
        grade = (r[1] or '').strip().upper()
        if not cnum or grade == 'F':
            continue
        existing = completed.get(cnum, '')
        if grade_rank.get(grade, 0) > grade_rank.get(existing, 0):
            completed[cnum] = grade
        meta = metadata.get(cnum, {})
        title = (meta.get('title') or '').strip().lower()
        if title:
            prev = completed_titles.get(title)
            if not prev or grade_rank.get(grade, 0) > grade_rank.get(prev[1], 0):
                completed_titles[title] = (cnum, grade)

    # ── Planned courses (future schedules) ──
    future_rows = db.session.execute(
        text('''
            SELECT sc.course_number, sc.grade
            FROM schedule_courses sc
            JOIN schedules s ON s.id = sc.schedule_id
            WHERE s.user_id = :uid
              AND s.schedule_type = 'future'
        '''),
        {'uid': user_id},
    ).fetchall()
    planned = set()
    planned_titles = set()
    for r in future_rows:
        cnum = _normalize_course_number(r[0])
        if cnum:
            planned.add(cnum)
            meta = metadata.get(cnum, {})
            title = (meta.get('title') or '').strip().lower()
            if title:
                planned_titles.add(title)

    # ── Title matching helpers (prefix-aware) ──
    # Courses in the catalog may have section suffixes like "Sec 1", "Sec 2".
    # When checking a requirement title like "Composition 1", we need to match
    # any completed/planned course whose title starts with that base name.

    def _match_completed_title(base_title: str):
        """Return (course_number, grade) if any completed title matches (exact or prefix)."""
        key = base_title.strip().lower()
        if key in completed_titles:
            return completed_titles[key]
        for ct_lower, val in completed_titles.items():
            if ct_lower.startswith(key):
                return val
        return None

    def _match_planned_title(base_title: str) -> bool:
        key = base_title.strip().lower()
        if key in planned_titles:
            return True
        return any(pt.startswith(key) for pt in planned_titles)

    def _is_grade_b_or_higher(grade_str):
        return grade_rank.get((grade_str or '').strip().upper(), 0) >= grade_rank.get('B', 8)

    # ── Build general requirements checklist ──
    general_reqs = get_general_requirements(entry_year)
    checklist = []

    for req in general_reqs:
        key = req['key']
        item = {
            'key': key,
            'label': req['label'],
            'note': req['note'],
            'status': 'not_started',
            'courses': [],
        }

        if key == 'english':
            eae_courses = [
                (cnum, meta) for cnum, meta in metadata.items()
                if (meta.get('area') or '').startswith('English for Academic Excellence')
            ]
            earned_eae = 0.0
            for cnum, meta in eae_courses:
                title = (meta.get('title') or '').strip()
                credits = float(meta.get('credits') or 0.0)
                if cnum in completed:
                    earned_eae += credits
                    item['courses'].append({'title': title, 'status': 'completed', 'grade': completed[cnum]})
                elif cnum in planned:
                    item['courses'].append({'title': title, 'status': 'planned', 'grade': None})
            if earned_eae >= 15:
                item['status'] = 'completed'
            elif earned_eae > 0 or any(c['status'] == 'planned' for c in item['courses']):
                item['status'] = 'in_progress'

        elif key == 'japanese':
            for title in req['courses']:
                match = _match_completed_title(title)
                if match:
                    item['courses'].append({'title': title, 'status': 'completed', 'grade': match[1]})
                elif _match_planned_title(title):
                    item['courses'].append({'title': title, 'status': 'planned', 'grade': None})
                else:
                    item['courses'].append({'title': title, 'status': 'missing', 'grade': None})
            if any(c['status'] == 'completed' for c in item['courses']):
                item['status'] = 'completed'
            elif any(c['status'] == 'planned' for c in item['courses']):
                item['status'] = 'in_progress'

        elif key == 'foundation':
            foundation_required = ['Foundation for College Success']
            writing_path_a = ['Composition 1', 'Composition 2', 'Academic Reading Across Disciplines']
            writing_path_b = ['Expository Research Writing']

            # Check Foundation for College Success first
            for title in foundation_required:
                match = _match_completed_title(title)
                if match:
                    item['courses'].append({'title': title, 'status': 'completed', 'grade': match[1]})
                elif _match_planned_title(title):
                    item['courses'].append({'title': title, 'status': 'planned', 'grade': None})
                else:
                    item['courses'].append({'title': title, 'status': 'missing', 'grade': None})

            # Detect which writing path the student is on (OR logic):
            # Path B = Expository Research Writing alone (3cr)
            # Path A = Composition 1 + Composition 2 + Academic Reading (9cr)
            path_b_match = _match_completed_title('Expository Research Writing')
            path_b_planned = _match_planned_title('Expository Research Writing')
            path_a_done = sum(1 for t in writing_path_a if _match_completed_title(t))
            path_a_planned = sum(1 for t in writing_path_a if _match_planned_title(t))

            # Student is on Path B if they completed/are planning Expository Research Writing
            if path_b_match or (path_b_planned and path_a_done == 0):
                chosen_path = writing_path_b
            else:
                chosen_path = writing_path_a

            for title in chosen_path:
                match = _match_completed_title(title)
                if match:
                    item['courses'].append({'title': title, 'status': 'completed', 'grade': match[1]})
                elif _match_planned_title(title):
                    item['courses'].append({'title': title, 'status': 'planned', 'grade': None})
                else:
                    item['courses'].append({'title': title, 'status': 'missing', 'grade': None})

            all_done = all(c['status'] == 'completed' for c in item['courses'])
            any_prog = any(c['status'] in ('completed', 'planned') for c in item['courses'])
            item['status'] = 'completed' if all_done else ('in_progress' if any_prog else 'not_started')

        elif key == 'pathway':
            completed_areas = set()
            planned_areas = set()
            for title_lower, area in PATHWAY_TITLE_TO_AREA.items():
                title_display = title_lower.title()
                for _cnum, _meta in metadata.items():
                    if (_meta.get('title') or '').strip().lower() == title_lower:
                        title_display = (_meta.get('title') or '').strip()
                        break
                match = _match_completed_title(title_display)
                if match:
                    if area not in completed_areas:
                        item['courses'].append({'title': title_display, 'status': 'completed', 'grade': match[1], 'area': area})
                        completed_areas.add(area)
                elif _match_planned_title(title_display):
                    if area not in completed_areas and area not in planned_areas:
                        item['courses'].append({'title': title_display, 'status': 'planned', 'grade': None, 'area': area})
                        planned_areas.add(area)

            total_ok = len(completed_areas)
            total_prog = len(completed_areas) + len(planned_areas)
            if total_ok >= 3:
                item['status'] = 'completed'
            elif total_prog > 0:
                item['status'] = 'in_progress'

        elif key == 'quantitative':
            for title in req['courses']:
                match = _match_completed_title(title)
                if match:
                    item['courses'].append({'title': title, 'status': 'completed', 'grade': match[1]})
                elif _match_planned_title(title):
                    item['courses'].append({'title': title, 'status': 'planned', 'grade': None})
                else:
                    item['courses'].append({'title': title, 'status': 'missing', 'grade': None})
            if any(c['status'] == 'completed' for c in item['courses']):
                item['status'] = 'completed'
            elif any(c['status'] == 'planned' for c in item['courses']):
                item['status'] = 'in_progress'

        elif key == 'major':
            pass  # handled below

        elif key == 'total_credits':
            total_earned = sum(float(metadata.get(c, {}).get('credits') or 0) for c in completed)
            total_planned = sum(float(metadata.get(c, {}).get('credits') or 0) for c in planned)
            item['note'] = f'{round(total_earned, 1)} earned + {round(total_planned, 1)} planned of {req["credits"]} required.'
            if total_earned >= req['credits']:
                item['status'] = 'completed'
            elif total_earned + total_planned > 0:
                item['status'] = 'in_progress'
            item['earned'] = round(total_earned, 1)
            item['planned'] = round(total_planned, 1)
            item['required'] = req['credits']

        checklist.append(item)

    # ── Major-specific requirements ──
    major_checklist = None
    if major_name:
        major_reqs = get_major_requirements(major_name, entry_year, major_option=major_option)
        if major_reqs:
            major_area_courses = [
                (cnum, meta) for cnum, meta in metadata.items()
                if _area_matches_major(meta.get('area', ''), major_name)
            ]
            earned_major = 0.0
            planned_major = 0.0
            for cnum, meta in major_area_courses:
                credits = float(meta.get('credits') or 0.0)
                if cnum in completed:
                    earned_major += credits
                elif cnum in planned:
                    planned_major += credits

            designated_items = []
            for title in major_reqs.get('designated', []):
                match = _match_completed_title(title)
                if match:
                    designated_items.append({'title': title, 'status': 'completed', 'grade': match[1]})
                elif _match_planned_title(title):
                    designated_items.append({'title': title, 'status': 'planned', 'grade': None})
                else:
                    designated_items.append({'title': title, 'status': 'missing', 'grade': None})

            ds_completed = sum(1 for d in designated_items if d['status'] == 'completed')
            ds_total = len(designated_items)

            # Seminar + GRP status (Option 1, ≤2024 only)
            seminar_status = None
            grp_status = None
            if major_reqs.get('seminar_required'):
                sem_match = _match_completed_title('Seminar')
                grp_match = _match_completed_title('Graduation Research Project')
                seminar_status = 'completed' if sem_match else ('planned' if _match_planned_title('Seminar') else 'missing')
                grp_status = 'completed' if grp_match else ('planned' if _match_planned_title('Graduation Research Project') else 'missing')

            major_checklist = {
                'name': major_name,
                'total_credits': major_reqs['total_credits'],
                'lecture_credits': major_reqs['lecture_credits'],
                'earned_credits': round(earned_major, 1),
                'planned_credits': round(planned_major, 1),
                'designated': designated_items,
                'designated_completed': ds_completed,
                'designated_total': ds_total,
                'other_credits': major_reqs.get('other_credits', major_reqs['lecture_credits'] - major_reqs.get('designated_credits', 0)),
                'other_note': major_reqs.get('other_note', ''),
                'other_pick_from': major_reqs.get('other_pick_from', []),
                'has_options': major_reqs.get('has_options', False),
                'major_option': major_reqs.get('major_option'),
                'seminar_required': major_reqs.get('seminar_required', False),
                'seminar_status': seminar_status,
                'grp_status': grp_status,
                'status': 'completed' if earned_major >= major_reqs['total_credits'] and ds_completed >= ds_total
                          else ('in_progress' if earned_major + planned_major > 0 else 'not_started'),
            }

            for item in checklist:
                if item['key'] == 'major':
                    item['note'] = f'{major_name}: {round(earned_major, 1)} earned + {round(planned_major, 1)} planned of {major_reqs["total_credits"]} required.'
                    item['status'] = major_checklist['status']

    # ── Minor-specific requirements ──
    minor_checklist = None
    if minor_name:
        minor_reqs = get_minor_requirements(minor_name)
        if minor_reqs:
            level_dist = minor_reqs.get('level_distribution', {})
            grade_b_groups = minor_reqs.get('grade_b_by_group', [])
            total_needed = minor_reqs.get('total_courses', 6)

            minor_courses = []
            by_level = {}  # level -> [entry]

            for cnum_raw in minor_reqs.get('eligible_course_numbers', []):
                cnum = _normalize_course_number(cnum_raw)
                meta = metadata.get(cnum, {})
                title = (meta.get('title') or cnum_raw).strip()
                # Determine level from first digit in course number
                level_match = re.search(r'(\d)', cnum_raw)
                level = int(level_match.group(1)) * 100 if level_match else 0

                if cnum in completed:
                    grade = completed[cnum]
                    status = 'completed'
                elif cnum in planned:
                    status = 'planned'
                    grade = None
                else:
                    status = 'missing'
                    grade = None

                entry = {'course_number': cnum_raw, 'title': title, 'status': status, 'grade': grade, 'level': level}
                minor_courses.append(entry)
                by_level.setdefault(level, []).append(entry)

            completed_count = sum(1 for c in minor_courses if c['status'] == 'completed')
            planned_count = sum(1 for c in minor_courses if c['status'] == 'planned')

            # Level distribution status
            level_status = {}
            for level, needed in sorted(level_dist.items()):
                done = sum(1 for c in by_level.get(level, []) if c['status'] == 'completed')
                prog = sum(1 for c in by_level.get(level, []) if c['status'] == 'planned')
                level_status[level] = {
                    'needed': needed,
                    'completed': done,
                    'planned': prog,
                    'met': done >= needed,
                }

            # Grade B by level-group
            grade_b_status = []
            for group in grade_b_groups:
                group_levels = group['levels']
                group_needed = group['needed']
                group_label = group['label']
                count_b = 0
                for c in minor_courses:
                    if c['level'] in group_levels and c['status'] == 'completed':
                        if _is_grade_b_or_higher(c['grade']):
                            count_b += 1
                grade_b_status.append({
                    'label': group_label,
                    'needed': group_needed,
                    'count': count_b,
                    'met': count_b >= group_needed,
                })

            minor_checklist = {
                'name': minor_name,
                'total_courses': total_needed,
                'completed_courses': completed_count,
                'planned_courses': planned_count,
                'courses': minor_courses,
                'level_status': level_status,
                'grade_b_status': grade_b_status,
                'status': 'completed' if (completed_count >= total_needed
                                          and all(ls['met'] for ls in level_status.values())
                                          and all(gb['met'] for gb in grade_b_status))
                          else ('in_progress' if completed_count + planned_count > 0 else 'not_started'),
            }

    return {
        'checklist': checklist,
        'major_checklist': major_checklist,
        'minor_checklist': minor_checklist,
        'major_name': major_name,
        'minor_name': minor_name,
        'entry_year': entry_year,
        'major_option': major_option,
    }


def _area_matches_major(area_name, major_name):
    """Check if a course's area matches the student's major (fuzzy)."""
    if not area_name or not major_name:
        return False
    a = area_name.strip().lower()
    m = major_name.strip().lower()
    # Direct match
    if a == m:
        return True
    # Area starts with major name (e.g., 'Psychology' matches 'Psychology - ...')
    if a.startswith(m) or m.startswith(a):
        return True
    # Data Science majors: courses in Data Science area
    if 'data science' in m and 'data science' in a:
        return True
    # Handle sub-areas (e.g., 'Interdisciplinary Arts - Language Arts' for 'Interdisciplinary Arts')
    if ' - ' in a:
        parent = a.split(' - ')[0].strip()
        if parent == m:
            return True
    return False


def _load_catalog_for_term(year: int, semester: str) -> tuple[list[Dict[str, Any]], str | None]:
    rows, dataset_id = _load_scraped_catalog_for_term(year, semester)
    if not rows:
        return [], None
    if dataset_id:
        return rows, f"scraped_dataset_{dataset_id}"
    return rows, f"unified_catalog_{year}_{semester}"


def _filter_rows_by_level(catalog_rows: list[Dict[str, Any]], schedule_level: int | None, level_scope: str) -> list[Dict[str, Any]]:
    if level_scope != 'my' or schedule_level not in {1, 2, 3, 4}:
        return catalog_rows

    filtered = [
        row
        for row in catalog_rows
        if _parse_schedule_level(row.get('year_level')) == schedule_level
    ]
    return filtered if filtered else catalog_rows

@student_bp.route('/courses/search')
def search_courses():
    # Supports two modes:
    # 1) Search: q/query provided (>=2 chars) -> returns matching courses
    # 2) Catalog load for schedule editor: json=true & schedule_id -> returns full catalog

    query_str = (request.args.get('query') or request.args.get('q') or '').strip()
    json_mode = str(request.args.get('json', '')).lower() in {'1', 'true', 'yes'}

    schedule_id = request.args.get('schedule_id')
    year = request.args.get('year')
    semester = request.args.get('semester')

    # Resolve dataset table by schedule (preferred for the editor), otherwise by year/semester.
    resolved_year = year
    resolved_semester = semester

    schedule_level = None
    if schedule_id:
        try:
            schedule_id_int = int(schedule_id)
        except Exception:
            return jsonify([])

        if current_user.is_authenticated:
            sched = Schedule.query.filter_by(id=schedule_id_int, user_id=current_user.id).first()
        else:
            sched = Schedule.query.filter_by(id=schedule_id_int, session_id=get_guest_id()).first()

        if not sched:
            return jsonify([])

        resolved_year = sched.year
        resolved_semester = sched.semester
        schedule_level = _parse_schedule_level(getattr(sched, 'level', None))

    level_scope = str(request.args.get('level_scope') or '').strip().lower()
    if level_scope not in {'my', 'all'}:
        level_scope = 'my'

    catalog_rows, dataset_id = _load_scraped_catalog_for_term(resolved_year, resolved_semester)
    if not catalog_rows:
        return jsonify([])
    filtered_catalog_rows = _filter_rows_by_level(catalog_rows, schedule_level, level_scope)
    # Full catalog load for the schedule editor.
    if json_mode and schedule_id and query_str == '':
        try:
            payload = [_add_display_fields(dict(r)) for r in filtered_catalog_rows]

            log_behavior_event_safe(
                'catalog_loaded',
                schedule_id=schedule_id_int,
                metadata={
                    'result_count': len(payload),
                    'year': resolved_year,
                    'semester': resolved_semester,
                    'source': 'schedule_editor',
                    'dataset_id': dataset_id,
                    'level_scope': level_scope,
                    'schedule_level': schedule_level,
                },
            )
            return jsonify(payload)
        except Exception as e:
            db.session.rollback()
            print(f"Catalog load error: {e}")
            return jsonify([])

    # Search mode
    if not query_str or len(query_str) < 2:
        return jsonify([])

    log_behavior_event_safe(
        'course_search',
        schedule_id=schedule_id_int if schedule_id else None,
        metadata={
            'query': query_str,
            'year': resolved_year,
            'semester': resolved_semester,
            'source': 'search',
        },
    )

    try:
        q = query_str.lower()
        filtered = [
            row for row in filtered_catalog_rows
            if q in str(row.get('Course_Number') or '').lower()
            or q in str(row.get('Course_Title') or '').lower()
        ]

        # Optional direct filters
        day_period = (request.args.get('day_period') or '').strip().lower()
        faculty = (request.args.get('faculty') or '').strip().lower()
        subject_area = (request.args.get('subject_area') or '').strip().lower()
        credits_min = request.args.get('credits_min')
        credits_max = request.args.get('credits_max')

        if day_period:
            filtered = [r for r in filtered if day_period in str(r.get('Day_and_Period') or '').lower()]
        if faculty:
            filtered = [r for r in filtered if faculty in str(r.get('Instructor') or '').lower()]
        if subject_area:
            filtered = [r for r in filtered if subject_area in str(r.get('Area_of_Study') or '').lower()]

        def _safe_float(v):
            try:
                return float(v)
            except Exception:
                return None

        min_v = _safe_float(credits_min)
        max_v = _safe_float(credits_max)
        if min_v is not None:
            filtered = [r for r in filtered if _safe_float(r.get('Credits')) is not None and _safe_float(r.get('Credits')) >= min_v]
        if max_v is not None:
            filtered = [r for r in filtered if _safe_float(r.get('Credits')) is not None and _safe_float(r.get('Credits')) <= max_v]

        payload = [_add_display_fields(dict(r)) for r in filtered[:200]]
        return jsonify(payload)
    except Exception as e:
        db.session.rollback()
        print(f"Search error: {e}")
        return jsonify([])


def _build_unlocks_for_course(catalog_rows: list[Dict[str, Any]], target_row: Dict[str, Any]) -> list[dict]:
    normalized_target_code = _normalize_course_number(target_row.get('Course_Number'))
    target_code_token = normalize_course_code_for_prereqs(target_row.get('Course_Number'))
    target_title_token = normalize_course_code_for_prereqs(target_row.get('Course_Title'))
    target_tokens = {v for v in [target_code_token, target_title_token] if v}

    unlocks: list[dict] = []
    seen_unlocks: set[tuple[str, str]] = set()

    for row in catalog_rows:
        row_code = _normalize_course_number(row.get('Course_Number'))
        row_day = str(row.get('Day_and_Period') or '').strip()
        if row_code == normalized_target_code and row_day.lower() == str(target_row.get('Day_and_Period') or '').strip().lower():
            continue

        prereq_value = str(row.get('Prerequisite') or '').strip()
        if not prereq_value:
            continue

        is_unlocked = False
        for term in _split_prereq_terms(prereq_value):
            if not _looks_like_trackable_course_term(term):
                continue
            norm_term = normalize_course_code_for_prereqs(term)
            if norm_term and norm_term in target_tokens:
                is_unlocked = True
                break

        if not is_unlocked:
            continue

        dedupe_key = (row_code, row_day.lower())
        if dedupe_key in seen_unlocks:
            continue
        seen_unlocks.add(dedupe_key)

        mapped = _add_display_fields(dict(row))
        instructor_text = _normalize_instructor(mapped.get('Instructor'))
        unlocks.append(
            {
                'course_number': mapped.get('Course_Number'),
                'display_course_number': mapped.get('display_course_number'),
                'course_title': mapped.get('Course_Title'),
                'display_title': mapped.get('display_title'),
                'instructor': instructor_text,
                'credits': mapped.get('Credits'),
                'prerequisite': mapped.get('Prerequisite'),
            }
        )

    unlocks.sort(
        key=lambda row: (
            str(row.get('course_number') or ''),
            str(row.get('display_title') or row.get('course_title') or ''),
        )
    )
    return unlocks


@student_bp.route('/api/course_detail', methods=['GET'])
def api_course_detail():
    try:
        schedule_id_raw = request.args.get('schedule_id')
        course_number_raw = str(request.args.get('course_number') or '').strip()
        day_period_raw = str(request.args.get('day_and_period') or '').strip().lower()

        if not course_number_raw:
            return jsonify({'success': False, 'message': 'course_number is required.'}), 400

        sched = None
        if schedule_id_raw not in (None, ''):
            try:
                schedule_id = int(schedule_id_raw)
            except Exception:
                return jsonify({'success': False, 'message': 'Invalid schedule_id.'}), 400

            if current_user.is_authenticated:
                sched = Schedule.query.filter_by(id=schedule_id, user_id=current_user.id).first()
            else:
                sched = Schedule.query.filter_by(id=schedule_id, session_id=get_guest_id()).first()

            if not sched:
                return jsonify({'success': False, 'message': 'Unauthorized.'}), 403

        if sched:
            catalog_rows, dataset_id = _load_scraped_catalog_for_term(sched.year, sched.semester)
            resolved_dataset_name = _resolve_contextual_dataset_name(sched.year, sched.semester)
        else:
            catalog_rows, dataset_id = _load_scraped_catalog_for_term('', '')
            resolved_dataset_name = _resolve_contextual_dataset_name('', '')

        if not catalog_rows:
            return jsonify({'success': False, 'message': 'No course data available for selected year/semester'}), 404

        normalized_target_code = _normalize_course_number(course_number_raw)
        matches = [
            row
            for row in catalog_rows
            if _normalize_course_number(row.get('Course_Number')) == normalized_target_code
        ]
        if not matches:
            return jsonify({'success': False, 'message': 'Course not found in catalog.'}), 404

        target = matches[0]
        if day_period_raw:
            for row in matches:
                if str(row.get('Day_and_Period') or '').strip().lower() == day_period_raw:
                    target = row
                    break

        target_row = _add_display_fields(dict(target))
        metadata = _build_course_metadata_index().get(normalized_target_code, {})
        if not target_row.get('Course_Title'):
            target_row['Course_Title'] = metadata.get('title') or normalized_target_code
        if target_row.get('Credits') in (None, ''):
            target_row['Credits'] = metadata.get('credits', 0.0)
        if not target_row.get('Day_and_Period'):
            target_row['Day_and_Period'] = metadata.get('day_and_period') or 'TBA'
        target_row['Instructor'] = _normalize_instructor(target_row.get('Instructor') or metadata.get('instructor')) or 'TBA'

        completed_identifiers: Iterable[str] = []
        major_name = ''
        minor_name = ''
        if current_user.is_authenticated:
            completed_identifiers = _completed_passing_prereq_identifiers(current_user.id)
            user_areas = _resolve_user_area_names()
            major_name = user_areas.get('major') or ''
            minor_name = user_areas.get('minor') or ''

        prereq_status = _build_prerequisite_status(
            target_row.get('Prerequisite'),
            completed_identifiers,
            major_name=major_name,
            minor_name=minor_name,
            prereq_title_lookup=_build_prereq_title_lookup(),
        )
        latest_catalog_rows, latest_dataset_id = _load_scraped_catalog_for_term('', '')
        unlock_source_rows = latest_catalog_rows if latest_catalog_rows else catalog_rows
        unlocks = _build_unlocks_for_course(unlock_source_rows, target_row)

        return jsonify(
            {
                'success': True,
                'dataset_id': dataset_id,
                'resolved_dataset_name': resolved_dataset_name or None,
                'unlock_dataset_id': latest_dataset_id,
                'course': {
                    'course_number': target_row.get('Course_Number'),
                    'display_course_number': target_row.get('display_course_number'),
                    'course_title': target_row.get('Course_Title'),
                    'display_title': target_row.get('display_title'),
                    'credits': target_row.get('Credits'),
                    'instructor': target_row.get('Instructor'),
                    'area_of_study': target_row.get('Area_of_Study'),
                    'prerequisite': target_row.get('Prerequisite'),
                },
                'prerequisite_status': prereq_status,
                'unlocks': unlocks[:250],
                'unlock_count': len(unlocks),
            }
        )
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error('Course detail lookup failed: %s', exc, exc_info=True)
        return jsonify({'success': False, 'message': 'Could not load course details right now.'}), 500


@student_bp.route('/api/course-details/<int:course_id>', methods=['GET'])
def api_course_details_graph():
    try:
        course = Course.query.get(course_id)
        if not course:
            return jsonify({'success': False, 'message': 'Course not found.'}), 404

        completed_identifiers: Iterable[str] = []
        major_name = ''
        minor_name = ''
        if current_user.is_authenticated:
            completed_identifiers = _completed_passing_prereq_identifiers(current_user.id)
            user_areas = _resolve_user_area_names()
            major_name = user_areas.get('major') or ''
            minor_name = user_areas.get('minor') or ''

        latest_catalog_rows, latest_dataset_id = _load_scraped_catalog_for_term('', '')
        catalog_rows = _catalog_rows_with_local_course_ids(latest_catalog_rows)

        target_row = _manual_or_csv_course_to_catalog_row(course)
        target_row['course_id'] = int(course.id)
        target_row = _add_display_fields(target_row)

        target_identity = _catalog_resolution_key(
            target_row.get('Course_Number'),
            target_row.get('Day_and_Period'),
            target_row.get('semester'),
            target_row.get('year'),
        )
        if not any(
            _catalog_resolution_key(r.get('Course_Number'), r.get('Day_and_Period'), r.get('semester'), r.get('year')) == target_identity
            for r in catalog_rows
        ):
            catalog_rows.append(target_row)

        dependency_graph = dependency_graph_for_course(
            course_number=str(course.course_number or ''),
            course_title=str(course.title or ''),
            prerequisite_text=course.prerequisite,
            catalog_courses=catalog_rows,
            completed_courses=completed_identifiers,
            normalizer=normalize_course_code_for_prereqs,
        )

        prereq_status = _build_prerequisite_status(
            course.prerequisite,
            completed_identifiers,
            major_name=major_name,
            minor_name=minor_name,
            prereq_title_lookup=_build_prereq_title_lookup(),
        )

        return jsonify(
            {
                'success': True,
                'dataset_id': latest_dataset_id,
                'course': {
                    'id': int(course.id),
                    'course_number': target_row.get('Course_Number'),
                    'display_course_number': target_row.get('display_course_number'),
                    'course_title': target_row.get('Course_Title'),
                    'display_title': target_row.get('display_title'),
                    'credits': target_row.get('Credits'),
                    'instructor': _normalize_instructor(target_row.get('Instructor')) or 'TBA',
                    'area_of_study': target_row.get('Area_of_Study'),
                    'prerequisite': target_row.get('Prerequisite'),
                },
                'prerequisite_status': prereq_status,
                'dependency_graph': {
                    'direct_requirements': dependency_graph.get('direct_requirements', []),
                    'direct_requirement_count': len(dependency_graph.get('direct_requirements', [])),
                    'direct_requirements_expression': dependency_graph.get('direct_requirements_expression', ''),
                    'direct_requirements_is_conditional': bool(dependency_graph.get('direct_requirements_is_conditional')),
                    'direct_requirements_eligible_now': bool(dependency_graph.get('direct_requirements_eligible_now')),
                    'dependent_unlockables': dependency_graph.get('dependent_unlockables', [])[:500],
                    'dependent_unlockable_count': len(dependency_graph.get('dependent_unlockables', [])),
                    'conditional_paths': dependency_graph.get('conditional_paths', [])[:500],
                    'conditional_path_count': len(dependency_graph.get('conditional_paths', [])),
                    'parse_errors': dependency_graph.get('parse_errors', []),
                },
            }
        )
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error('Course dependency graph lookup failed: %s', exc, exc_info=True)
        return jsonify({'success': False, 'message': 'Could not load course dependency graph right now.'}), 500


@student_bp.route('/api/catalog_course_detail', methods=['GET'])
@login_required
def api_catalog_course_detail():
    """Full course detail for the catalog browser — includes dependency graph."""
    try:
        course_number_raw = str(request.args.get('course_number') or '').strip()

        if not course_number_raw:
            return jsonify({'success': False, 'message': 'course_number is required.'}), 400

        combined = _build_combined_course_catalog()
        if not combined:
            return jsonify({'success': False, 'message': 'No course data available.'}), 404

        normalized_target = _normalize_course_number(course_number_raw)
        target_row = None
        for r in combined:
            if _normalize_course_number(r.get('Course_Number')) == normalized_target:
                target_row = dict(r)
                break
        if not target_row:
            return jsonify({'success': False, 'message': 'Course not found in catalog.'}), 404

        target_row = _add_display_fields(target_row)
        metadata = _build_course_metadata_index().get(normalized_target, {})
        if not target_row.get('Course_Title'):
            target_row['Course_Title'] = metadata.get('title') or normalized_target
        if target_row.get('Credits') in (None, ''):
            target_row['Credits'] = metadata.get('credits', 0.0)
        target_row['Instructor'] = _normalize_instructor(target_row.get('Instructor') or metadata.get('instructor')) or 'TBA'

        completed_identifiers = _completed_passing_prereq_identifiers(current_user.id)
        area_names = _resolve_user_area_names()

        prereq_status = _build_prerequisite_status(
            target_row.get('Prerequisite'),
            completed_identifiers,
            major_name=area_names.get('major'),
            minor_name=area_names.get('minor'),
            prereq_title_lookup=_build_prereq_title_lookup(),
        )

        # Use the full combined catalog for dependency scanning
        catalog_for_graph = _catalog_rows_with_local_course_ids(combined)
        dep_graph = dependency_graph_for_course(
            course_number=str(target_row.get('Course_Number') or ''),
            course_title=str(target_row.get('Course_Title') or ''),
            prerequisite_text=target_row.get('Prerequisite'),
            catalog_courses=catalog_for_graph,
            completed_courses=completed_identifiers,
            normalizer=normalize_course_code_for_prereqs,
        )

        # Areas — already derived in the combined catalog
        areas = target_row.get('areas') or _derive_areas_from_course_number(target_row.get('Course_Number'))

        # Sections: collect ALL rows for this course across all datasets
        all_course_rows = [
            _manual_or_csv_course_to_catalog_row(r) for r in
            Course.query.filter(func.upper(Course.course_number) == normalized_target).all()
        ]
        seen_sections: set[tuple[str, str]] = set()
        sections = []
        for s in all_course_rows:
            inst = _normalize_instructor(s.get('Instructor')) or 'TBA'
            dp = str(s.get('Day_and_Period') or 'TBA')
            sem = _normalize_semester_label(s.get('semester'))
            key = (dp.lower(), sem.lower())
            if key in seen_sections:
                continue
            seen_sections.add(key)
            sections.append({
                'day_period': dp,
                'instructor': inst,
                'semester': sem,
            })

        unlockables = dep_graph.get('dependent_unlockables', [])[:200]
        conditional_paths = dep_graph.get('conditional_paths', [])[:200]
        conditional_lookup = {}
        for cp in conditional_paths:
            key = str(cp.get('unlock_course_number') or '').upper()
            if key and key not in conditional_lookup:
                conditional_lookup[key] = cp

        return jsonify({
            'success': True,
            'course': {
                'course_number': target_row.get('Course_Number'),
                'display_course_number': target_row.get('display_course_number'),
                'course_title': target_row.get('Course_Title'),
                'display_title': target_row.get('display_title'),
                'credits': target_row.get('Credits'),
                'instructor': target_row.get('Instructor'),
                'area_of_study': target_row.get('Area_of_Study'),
                'areas': areas,
                'prerequisite': target_row.get('Prerequisite'),
                'year_level': target_row.get('year_level'),
                'semesters': target_row.get('semesters', ''),
            },
            'sections': sections,
            'prerequisite_status': prereq_status,
            'dependency_graph': {
                'direct_requirements': dep_graph.get('direct_requirements', []),
                'expression': dep_graph.get('direct_requirements_expression', ''),
                'is_conditional': bool(dep_graph.get('direct_requirements_is_conditional')),
                'eligible_now': bool(dep_graph.get('direct_requirements_eligible_now')),
                'unlockables': unlockables,
                'unlock_count': len(dep_graph.get('dependent_unlockables', [])),
                'conditional_paths': conditional_paths,
                'conditional_lookup': conditional_lookup,
            },
        })
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error('catalog_course_detail failed: %s', exc, exc_info=True)
        return jsonify({'success': False, 'message': 'Could not load course details right now.'}), 500


@student_bp.route('/courses')
@login_required
def courses_view():
    all_rows: list[dict] = []
    subject_options: list[str] = []
    year_level_options: list[int] = []
    semester_options: list[str] = []
    credit_options: list[float] = []

    try:
        all_rows = _build_combined_course_catalog()
        subject_options = sorted({str(r.get('Area_of_Study') or '').strip() for r in all_rows if str(r.get('Area_of_Study') or '').strip()})
        year_level_options = sorted({int(r.get('year_level') or 0) for r in all_rows if r.get('year_level')})
        semester_options = sorted({str(r.get('semesters') or '').strip() for r in all_rows if str(r.get('semesters') or '').strip()})
        credit_options = sorted({float(r.get('Credits') or 0) for r in all_rows if r.get('Credits')})
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error('courses_view failed: %s', exc, exc_info=True)

    return render_template(
        'Student/courses.html',
        courses=all_rows,
        subject_options=subject_options,
        year_level_options=year_level_options,
        semester_options=semester_options,
        credit_options=credit_options,
    )

# --- Premium Dashboard Logic ---
@student_bp.route('/dashboard')
@login_required
def user_dashboard():
    schedules = Schedule.query.filter_by(user_id=current_user.id).order_by(Schedule.year.desc()).all()
    past_schedules = [s for s in schedules if s.schedule_type == 'past']
    dataset_years = get_available_dataset_years()

    total_credits = 0.0
    area_credits = {}
    top_faculty = {}
    top_periods = {}
    overall_courses_for_gpa = []
    future_courses_for_predictive = []

    metadata_index = _build_course_metadata_index()
    try:
        joined_rows = db.session.execute(
            text(
                '''
                SELECT s.schedule_type, sc.grade, sc.course_number
                FROM schedule_courses sc
                JOIN schedules s ON sc.schedule_id = s.id
                WHERE s.user_id = :uid
                '''
            ),
            {'uid': current_user.id},
        ).fetchall()

        for row in joined_rows:
            cnum = _normalize_course_number(row.course_number)
            meta = metadata_index.get(cnum, {})
            credits_value = float(meta.get('credits') or 0.0)
            instructor = str(meta.get('instructor') or '').strip()
            area = str(meta.get('area') or '').strip() or 'Unspecified'
            day_and_period = str(meta.get('day_and_period') or '').strip()
            grade = str(row.grade or '').strip()
            schedule_type = str(row.schedule_type or '').strip().lower()

            if schedule_type == 'past':
                if grade and grade.upper() != 'F':
                    total_credits += credits_value
                    area_credits[area] = area_credits.get(area, 0) + credits_value

                if grade:
                    overall_courses_for_gpa.append({'grade': grade, 'Credits': credits_value})

                if instructor:
                    top_faculty[instructor] = top_faculty.get(instructor, 0) + 1

                if day_and_period:
                    days = re.findall(r'[A-Za-z]+', day_and_period)
                    for d in days:
                        if len(d) >= 3:
                            top_periods[d] = top_periods.get(d, 0) + 1

            if schedule_type == 'future' and grade:
                future_courses_for_predictive.append({'grade': grade, 'Credits': credits_value})

    except Exception as e:
        db.session.rollback()
        print(f"Error calculating dashboard analytics: {e}")

    overall_gpa = calculate_gpa(overall_courses_for_gpa) if overall_courses_for_gpa else 0.0

    # Predictive GPA = Combined past + future expected
    combined_for_predictive = overall_courses_for_gpa + future_courses_for_predictive
    predictive_gpa = calculate_gpa(combined_for_predictive) if combined_for_predictive else overall_gpa

    progress = _degree_progress_context(current_user.id)
    earned_credits = float(progress.get('earned_credits', total_credits))
    in_progress_credits = float(progress.get('in_progress_credits', 0.0))
    projected_credits = float(progress.get('projected_credits', earned_credits + in_progress_credits))
    graduation_required_credits = float(progress.get('graduation_required_credits', 124.0))
    remaining_to_graduate = float(progress.get('remaining_to_graduate', max(0.0, graduation_required_credits - projected_credits)))

    favorite_faculty = max(top_faculty, key=top_faculty.get) if top_faculty else "N/A"
    favorite_day = max(top_periods, key=top_periods.get) if top_periods else "N/A"

    ai_enabled = get_bool_setting('ai_enabled', False)
    ai_data_collection_enabled = get_bool_setting('ai_data_collection_enabled', False)

    notifications = []
    unread_notifications_count = 0
    try:
        notifications = (
            Notification.query.filter_by(user_id=current_user.id)
            .order_by(Notification.created_at.desc())
            .limit(5)
            .all()
        )
        unread_notifications_count = (
            Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
        )
    except Exception as e:
        db.session.rollback()
        print(f"Dashboard notification load failed: {e}")

    schedule_ids = [int(s.id) for s in schedules]
    schedule_courses_by_schedule: dict[int, list[dict[str, Any]]] = {sid: [] for sid in schedule_ids}
    if schedule_ids:
        try:
            rows = (
                db.session.query(ScheduleCourse.schedule_id, ScheduleCourse.course_number)
                .filter(ScheduleCourse.schedule_id.in_(schedule_ids))
                .all()
            )
            for row in rows:
                sid = int(row.schedule_id)
                code = _normalize_course_number(row.course_number)
                meta = metadata_index.get(code, {})
                day_period = str(meta.get('day_and_period') or '').strip()
                instructor = _normalize_instructor(meta.get('instructor') or '') or 'TBA'
                title = str(meta.get('title') or row.course_number or '').strip()
                schedule_courses_by_schedule.setdefault(sid, []).append(
                    {
                        'day_period': day_period,
                        'title': title,
                        'faculty': instructor,
                    }
                )
        except Exception:
            db.session.rollback()

    # ── Per-schedule GPA + credits breakdown ──
    schedule_grade_data: dict[int, list[dict]] = {int(s.id): [] for s in schedules}
    if schedule_ids:
        try:
            grade_rows = db.session.execute(
                text(
                    '''
                    SELECT sc.schedule_id, sc.course_number, sc.grade
                    FROM schedule_courses sc
                    WHERE sc.schedule_id IN :sids
                    '''
                ),
                {'sids': tuple(schedule_ids)},
            ).fetchall()
            for row in grade_rows:
                sid = int(row.schedule_id)
                code = _normalize_course_number(row.course_number)
                meta = metadata_index.get(code, {})
                credits_val = float(meta.get('credits') or 0.0)
                schedule_grade_data.setdefault(sid, []).append({
                    'grade': str(row.grade or '').strip(),
                    'Credits': credits_val,
                })
        except Exception:
            db.session.rollback()

    dashboard_mini_schedules: list[dict[str, Any]] = []
    cumulative_courses: list[dict] = []
    for sched in schedules:
        slot_rows = schedule_courses_by_schedule.get(int(sched.id), [])
        sched_courses = schedule_grade_data.get(int(sched.id), [])
        sched_gpa = calculate_gpa(sched_courses) if sched_courses else 0.0
        sched_credits = sum(
            c['Credits'] for c in sched_courses
            if c.get('grade') and c['grade'].upper() != 'F'
        )
        # Cumulative GPA up to this schedule (chronological, past first)
        if sched.schedule_type == 'past':
            cumulative_courses.extend(sched_courses)
        cumulative_gpa = calculate_gpa(cumulative_courses) if cumulative_courses else 0.0

        dashboard_mini_schedules.append(
            {
                'id': sched.id,
                'name': sched.schedule_name,
                'semester': sched.semester,
                'year': sched.year,
                'schedule_type': sched.schedule_type,
                'slots': slot_rows[:25],
                'gpa': round(sched_gpa, 2),
                'credits': round(sched_credits, 1),
                'cumulative_gpa': round(cumulative_gpa, 2),
                'course_count': len(sched_courses),
            }
        )

    # ── Build area progress data for interactive bars ──
    in_progress_by_area: Dict[str, float] = {}
    for row in joined_rows:
        schedule_type_r = str(row.schedule_type or '').strip().lower()
        if schedule_type_r != 'future':
            continue
        cnum_r = _normalize_course_number(row.course_number)
        meta_r = metadata_index.get(cnum_r, {})
        area_r = str(meta_r.get('area') or '').strip() or 'Unspecified'
        credits_r = float(meta_r.get('credits') or 0.0)
        in_progress_by_area[area_r] = in_progress_by_area.get(area_r, 0.0) + credits_r

    # Total available credits per area (all courses in catalog)
    total_available_by_area: Dict[str, float] = {}
    for _cnum, _meta in metadata_index.items():
        _area = str(_meta.get('area') or '').strip()
        if _area:
            _cr = float(_meta.get('credits') or 0.0)
            total_available_by_area[_area] = total_available_by_area.get(_area, 0.0) + _cr

    # Only show required/remaining for user's declared major/minor areas
    user_area_names = _resolve_user_area_names()
    major_area_name = (user_area_names.get('major') or '').strip()
    minor_area_name = (user_area_names.get('minor') or '').strip()
    requirement_areas = set()
    if major_area_name:
        requirement_areas.add(major_area_name)
    if minor_area_name:
        requirement_areas.add(minor_area_name)

    area_progress = []
    all_area_names = set(area_credits.keys()) | set(in_progress_by_area.keys()) | set(progress.get('missing_by_area', {}).keys())
    for area_name in sorted(all_area_names):
        earned_a = area_credits.get(area_name, 0.0)
        in_prog_a = in_progress_by_area.get(area_name, 0.0)
        total_avail = total_available_by_area.get(area_name, 0.0)
        req_a = 0.0
        if area_name in requirement_areas:
            for a_key, a_val in progress.get('missing_by_area', {}).items():
                if a_key == area_name:
                    req_a = earned_a + a_val
                    break
            if req_a <= 0:
                active = _active_subject_areas()
                area_lower = area_name.strip().lower()
                if area_lower in active:
                    sa = active[area_lower]
                    if area_name == major_area_name:
                        req_a = float(getattr(sa, 'min_major_credits', 0.0) or 0.0)
                    elif area_name == minor_area_name:
                        req_a = float(getattr(sa, 'min_minor_credits', 0.0) or 0.0)
        area_progress.append({
            'name': area_name,
            'earned': round(earned_a, 1),
            'in_progress': round(in_prog_a, 1),
            'total_available': round(total_avail, 1),
            'required': round(req_a, 1) if area_name in requirement_areas else 0.0,
            'remaining': round(max(0.0, req_a - earned_a - in_prog_a), 1) if area_name in requirement_areas and req_a > 0 else 0.0,
            'is_major': area_name == major_area_name,
            'is_minor': area_name == minor_area_name,
        })

    # ── Graduation requirements checklist ──
    req_year_param = request.args.get('req_year', type=int)
    major_option_param = request.args.get('major_option', default=1, type=int)
    if major_option_param not in (1, 2):
        major_option_param = 1
    grad_reqs = _graduation_requirements_context(current_user.id,
                                                 entry_year_override=req_year_param,
                                                 major_option=major_option_param)

    return render_template('Student/dashboard.html', 
                           schedules=schedules, 
                           has_past=len(past_schedules) > 0,
                           total_credits=earned_credits,
                           in_progress_credits=in_progress_credits,
                           projected_credits=projected_credits,
                           graduation_required_credits=graduation_required_credits,
                           remaining_to_graduate=remaining_to_graduate,
                           area_credits=area_credits,
                           area_progress=area_progress,
                           overall_gpa=overall_gpa,
                           predictive_gpa=predictive_gpa,
                           favorite_faculty=favorite_faculty,
                           favorite_day=favorite_day,
                           ai_enabled=ai_enabled,
                           ai_data_collection_enabled=ai_data_collection_enabled,
                           has_dataset_years=bool(dataset_years),
                           dataset_years=dataset_years,
                           dashboard_mini_schedules=dashboard_mini_schedules,
                           notifications=notifications,
                           unread_notifications_count=unread_notifications_count,
                           grad_reqs=grad_reqs)


@student_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    majors = SubjectArea.query.filter(
        SubjectArea.is_active.is_(True),
        or_(SubjectArea.area_type == 'major', SubjectArea.area_type == 'both'),
    ).order_by(SubjectArea.name.asc()).all()
    minors = SubjectArea.query.filter(
        SubjectArea.is_active.is_(True),
        or_(SubjectArea.area_type == 'minor', SubjectArea.area_type == 'both'),
    ).order_by(SubjectArea.name.asc()).all()

    extra_profile = _safe_json_load_dict(getattr(current_user, 'extra_profile_json', None))
    major_optional_choice = str(extra_profile.get('major_optional_choice') or 'undeclared').strip().lower()
    minor_optional_choice = str(extra_profile.get('minor_optional_choice') or 'undeclared').strip().lower()

    if request.method == 'POST':
        full_name = (request.form.get('full_name') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        gender = (request.form.get('gender') or '').strip() or None
        career_interests = (request.form.get('career_interests') or '').strip() or None
        major_raw = (request.form.get('major_subject_area_id') or '').strip().lower()
        minor_raw = (request.form.get('minor_subject_area_id') or '').strip().lower()
        major_subject_area_id = None if major_raw in {'', 'undeclared', 'prefer_not_to_say'} else request.form.get('major_subject_area_id', type=int)
        minor_subject_area_id = None if minor_raw in {'', 'undeclared', 'prefer_not_to_say'} else request.form.get('minor_subject_area_id', type=int)

        if not full_name:
            flash('Name is required.', 'error')
            return render_template('Student/profile.html', majors=majors, minors=minors)

        email_regex = r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$'
        if not email or not re.match(email_regex, email):
            flash('Please provide a valid email address.', 'error')
            return render_template('Student/profile.html', majors=majors, minors=minors)

        existing_email = User.query.filter(User.email == email, User.id != current_user.id).first()
        if existing_email:
            flash('Email already in use by another account.', 'error')
            return render_template('Student/profile.html', majors=majors, minors=minors, major_optional_choice=major_optional_choice, minor_optional_choice=minor_optional_choice)

        if gender not in {None, 'Male', 'Female', 'Prefer not to say'}:
            flash('Invalid gender value.', 'error')
            return render_template('Student/profile.html', majors=majors, minors=minors, major_optional_choice=major_optional_choice, minor_optional_choice=minor_optional_choice)

        current_user.full_name = full_name
        current_user.email = email
        current_user.gender = gender
        current_user.career_interests = career_interests
        current_user.major_subject_area_id = major_subject_area_id
        current_user.minor_subject_area_id = minor_subject_area_id

        updated_extra = _safe_json_load_dict(getattr(current_user, 'extra_profile_json', None))
        updated_extra['major_optional_choice'] = major_raw if major_raw in {'undeclared', 'prefer_not_to_say'} else 'declared'
        updated_extra['minor_optional_choice'] = minor_raw if minor_raw in {'undeclared', 'prefer_not_to_say'} else 'declared'
        current_user.extra_profile_json = json.dumps(updated_extra)

        image_file = request.files.get('profile_image')
        if image_file and image_file.filename:
            if not _allowed_profile_image(image_file.filename):
                flash('Profile image must be png, jpg, jpeg, webp, or gif.', 'error')
                return render_template('Student/profile.html', majors=majors, minors=minors)
            allowed_mimes = {'image/png', 'image/jpeg', 'image/webp', 'image/gif'}
            if (image_file.mimetype or '').lower() not in allowed_mimes:
                flash('Invalid image type.', 'error')
                return render_template('Student/profile.html', majors=majors, minors=minors)

            ext = os.path.splitext(secure_filename(image_file.filename))[1].lower()
            filename = secure_filename(f"user_{current_user.id}_{uuid.uuid4().hex}{ext}")
            profile_dir = current_app.config.get('PROFILE_UPLOAD_FOLDER', os.path.join('uploads', 'profile_images'))
            os.makedirs(profile_dir, exist_ok=True)
            file_path = os.path.join(profile_dir, filename)
            image_file.save(file_path)
            current_user.profile_image_path = file_path

        old_password = request.form.get('old_password') or ''
        new_password = request.form.get('new_password') or ''
        confirm_new_password = request.form.get('confirm_new_password') or ''

        if new_password or confirm_new_password:
            if not old_password or not check_password_hash(current_user.password, old_password):
                flash('Old password is incorrect.', 'error')
                return render_template('Student/profile.html', majors=majors, minors=minors, major_optional_choice=major_optional_choice, minor_optional_choice=minor_optional_choice)
            if new_password != confirm_new_password:
                flash('New passwords do not match.', 'error')
                return render_template('Student/profile.html', majors=majors, minors=minors, major_optional_choice=major_optional_choice, minor_optional_choice=minor_optional_choice)
            if len(new_password) < 8:
                flash('New password must be at least 8 characters.', 'error')
                return render_template('Student/profile.html', majors=majors, minors=minors, major_optional_choice=major_optional_choice, minor_optional_choice=minor_optional_choice)
            current_user.password = generate_password_hash(new_password, method='pbkdf2:sha256')

        db.session.commit()
        flash('Profile updated successfully.', 'success')
        return redirect(url_for('student.profile'))

    return render_template('Student/profile.html', majors=majors, minors=minors, major_optional_choice=major_optional_choice, minor_optional_choice=minor_optional_choice)


@student_bp.route('/preferences', methods=['GET'])
@login_required
def preferences():
    return render_template('Student/preferences.html')


@student_bp.route('/profile/image', methods=['GET'])
@login_required
def profile_image():
    image_path = str(getattr(current_user, 'profile_image_path', '') or '').strip()
    if not image_path or not os.path.isfile(image_path):
        return redirect(url_for('static', filename='img/acadex-icon.svg'))
    return send_file(image_path)


@student_bp.route('/notifications/<int:notif_id>/mark_read', methods=['POST'])
@login_required
def mark_notification_read(notif_id: int):
    try:
        n = Notification.query.filter_by(id=notif_id, user_id=current_user.id).first()
        if not n:
            flash('Notification not found.', 'error')
            return redirect(url_for('student.user_dashboard'))

        if not n.is_read:
            n.is_read = True
            n.read_at = datetime.utcnow()
            db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Mark notification read failed: {e}")
        flash('Could not update notification right now.', 'error')

    return redirect(url_for('student.user_dashboard'))


@student_bp.route('/notifications/mark_all_read', methods=['POST'])
@login_required
def mark_all_notifications_read():
    try:
        Notification.query.filter_by(user_id=current_user.id, is_read=False).update(
            {'is_read': True, 'read_at': datetime.utcnow()}, synchronize_session=False
        )
        db.session.commit()
        flash('Notifications marked as read.', 'success')
    except Exception as e:
        db.session.rollback()
        print(f"Mark notifications read failed: {e}")
        flash('Could not update notifications right now.', 'error')
    return redirect(url_for('student.user_dashboard'))


@student_bp.route('/api/notifications', methods=['GET'])
def api_notifications_list():
    try:
        limit = int(request.args.get('limit', '20'))
    except Exception:
        limit = 20
    try:
        offset = int(request.args.get('offset', '0'))
    except Exception:
        offset = 0

    limit = max(1, min(100, limit))
    offset = max(0, offset)

    try:
        q = _notifications_base_query_for_request().order_by(Notification.created_at.desc())
        items = q.offset(offset).limit(limit).all()
        return jsonify({'success': True, 'notifications': [_serialize_notification(n) for n in items]})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': 'Could not load notifications'}), 500


@student_bp.route('/api/notifications/unread_count', methods=['GET'])
def api_notifications_unread_count():
    try:
        if current_user.is_authenticated:
            cnt = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
            return jsonify({'success': True, 'unread': int(cnt)})

        seen = _guest_seen_notification_ids()
        guest_id = get_guest_id()

        # Broadcast unread count is computed using session seen ids.
        broadcast_ids = [r[0] for r in db.session.query(Notification.id).filter(
            Notification.user_id.is_(None), Notification.session_id.is_(None)
        ).all()]
        broadcast_unread = sum(1 for nid in broadcast_ids if nid not in seen)

        # Session-scoped guest notifications can use DB is_read.
        guest_unread = Notification.query.filter_by(session_id=guest_id, is_read=False).count()
        return jsonify({'success': True, 'unread': int(broadcast_unread + guest_unread)})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': 'Could not load unread count'}), 500


@student_bp.route('/api/notifications/<int:notif_id>/read', methods=['POST'])
def api_notifications_mark_read(notif_id: int):
    try:
        if current_user.is_authenticated:
            n = Notification.query.filter_by(id=notif_id, user_id=current_user.id).first()
            if not n:
                return jsonify({'success': False, 'message': 'Not found'}), 404
            if not n.is_read:
                n.is_read = True
                n.read_at = datetime.utcnow()
                db.session.commit()
            return jsonify({'success': True})

        # Guest: broadcast read state is stored in the session.
        n = Notification.query.filter_by(id=notif_id).first()
        if not n:
            return jsonify({'success': False, 'message': 'Not found'}), 404

        if n.user_id is None and n.session_id is None:
            seen = _guest_seen_notification_ids()
            seen.add(int(notif_id))
            _set_guest_seen_notification_ids(seen)
            return jsonify({'success': True})

        guest_id = get_guest_id()
        if n.session_id != guest_id:
            return jsonify({'success': False, 'message': 'Not found'}), 404

        if not n.is_read:
            n.is_read = True
            n.read_at = datetime.utcnow()
            db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': 'Could not update notification'}), 500


@student_bp.route('/api/notifications/mark_all_read', methods=['POST'])
def api_notifications_mark_all_read():
    try:
        if current_user.is_authenticated:
            Notification.query.filter_by(user_id=current_user.id, is_read=False).update(
                {'is_read': True, 'read_at': datetime.utcnow()}, synchronize_session=False
            )
            db.session.commit()
            return jsonify({'success': True})

        # Guest: mark broadcast as seen in session, mark guest session notifications in DB.
        seen = _guest_seen_notification_ids()
        broadcast_ids = [r[0] for r in db.session.query(Notification.id).filter(
            Notification.user_id.is_(None), Notification.session_id.is_(None)
        ).all()]
        for nid in broadcast_ids:
            try:
                seen.add(int(nid))
            except Exception:
                continue
        _set_guest_seen_notification_ids(seen)

        guest_id = get_guest_id()
        Notification.query.filter_by(session_id=guest_id, is_read=False).update(
            {'is_read': True, 'read_at': datetime.utcnow()}, synchronize_session=False
        )
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': 'Could not update notifications'}), 500


@student_bp.route('/ai/consent', methods=['POST'])
@login_required
def ai_consent():
    payload = request.get_json(silent=True) if request.is_json else None
    action = (payload or {}).get('action') if request.is_json else request.form.get('action')
    action = (action or '').strip().lower()

    if action not in {'opt_in', 'opt_out'}:
        if request.is_json:
            return jsonify({'success': False, 'message': 'Invalid action'}), 400
        flash('Invalid AI preference update.', 'error')
        return redirect(url_for('student.user_dashboard'))

    current_user.ai_opt_in = (action == 'opt_in')
    db.session.commit()

    if request.is_json:
        return jsonify({'success': True, 'ai_opt_in': bool(current_user.ai_opt_in)})

    flash('AI preference updated.', 'success')
    return redirect(url_for('student.user_dashboard'))

@student_bp.route('/schedule/create', methods=['GET', 'POST'])
def create_schedule():
    dataset_years = get_available_dataset_years()
    current_year = _current_year_setting_value()
    registry_terms = _registry_terms_index()

    if request.method == 'POST':
        name = (request.form.get('schedule_name') or '').strip()[:150]
        year = request.form.get('year')
        semester = normalize_dataset_semester((request.form.get('semester') or '').strip()[:50])
        
        if not current_user.is_authenticated:
            sched_type = 'future'
        else:
            sched_type = request.form.get('schedule_type', 'future')
        sched_type = str(sched_type or 'future').strip().lower()
        if sched_type not in {'past', 'future'}:
            sched_type = 'future'
        schedule_level = _parse_schedule_level(request.form.get('level'))

        if not all([name, year, semester]):
            flash("Please fill out all fields.", "error")
            return redirect(
                url_for('student.user_dashboard')
                if current_user.is_authenticated
                else url_for('student.create_schedule', schedule_type='future')
            )

        try:
            year_int = int(year)
        except (TypeError, ValueError):
            flash("Please enter a valid year.", "error")
            return redirect(
                url_for('student.user_dashboard')
                if current_user.is_authenticated
                else url_for('student.create_schedule', schedule_type='future')
            )

        resolved_year, resolved_semester, fallback_warning = _resolve_term_with_fallback(
            year_int,
            semester,
            sched_type,
            current_year=current_year,
            term_index=registry_terms,
        )
        if resolved_year is None or not resolved_semester:
            flash("No course data available for selected year/semester", "error")
            return redirect(
                url_for('student.user_dashboard')
                if current_user.is_authenticated
                else url_for('student.create_schedule', schedule_type='future')
            )

        resolved_dataset = resolve_dataset(resolved_year, resolved_semester)
        if resolved_dataset is None:
            flash("No course data available for selected year/semester", "error")
            return redirect(
                url_for('student.user_dashboard')
                if current_user.is_authenticated
                else url_for('student.create_schedule', schedule_type='future')
            )

        if fallback_warning:
            flash(fallback_warning, 'warning')

        new_sched = Schedule(
            schedule_name=name,
            year=resolved_year,
            semester=resolved_semester.title(),
            schedule_type=sched_type,
            level=schedule_level,
        )

        if current_user.is_authenticated:
            new_sched.user_id = current_user.id
        else:
            new_sched.session_id = get_guest_id()

        try:
            db.session.add(new_sched)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Create schedule error: {e}")
            flash(
                "Could not create schedule. If this persists, ensure the database is migrated (flask db upgrade).",
                "error",
            )
            return redirect(
                url_for('student.user_dashboard')
                if current_user.is_authenticated
                else url_for('student.create_schedule', schedule_type='future')
            )
        
        if current_user.is_authenticated:
            log_ai_event_safe(
                'schedule_created',
                schedule_id=new_sched.id,
                metadata={
                    'schedule_type': new_sched.schedule_type,
                    'year': new_sched.year,
                    'semester': new_sched.semester,
                    'level': new_sched.level,
                },
            )
            _update_learning_profile_snapshot_safe(current_user.id)

        log_behavior_event_safe(
            'schedule_created',
            schedule_id=new_sched.id,
            metadata={
                'schedule_type': new_sched.schedule_type,
                'year': new_sched.year,
                'semester': new_sched.semester,
                'level': new_sched.level,
            },
        )

        return redirect(url_for('student.edit_schedule', schedule_id=new_sched.id))
        
    if current_user.is_authenticated:
        requested_type = (request.args.get('schedule_type') or '').strip().lower()
        if not requested_type:
            return redirect(url_for('auth.choose_schedule_type'))

    schedule_type = (request.args.get('schedule_type') or 'future').strip().lower()
    if schedule_type not in {'past', 'future'}:
        schedule_type = 'future'
    if not current_user.is_authenticated:
        schedule_type = 'future'

    eligible_terms = _eligible_term_index(registry_terms, schedule_type, current_year)
    available_term_map: dict[int, list[str]] = {
        int(year): [s.title() for s in sorted(semesters)]
        for year, semesters in sorted(eligible_terms.items())
        if semesters
    }
    available_years = sorted(available_term_map.keys())

    suggestions_allowed = False
    suggestions_gate_message = None
    if current_user.is_authenticated and schedule_type == 'future':
        suggestions_allowed, suggestions_gate_message = _student_can_receive_ai_suggestions(current_user.id)

    return render_template(
        'Student/create_schedule.html',
        available_years=available_years,
        available_term_map=available_term_map,
        current_year=current_year,
        future_max_year=current_year + 5,
        schedule_type=schedule_type,
        has_dataset_years=bool(available_years),
        ai_enabled=get_bool_setting('ai_enabled', False),
        ai_recommendations_enabled=get_bool_setting('ai_recommendations_enabled', True),
        ai_data_collection_enabled=get_bool_setting('ai_data_collection_enabled', False),
        default_level=_parse_schedule_level(getattr(current_user, 'year_of_study', None)) if current_user.is_authenticated else None,
        user_area_names=_resolve_user_area_names() if current_user.is_authenticated else {'major': '', 'minor': ''},
        ai_suggestions_allowed=suggestions_allowed,
        ai_suggestions_gate_message=suggestions_gate_message,
    )

@student_bp.route('/schedule/edit/<int:schedule_id>')
def edit_schedule(schedule_id):
    if current_user.is_authenticated:
        schedule = Schedule.query.filter_by(id=schedule_id, user_id=current_user.id).first_or_404()
    else:
        schedule = Schedule.query.filter_by(
            id=schedule_id,
            session_id=get_guest_id(),
            schedule_type='future',
        ).first_or_404()

    log_behavior_event_safe('schedule_editor_view', schedule_id=schedule.id)
        
    current_courses = []
    try:
        rows = _schedule_course_rows_for_schedule(schedule.id)
        catalog_rows, _dataset_id = _load_scraped_catalog_for_term(schedule.year, schedule.semester)
        current_courses = _hydrate_schedule_courses(schedule, rows, catalog_rows)
    except Exception as e:
        db.session.rollback()
        print(f"Error fetching current courses: {e}")
    if not current_courses:
        current_courses = _load_schedule_courses_fallback(schedule.id)

    obtained_courses = []
    if current_user.is_authenticated:
        try:
            obtained_query = text(f'''
                SELECT sc.course_number
                FROM schedule_courses sc
                JOIN schedules s ON s.id = sc.schedule_id
                WHERE s.user_id = :uid 
                AND s.schedule_type = 'past'
                AND sc.grade IS NOT NULL
                AND sc.grade != ''
                AND sc.grade != 'F'
            ''')
            obtained_result = db.session.execute(obtained_query, {'uid': current_user.id}).fetchall()
            obtained_courses = [r[0] for r in obtained_result]
        except Exception as e:
            db.session.rollback()
            print(f"Error fetching obtained courses: {e}")

    periods = Period.query.order_by(Period.name.asc()).all()

    lunch_start_time = get_str_setting('lunch_start_time', '11:40')
    lunch_duration_minutes = get_str_setting('lunch_duration_minutes', '60')
    try:
        lunch_duration_minutes_int = int(lunch_duration_minutes)
    except Exception:
        lunch_duration_minutes_int = 60

    # Compute end time as a convenience for the template.
    lunch_end_time = None
    try:
        parts = lunch_start_time.split(':')
        if len(parts) == 2:
            h = int(parts[0])
            m = int(parts[1])
            total = h * 60 + m + lunch_duration_minutes_int
            lunch_end_time = f"{(total // 60) % 24:02d}:{total % 60:02d}"
    except Exception:
        lunch_end_time = None

    return render_template('Student/edit_schedule.html', 
                           schedule=schedule, 
                           current_courses=current_courses,
                           obtained_courses=obtained_courses,
                           schedule_level=_parse_schedule_level(getattr(schedule, 'level', None)),
                           periods=periods,
                           lunch_start_time=lunch_start_time,
                           lunch_end_time=lunch_end_time)


@student_bp.route('/api/recommendations_preview', methods=['GET'])
@login_required
def api_recommendations_preview():
    year_raw = request.args.get('year')
    semester = normalize_dataset_semester((request.args.get('semester') or '').strip())

    try:
        year = int(year_raw)
    except Exception:
        return jsonify({'success': False, 'message': 'Invalid year'}), 400
    if semester not in {'spring', 'fall'}:
        return jsonify({'success': False, 'message': 'Invalid semester'}), 400

    schedule_type = (request.args.get('schedule_type') or 'future').strip().lower()
    if schedule_type != 'future':
        return jsonify({'success': True, 'recommendations': [], 'message': 'Suggestions are only available for future schedules.'})

    try:
        limit = int(request.args.get('limit', 5))
    except Exception:
        limit = 5
    limit = max(1, min(limit, 5))

    current_year = _current_year_setting_value()
    resolved_year, resolved_semester, fallback_warning = _resolve_term_with_fallback(
        year,
        semester,
        'future',
        current_year=current_year,
        term_index=_registry_terms_index(),
    )
    if resolved_year is None or not resolved_semester:
        return jsonify({'success': False, 'message': 'No course data available for selected year/semester'}), 400

    log_behavior_event_safe(
        'ai_preview_requested',
        metadata={'year': resolved_year, 'semester': resolved_semester, 'limit': limit},
    )

    if not get_bool_setting('ai_enabled', False) or not get_bool_setting('ai_recommendations_enabled', True):
        log_ai_event_safe(
            'ai_preview_blocked',
            metadata={'year': resolved_year, 'semester': resolved_semester, 'reason': 'feature_disabled'},
        )
        return jsonify({'success': False, 'message': 'AI recommendations are currently disabled.'}), 403

    if not bool(getattr(current_user, 'ai_opt_in', False)):
        log_ai_event_safe(
            'ai_preview_blocked',
            metadata={'year': resolved_year, 'semester': resolved_semester, 'reason': 'opt_in_required'},
        )
        return jsonify({'success': False, 'message': 'AI recommendations require opt-in.'}), 403

    catalog, table_name = _load_catalog_for_term(resolved_year, resolved_semester)
    if not table_name:
        log_ai_event_safe(
            'ai_preview_blocked',
            metadata={'year': resolved_year, 'semester': resolved_semester, 'reason': 'catalog_unavailable'},
        )
        return jsonify({'success': False, 'message': 'No course data available for selected year/semester'}), 400

    progress = _degree_progress_context(current_user.id)
    area_names = _resolve_user_area_names()
    prereq_title_lookup = _build_prereq_title_lookup()
    eligible, gate_reason = _student_can_receive_ai_suggestions(current_user.id)
    if not eligible:
        return jsonify(
            {
                'success': True,
                'recommendations': [],
                'message': gate_reason,
                'context': {
                    'major': area_names.get('major') or None,
                    'minor': area_names.get('minor') or None,
                    'missing_credits_by_area': progress.get('missing_by_area', {}),
                    'remaining_to_graduate': progress.get('remaining_to_graduate', 0.0),
                    'graduation_required_credits': progress.get('graduation_required_credits', 124.0),
                },
            }
        )

    completed = progress['completed']
    completed_prereq_identifiers = progress.get('completed_prereq_identifiers', completed)
    filtered_catalog = [
        c for c in catalog
        if _normalize_course_number(c.get('Course_Number')) not in completed
    ]

    signals = _build_learning_signals(
        current_user.id,
        filtered_catalog,
        major_subject_area_id=getattr(current_user, 'major_subject_area_id', None),
        minor_subject_area_id=getattr(current_user, 'minor_subject_area_id', None),
    )

    recs, errors = recommend_courses(
        filtered_catalog,
        completed_prereq_identifiers,
        limit=limit,
        major_area=area_names.get('major'),
        minor_area=area_names.get('minor'),
        missing_credits_by_area=progress.get('missing_by_area', {}),
        historical_area_counts=progress.get('history_counts', {}),
        remaining_credits_to_graduate=float(progress.get('remaining_to_graduate', 0.0)),
        peer_popularity_by_course=signals.get('peer_popularity_by_course', {}),
        behavior_affinity_by_course=signals.get('behavior_affinity_by_course', {}),
        ai_affinity_by_course=signals.get('ai_affinity_by_course', {}),
        faculty_success_by_name=signals.get('faculty_success_by_name', {}),
        course_avg_grade_points=signals.get('course_avg_grade_points', {}),
    )

    payload = [_add_display_fields(dict(r)) for r in recs]
    for row in payload:
        row['prerequisite_status'] = _build_prerequisite_status(
            row.get('Prerequisite'),
            completed_prereq_identifiers,
            major_name=area_names.get('major'),
            minor_name=area_names.get('minor'),
            prereq_title_lookup=prereq_title_lookup,
        )

    log_ai_event_safe(
        'ai_preview_generated',
        metadata={
            'year': resolved_year,
            'semester': resolved_semester,
            'table_name': table_name,
            'recommendation_count': len(payload),
            'parse_error_count': len(errors),
        },
    )

    response_payload = {
        'success': True,
        'recommendations': payload,
        'context': {
            'major': area_names.get('major') or None,
            'minor': area_names.get('minor') or None,
            'missing_credits_by_area': progress.get('missing_by_area', {}),
            'remaining_to_graduate': progress.get('remaining_to_graduate', 0.0),
            'graduation_required_credits': progress.get('graduation_required_credits', 124.0),
        },
    }
    if fallback_warning:
        response_payload['message'] = fallback_warning

    return jsonify(response_payload)


@student_bp.route('/api/schedule/<int:schedule_id>/courses', methods=['GET'])
def api_schedule_courses(schedule_id: int):
    if current_user.is_authenticated:
        schedule = Schedule.query.filter_by(id=schedule_id, user_id=current_user.id).first()
    else:
        schedule = Schedule.query.filter_by(
            id=schedule_id,
            session_id=get_guest_id(),
            schedule_type='future',
        ).first()

    if not schedule:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403

    log_behavior_event_safe('schedule_courses_loaded', schedule_id=schedule.id)

    try:
        rows = _schedule_course_rows_for_schedule(schedule.id)

        catalog_rows, _dataset_id = _load_scraped_catalog_for_term(schedule.year, schedule.semester)
        courses = _hydrate_schedule_courses(schedule, rows, catalog_rows)

        if not courses:
            courses = _load_schedule_courses_fallback(schedule.id)
        return jsonify({'success': True, 'courses': courses})
    except Exception as e:
        db.session.rollback()
        print(f"Error fetching schedule courses for {schedule_id}: {e}")
        return jsonify({'success': False, 'message': 'Could not load schedule courses'}), 500

@student_bp.route('/api/add_course', methods=['POST'])
def add_course():
    data = request.get_json(silent=True) or {}
    sched_id = data.get('schedule_id')
    course_num = (data.get('course_number') or '').strip()
    day_period_raw = str(data.get('day_period') or data.get('day_and_period') or '').strip()
    semester_raw = normalize_dataset_semester(data.get('semester') or '')
    year_raw = str(data.get('year') or '').strip()
    dataset_name_raw = str(data.get('dataset_name') or '').strip()
    source_raw = str(data.get('source') or '').strip().lower()

    try:
        sched_id = int(sched_id)
    except (TypeError, ValueError):
        sched_id = None

    if not sched_id or not course_num:
        return jsonify({'success': False, 'message': 'Missing data'}), 400

    if current_user.is_authenticated:
        sched = Schedule.query.filter_by(id=sched_id, user_id=current_user.id).first()
    else:
        sched = Schedule.query.filter_by(id=sched_id, session_id=get_guest_id(), schedule_type='future').first()
        
    if not sched:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403

    # Prevent duplicates (identity-aware when section context is provided).
    existing_query = ScheduleCourse.query.filter_by(schedule_id=sched.id, course_number=course_num)
    if day_period_raw:
        existing_query = existing_query.filter(
            func.lower(func.coalesce(ScheduleCourse.day_period, '')) == day_period_raw.lower(),
            func.lower(func.coalesce(ScheduleCourse.semester, '')) == (semester_raw or normalize_dataset_semester(sched.semester)),
            func.lower(func.coalesce(ScheduleCourse.year, '')) == (year_raw or str(sched.year)).lower(),
            func.lower(func.coalesce(ScheduleCourse.dataset_name, '')) == dataset_name_raw.lower(),
        )
    existing = existing_query.first()
    if existing:
        return (
            jsonify(
                {
                    'success': False,
                    'error': 'Course already in schedule',
                    'message': 'Course already in schedule.'
                }
            ),
            400,
        )

    # --- Prerequisite enforcement ---
    catalog_rows, dataset_id = _load_scraped_catalog_for_term(sched.year, sched.semester)
    if not catalog_rows:
        print(f"Prereq check blocked: missing scraped dataset for schedule {sched.id}")
        return (
            jsonify(
                {
                    'success': False,
                    'error': 'Unable to validate prerequisites',
                    'message': 'No course data available for selected year/semester'
                }
            ),
            400,
        )

    course_map = {
        _normalize_course_number(r.get('Course_Number')): r
        for r in catalog_rows
        if _normalize_course_number(r.get('Course_Number'))
    }
    normalized_course = _normalize_course_number(course_num)
    candidate = None
    if day_period_raw:
        for row in catalog_rows:
            if _normalize_course_number(row.get('Course_Number')) != normalized_course:
                continue
            if str(row.get('Day_and_Period') or '').strip().lower() == day_period_raw.lower():
                candidate = row
                break

    if candidate is None:
        candidate = course_map.get(normalized_course)

    if not candidate:
        return (
            jsonify(
                {
                    'success': False,
                    'error': 'Unknown course',
                    'message': 'Unknown course.'
                }
            ),
            400,
        )

    prereq_str = candidate.get('Prerequisite')
    candidate_day_period = candidate.get('Day_and_Period')
    persisted_day_period = str(candidate_day_period or day_period_raw or 'TBA').strip() or 'TBA'
    persisted_semester = normalize_dataset_semester(candidate.get('semester') or semester_raw or sched.semester)
    persisted_year = str(candidate.get('year') or year_raw or sched.year).strip()
    persisted_dataset_name = str(candidate.get('dataset_name') or dataset_name_raw).strip()
    persisted_source = str(candidate.get('source') or source_raw).strip().lower()

    completed_passing = []
    if current_user.is_authenticated:
        try:
            completed_passing = list(_completed_passing_prereq_identifiers(current_user.id))
        except Exception as e:
            db.session.rollback()
            print(f"Error fetching completed courses for prereq check (user {current_user.id}): {e}")
            return (
                jsonify(
                    {
                        'success': False,
                        'error': 'Unable to validate prerequisites',
                        'message': 'Could not validate prerequisites right now.'
                    }
                ),
                400,
            )

    user_areas = _resolve_user_area_names() if current_user.is_authenticated else {'major': '', 'minor': ''}
    prereq_title_lookup = _build_prereq_title_lookup()
    prereq_status = _build_prerequisite_status(
        prereq_str,
        completed_passing,
        major_name=user_areas.get('major'),
        minor_name=user_areas.get('minor'),
        prereq_title_lookup=prereq_title_lookup,
    )

    # --- Conflict enforcement ---
    try:
        existing_rows = _schedule_course_rows_for_schedule(sched.id)
        period_map = _period_time_map()
        occupied_windows: list[tuple[str, int, int]] = []
        meta_index = _build_course_metadata_index()
        for r in existing_rows:
            code = _normalize_course_number(r.get('course_number'))
            existing = course_map.get(code)
            existing_dp = str(r.get('day_period') or '').strip()
            if not existing_dp:
                existing_dp = (existing or {}).get('Day_and_Period') if existing else None
            if not existing_dp:
                existing_dp = meta_index.get(code, {}).get('day_and_period')
            occupied_windows.extend(_time_windows_from_day_period(existing_dp, period_map))

        candidate_windows = _time_windows_from_day_period(candidate_day_period, period_map)
        if _windows_overlap(occupied_windows, candidate_windows):
            return (
                jsonify(
                    {
                        'success': False,
                        'error': 'Schedule conflict',
                        'message': 'Schedule conflict.'
                    }
                ),
                400,
            )
    except Exception as e:
        db.session.rollback()
        print(f"Error checking conflicts for {course_num} on schedule {sched.id}, dataset {dataset_id}: {e}")
        return (
            jsonify(
                {
                    'success': False,
                    'error': 'Unable to validate schedule conflicts',
                    'message': 'Could not validate schedule conflicts right now.'
                }
            ),
            400,
        )

    new_course = ScheduleCourse(
        schedule_id=sched_id,
        course_number=course_num,
        day_period=persisted_day_period,
        semester=persisted_semester,
        year=persisted_year,
        dataset_name=persisted_dataset_name or None,
        source=persisted_source or None,
    )
    db.session.add(new_course)
    db.session.commit()

    if current_user.is_authenticated:
        log_ai_event_safe(
            'course_added',
            schedule_id=sched.id,
            metadata={'course_number': course_num},
        )
        _update_learning_profile_snapshot_safe(current_user.id)

    log_behavior_event_safe(
        'schedule_course_added',
        schedule_id=sched.id,
        metadata={'course_number': course_num},
    )

    response_payload = {'success': True, 'prerequisite_status': prereq_status}
    if (
        (sched.schedule_type or '').strip().lower() == 'future'
        and prereq_status.get('warning_message')
    ):
        response_payload['message'] = prereq_status.get('warning_message')
    return jsonify(response_payload)

@student_bp.route('/api/remove_course/<int:entry_id>', methods=['DELETE', 'POST'])
def remove_course(entry_id):
    course_entry = ScheduleCourse.query.get(entry_id)
    if not course_entry:
        return jsonify({'success': False, 'message': 'Course not found'}), 404
        
    if current_user.is_authenticated:
        if course_entry.schedule.user_id != current_user.id:
            return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    else:
        if course_entry.schedule.schedule_type != 'future':
            return jsonify({'success': False, 'message': 'Unauthorized'}), 403
        if course_entry.schedule.session_id != get_guest_id():
            return jsonify({'success': False, 'message': 'Unauthorized'}), 403

    schedule_id = course_entry.schedule_id
    course_number = course_entry.course_number
    db.session.delete(course_entry)
    db.session.commit()

    if current_user.is_authenticated:
        log_ai_event_safe(
            'course_removed',
            schedule_id=schedule_id,
            metadata={'course_number': course_number, 'entry_id': entry_id},
        )
        _update_learning_profile_snapshot_safe(current_user.id)

    log_behavior_event_safe(
        'schedule_course_removed',
        schedule_id=schedule_id,
        metadata={'course_number': course_number, 'entry_id': entry_id},
    )

    return jsonify({'success': True})


@student_bp.route('/api/remove_course', methods=['POST'])
def remove_course_post():
    data = request.get_json(silent=True) or request.form
    entry_id = data.get('entry_id') or data.get('id')
    if not entry_id:
        return jsonify({'success': False, 'message': 'Missing entry_id'}), 400
    try:
        entry_id = int(entry_id)
    except Exception:
        return jsonify({'success': False, 'message': 'Invalid entry_id'}), 400

    return remove_course(entry_id)


@student_bp.route('/api/schedule/<int:schedule_id>', methods=['DELETE', 'POST'])
@login_required
def delete_schedule_api(schedule_id: int):
    sched = Schedule.query.filter_by(id=schedule_id, user_id=current_user.id).first()
    if not sched:
        return jsonify({'success': False, 'message': 'Schedule not found'}), 404

    deleted_type = sched.schedule_type
    deleted_name = sched.schedule_name

    try:
        ScheduleCourse.query.filter_by(schedule_id=sched.id).delete(synchronize_session=False)
        AIEvent.query.filter_by(schedule_id=sched.id).delete(synchronize_session=False)
        BehaviorEvent.query.filter_by(schedule_id=sched.id).delete(synchronize_session=False)
        db.session.delete(sched)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Failed to delete schedule: {e}'}), 500

    log_ai_event_safe(
        'schedule_deleted',
        metadata={'schedule_id': schedule_id, 'schedule_type': deleted_type},
    )
    log_behavior_event_safe(
        'schedule_deleted',
        metadata={'schedule_id': schedule_id, 'schedule_type': deleted_type, 'schedule_name': deleted_name},
    )
    _update_learning_profile_snapshot_safe(current_user.id)

    remaining = Schedule.query.filter_by(user_id=current_user.id).count()
    return jsonify({'success': True, 'deleted_schedule_id': schedule_id, 'remaining_schedules': remaining})

@student_bp.route('/api/update_grade', methods=['POST'])
@login_required
def update_grade():
    data = request.get_json(silent=True) or {}
    entry_id = data.get('entry_id')
    grade = data.get('grade')

    if entry_id is None:
        return jsonify({'success': False, 'message': 'Missing entry_id'}), 400
    try:
        entry_id_int = int(entry_id)
    except Exception:
        return jsonify({'success': False, 'message': 'Invalid entry_id'}), 400

    allowed_grades = {'', None, 'A', 'B', 'C', 'D', 'F'}
    if grade not in allowed_grades:
        return jsonify({'success': False, 'message': 'Invalid grade'}), 400

    course_entry = ScheduleCourse.query.get(entry_id_int)
    if not course_entry:
        return jsonify({'success': False, 'message': 'Not found'}), 404

    # Defense-in-depth: even though this route is login-protected, ensure the target
    # entry belongs to a registered user's schedule (not a guest/session schedule).
    if course_entry.schedule.user_id is None:
        return jsonify({'success': False, 'message': 'Grades are only available for registered users'}), 403
    if course_entry.schedule.user_id != current_user.id:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403

    course_entry.grade = grade
    db.session.commit()

    log_ai_event_safe(
        'grade_updated',
        schedule_id=course_entry.schedule_id,
        metadata={'course_number': course_entry.course_number, 'entry_id': entry_id_int, 'grade': grade},
    )
    _update_learning_profile_snapshot_safe(current_user.id)

    log_behavior_event_safe(
        'schedule_grade_updated',
        schedule_id=course_entry.schedule_id,
        metadata={'course_number': course_entry.course_number, 'entry_id': entry_id_int, 'action': 'grade_updated'},
    )

    return jsonify({'success': True})


@student_bp.route('/api/recommendations', methods=['GET'])
def api_recommendations():
    if not current_user.is_authenticated:
        return jsonify({'success': False, 'error': 'Unauthorized', 'message': 'Unauthorized'}), 403

    sched_id = request.args.get('schedule_id')
    if not sched_id:
        return jsonify({'success': False, 'error': 'Missing schedule_id', 'message': 'Missing schedule_id'}), 400
    try:
        sched_id_int = int(sched_id)
    except Exception:
        return jsonify({'success': False, 'error': 'Invalid schedule_id', 'message': 'Invalid schedule_id'}), 400

    sched = Schedule.query.filter_by(id=sched_id_int, user_id=current_user.id).first()
    if not sched:
        return jsonify({'success': False, 'error': 'Unauthorized', 'message': 'Unauthorized'}), 403
    if (sched.schedule_type or '').strip().lower() != 'future':
        return jsonify({'success': False, 'error': 'Invalid schedule type', 'message': 'AI schedule generation is only available for future schedules.'}), 400
    if (sched.schedule_type or '').strip().lower() != 'future':
        return jsonify({'success': True, 'recommendations': [], 'message': 'Suggestions are only available for future schedules.'})

    eligible, gate_reason = _student_can_receive_ai_suggestions(current_user.id)
    if not eligible:
        return jsonify({'success': True, 'recommendations': [], 'message': gate_reason})

    if not get_bool_setting('ai_enabled', False):
        log_ai_event_safe('ai_recommendations_blocked', schedule_id=sched.id, metadata={'reason': 'ai_disabled'})
        log_behavior_event_safe('ai_recommendations_blocked', schedule_id=sched.id, metadata={'reason': 'ai_disabled'})
        return jsonify({'success': False, 'error': 'AI disabled', 'message': 'AI features are currently disabled.'}), 403
    if not get_bool_setting('ai_recommendations_enabled', True):
        log_ai_event_safe('ai_recommendations_blocked', schedule_id=sched.id, metadata={'reason': 'recommendations_disabled'})
        log_behavior_event_safe('ai_recommendations_blocked', schedule_id=sched.id, metadata={'reason': 'recommendations_disabled'})
        return jsonify({'success': False, 'error': 'Recommendations disabled', 'message': 'AI recommendations are currently disabled.'}), 403
    if not bool(getattr(current_user, 'ai_opt_in', False)):
        log_ai_event_safe('ai_recommendations_blocked', schedule_id=sched.id, metadata={'reason': 'opt_in_required'})
        log_behavior_event_safe('ai_recommendations_blocked', schedule_id=sched.id, metadata={'reason': 'opt_in_required'})
        return jsonify({'success': False, 'error': 'Opt-in required', 'message': 'AI requires user opt-in.'}), 403

    try:
        limit = int(request.args.get('limit', 5))
    except Exception:
        limit = 5
    limit = max(1, min(limit, 5))

    log_behavior_event_safe(
        'ai_recommendations_requested',
        schedule_id=sched.id,
        metadata={'year': sched.year, 'semester': sched.semester, 'limit': limit},
    )

    catalog, table_name = _load_catalog_for_term(sched.year, sched.semester)
    if not table_name:
        log_ai_event_safe('ai_recommendations_blocked', schedule_id=sched.id, metadata={'reason': 'catalog_unavailable'})
        return jsonify({'success': False, 'error': 'Catalog unavailable', 'message': 'No course data available for selected year/semester'}), 400

    progress = _degree_progress_context(current_user.id)
    area_names = _resolve_user_area_names()
    prereq_title_lookup = _build_prereq_title_lookup()
    completed_passing = progress.get('completed', set())
    completed_prereq_identifiers = progress.get('completed_prereq_identifiers', completed_passing)

    # Avoid recommending courses already in the schedule.
    try:
        in_sched_rows = db.session.execute(
            text('SELECT course_number FROM schedule_courses WHERE schedule_id = :sid'),
            {'sid': sched.id},
        ).fetchall()
        in_sched = {_normalize_course_number(r[0]) for r in in_sched_rows if r and r[0]}
        catalog = [
            c for c in catalog
            if _normalize_course_number(c.get('Course_Number')) not in in_sched
            and _normalize_course_number(c.get('Course_Number')) not in completed_passing
        ]
    except Exception as e:
        db.session.rollback()
        print(f"Error checking existing schedule courses for recommendations: {e}")

    signals = _build_learning_signals(
        current_user.id,
        catalog,
        major_subject_area_id=getattr(current_user, 'major_subject_area_id', None),
        minor_subject_area_id=getattr(current_user, 'minor_subject_area_id', None),
    )

    recs, errors = recommend_courses(
        catalog,
        completed_prereq_identifiers,
        limit=limit,
        major_area=area_names.get('major'),
        minor_area=area_names.get('minor'),
        missing_credits_by_area=progress.get('missing_by_area', {}),
        historical_area_counts=progress.get('history_counts', {}),
        remaining_credits_to_graduate=float(progress.get('remaining_to_graduate', 0.0)),
        peer_popularity_by_course=signals.get('peer_popularity_by_course', {}),
        behavior_affinity_by_course=signals.get('behavior_affinity_by_course', {}),
        ai_affinity_by_course=signals.get('ai_affinity_by_course', {}),
        faculty_success_by_name=signals.get('faculty_success_by_name', {}),
        course_avg_grade_points=signals.get('course_avg_grade_points', {}),
    )
    for err in errors:
        print(f"Recommendation prereq parse error: {err}")

    recs = [_add_display_fields(dict(r)) for r in recs]
    for row in recs:
        row['prerequisite_status'] = _build_prerequisite_status(
            row.get('Prerequisite'),
            completed_prereq_identifiers,
            major_name=area_names.get('major'),
            minor_name=area_names.get('minor'),
            prereq_title_lookup=prereq_title_lookup,
        )

    log_ai_event_safe(
        'ai_recommendations',
        schedule_id=sched.id,
        metadata={
            'limit': limit,
            'recommendation_count': len(recs),
            'table_name': table_name,
            'year': sched.year,
            'semester': sched.semester,
        },
    )
    log_behavior_event_safe(
        'ai_recommendations_served',
        schedule_id=sched.id,
        metadata={'limit': limit, 'recommendation_count': len(recs)},
    )
    return jsonify({'success': True, 'recommendations': recs})


@student_bp.route('/api/generate_schedule', methods=['POST'])
def api_generate_schedule():
    if not current_user.is_authenticated:
        return jsonify({'success': False, 'error': 'Unauthorized', 'message': 'Unauthorized'}), 403

    data = request.get_json(silent=True) or {}
    sched_id = data.get('schedule_id')
    target_count = data.get('target_count', 5)

    if sched_id:
        try:
            sched_id_int = int(sched_id)
            sched_for_logs = Schedule.query.filter_by(id=sched_id_int, user_id=current_user.id).first()
        except Exception:
            sched_for_logs = None
    else:
        sched_for_logs = None

    log_behavior_event_safe(
        'ai_generate_requested',
        schedule_id=sched_for_logs.id if sched_for_logs else None,
        metadata={'target_count': target_count},
    )

    if not get_bool_setting('ai_enabled', False):
        log_ai_event_safe('ai_generate_blocked', schedule_id=sched_for_logs.id if sched_for_logs else None, metadata={'reason': 'ai_disabled'})
        return jsonify({'success': False, 'error': 'AI disabled', 'message': 'AI features are currently disabled.'}), 403
    if not get_bool_setting('ai_predictive_analysis_enabled', True):
        log_ai_event_safe('ai_generate_blocked', schedule_id=sched_for_logs.id if sched_for_logs else None, metadata={'reason': 'predictive_disabled'})
        return jsonify({'success': False, 'error': 'Predictive analysis disabled', 'message': 'AI predictive analysis is currently disabled.'}), 403
    if not bool(getattr(current_user, 'ai_opt_in', False)):
        log_ai_event_safe('ai_generate_blocked', schedule_id=sched_for_logs.id if sched_for_logs else None, metadata={'reason': 'opt_in_required'})
        return jsonify({'success': False, 'error': 'Opt-in required', 'message': 'AI requires user opt-in.'}), 403

    if not sched_id:
        return jsonify({'success': False, 'error': 'Missing schedule_id', 'message': 'Missing schedule_id'}), 400
    try:
        sched_id_int = int(sched_id)
    except Exception:
        return jsonify({'success': False, 'error': 'Invalid schedule_id', 'message': 'Invalid schedule_id'}), 400
    try:
        target_count = int(target_count)
    except Exception:
        target_count = 5

    sched = Schedule.query.filter_by(id=sched_id_int, user_id=current_user.id).first()
    if not sched:
        return jsonify({'success': False, 'error': 'Unauthorized', 'message': 'Unauthorized'}), 403

    catalog, dataset_name = _load_catalog_for_term(sched.year, sched.semester)
    if not dataset_name or not catalog:
        return jsonify({'success': False, 'error': 'Catalog unavailable', 'message': 'No course data available for selected year/semester'}), 400

    completed_passing = []
    if current_user.is_authenticated:
        try:
            completed_passing = list(_completed_passing_prereq_identifiers(current_user.id))
        except Exception as e:
            db.session.rollback()
            print(f"Error fetching completed courses for generator (user {current_user.id}): {e}")
            return jsonify({'success': False, 'error': 'Unable to validate prerequisites', 'message': 'Unable to validate prerequisites'}), 400

    try:
        rows = db.session.execute(
            text('SELECT course_number FROM schedule_courses WHERE schedule_id = :sid'),
            {'sid': sched.id},
        ).fetchall()
        catalog_map = {
            _normalize_course_number(r.get('Course_Number')): r
            for r in catalog
            if _normalize_course_number(r.get('Course_Number'))
        }
        meta_index = _build_course_metadata_index()
        existing_day_period = []
        for row in rows:
            code = _normalize_course_number(row[0])
            mapped = catalog_map.get(code)
            value = (mapped or {}).get('Day_and_Period') if mapped else None
            if not value:
                value = meta_index.get(code, {}).get('day_and_period')
            if value:
                existing_day_period.append(value)
    except Exception as e:
        db.session.rollback()
        print(f"Error loading existing day/period for generator: {e}")
        existing_day_period = []

    user_areas = _resolve_user_area_names()
    generated, errors = generate_schedule_courses(
        catalog,
        completed_passing,
        target_count=target_count,
        existing_day_and_period=existing_day_period,
        major_area=user_areas.get('major'),
        minor_area=user_areas.get('minor'),
    )
    for err in errors:
        print(f"Generator prereq parse error: {err}")

    log_ai_event_safe(
        'ai_generate_schedule',
        schedule_id=sched.id,
        metadata={'target_count': target_count, 'generated_count': len(generated), 'table_name': dataset_name},
    )

    return jsonify({'success': True, 'courses': generated})
