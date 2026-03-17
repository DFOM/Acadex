# Routes/student_routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session, current_app
from flask_login import login_required, current_user
from sqlalchemy import text, or_, and_, inspect
import uuid
import re
import json
import os
from datetime import datetime
from typing import Optional, Dict, Any, Set, Iterable
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash, generate_password_hash
from models import db, User, Schedule, ScheduleCourse, Period, Setting, Announcement, AIEvent, BehaviorEvent, Notification, SubjectArea
from utils import (
    get_best_course_table,
    get_course_data_tables,
    get_available_dataset_years,
    calculate_gpa,
    is_valid_table_name,
    semester_label_for_dataset,
    table_has_column,
    format_display_course_number,
)
from services.eligibility import normalize_course_code_for_prereqs
from services.conflicts import parse_day_period_blocks, has_conflict
from services.ai.recommendations import recommend_courses
from services.ai.generator import generate_schedule_courses

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


def _build_course_metadata_index() -> Dict[str, Dict[str, Any]]:
    tables = [t for t in get_course_data_tables() if is_valid_table_name(t)]
    if not tables:
        return {}

    try:
        insp = inspect(db.engine)
        union_parts = []
        for table_name in tables:
            cols = {c['name'] for c in insp.get_columns(table_name)}
            area_expr = '"Area_of_Study"' if 'Area_of_Study' in cols else 'NULL'
            credits_expr = '"Credits"' if 'Credits' in cols else '0'
            title_expr = '"Course_Title"' if 'Course_Title' in cols else 'NULL'
            instructor_expr = '"Instructor"' if 'Instructor' in cols else 'NULL'
            union_parts.append(
                f'''SELECT "Course_Number" as course_number,
                           {title_expr} as course_title,
                           {instructor_expr} as instructor,
                           {area_expr} as area_of_study,
                           {credits_expr} as credits
                    FROM "{table_name}"'''
            )

        sql = ' UNION ALL '.join(union_parts)
        rows = db.session.execute(
            text(
                f'''
                SELECT course_number,
                      MAX(course_title) as course_title,
                        MAX(instructor) as instructor,
                       MAX(area_of_study) as area_of_study,
                       MAX(credits) as credits
                FROM ({sql})
                GROUP BY course_number
                '''
            )
        ).fetchall()

        index: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            d = dict(r._mapping)
            course_number = _normalize_course_number(d.get('course_number'))
            if not course_number:
                continue
            try:
                credits = float(d.get('credits') or 0.0)
            except Exception:
                credits = 0.0
            index[course_number] = {
                'title': (d.get('course_title') or '').strip(),
                'instructor': (d.get('instructor') or '').strip(),
                'area': (d.get('area_of_study') or '').strip(),
                'credits': max(0.0, credits),
            }
        return index
    except Exception:
        db.session.rollback()
        return {}


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
    try:
        rows = db.session.execute(
            text('SELECT id as entry_id, grade, course_number FROM schedule_courses WHERE schedule_id = :sid'),
            {'sid': schedule_id},
        ).fetchall()
    except Exception:
        db.session.rollback()
        return []

    metadata = _build_course_metadata_index()
    payload: list[Dict[str, Any]] = []
    for row in rows:
        mapped = dict(row._mapping)
        cnum = _normalize_course_number(mapped.get('course_number'))
        meta = metadata.get(cnum, {})
        item = {
            'entry_id': mapped.get('entry_id'),
            'grade': mapped.get('grade'),
            'Course_Number': cnum,
            'Course_Title': (meta.get('title') or 'Required course title unavailable').strip(),
            'Credits': meta.get('credits', 0.0),
            'Instructor': (meta.get('instructor') or 'TBA').strip() or 'TBA',
            'Day_and_Period': 'TBA',
            'Area_of_Study': (meta.get('area') or '').strip(),
            'Prerequisite': '',
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

        if _looks_like_trackable_course_term(term):
            trackable_count += 1
            normalized_term = normalize_course_code_for_prereqs(term)
            display_term = title_lookup.get(normalized_term, 'Required course') if normalized_term else 'Required course'
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


def _load_catalog_for_term(year: int, semester: str) -> tuple[list[Dict[str, Any]], str | None]:
    table_name, needs_sem_filter = get_best_course_table(year, semester)
    if not table_name or not is_valid_table_name(table_name):
        return [], None

    semester_label = None
    if needs_sem_filter and table_has_column(table_name, 'Semester'):
        semester_label = semester_label_for_dataset(year, semester)

    try:
        sql = (
            f'''SELECT "Course_Number", "Course_Title", "Credits", "Instructor", "Day_and_Period", "Area_of_Study", "Prerequisite"
                FROM "{table_name}"'''
        )
        params: Dict[str, Any] = {}
        if semester_label:
            sql += ' WHERE "Semester" = :semester_label'
            params['semester_label'] = semester_label
        rows = db.session.execute(text(sql), params).fetchall()
        return [dict(r._mapping) for r in rows], table_name
    except Exception as e:
        db.session.rollback()
        msg = str(e)
        if 'Area_of_Study' in msg and ('no such column' in msg.lower() or 'undefined column' in msg.lower()):
            try:
                sql = (
                    f'''SELECT "Course_Number", "Course_Title", "Credits", "Instructor", "Day_and_Period", "Prerequisite"
                        FROM "{table_name}"'''
                )
                params = {}
                if semester_label:
                    sql += ' WHERE "Semester" = :semester_label'
                    params['semester_label'] = semester_label
                rows = db.session.execute(text(sql), params).fetchall()
                payload = []
                for r in rows:
                    d = dict(r._mapping)
                    d['Area_of_Study'] = None
                    payload.append(d)
                return payload, table_name
            except Exception:
                db.session.rollback()
                return [], None
        return [], None

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

    table_name, needs_sem_filter = get_best_course_table(resolved_year, resolved_semester)
    if not table_name or not is_valid_table_name(table_name):
        return jsonify([])

    semester_label = None
    if needs_sem_filter and table_has_column(table_name, 'Semester') and resolved_year and resolved_semester:
        semester_label = semester_label_for_dataset(resolved_year, resolved_semester)

    # Full catalog load for the schedule editor.
    if json_mode and schedule_id and query_str == '':
        try:
            sql = (
                f'''SELECT "Course_Number", "Course_Title", "Credits", "Instructor", "Day_and_Period", "Area_of_Study", "Prerequisite"
                    FROM "{table_name}"'''
            )
            params = {}
            if semester_label:
                sql += ' WHERE "Semester" = :semester_label'
                params['semester_label'] = semester_label
            results = db.session.execute(text(sql), params).fetchall()
            payload = [_add_display_fields(dict(r._mapping)) for r in results]

            log_behavior_event_safe(
                'catalog_loaded',
                schedule_id=schedule_id_int,
                metadata={
                    'result_count': len(payload),
                    'year': resolved_year,
                    'semester': resolved_semester,
                    'source': 'schedule_editor',
                },
            )
            return jsonify(payload)
        except Exception as e:
            db.session.rollback()
            # Backward compatibility: some uploaded datasets don't include Area_of_Study.
            msg = str(e)
            if 'Area_of_Study' in msg and ('no such column' in msg.lower() or 'undefined column' in msg.lower()):
                try:
                    sql = (
                        f'''SELECT "Course_Number", "Course_Title", "Credits", "Instructor", "Day_and_Period", "Prerequisite"
                            FROM "{table_name}"'''
                    )
                    params = {}
                    if semester_label:
                        sql += ' WHERE "Semester" = :semester_label'
                        params['semester_label'] = semester_label
                    results = db.session.execute(text(sql), params).fetchall()
                    payload = []
                    for r in results:
                        d = dict(r._mapping)
                        d['Area_of_Study'] = None
                        payload.append(_add_display_fields(d))

                    log_behavior_event_safe(
                        'catalog_loaded',
                        schedule_id=schedule_id_int,
                        metadata={
                            'result_count': len(payload),
                            'year': resolved_year,
                            'semester': resolved_semester,
                            'source': 'schedule_editor',
                        },
                    )
                    return jsonify(payload)
                except Exception as e2:
                    db.session.rollback()
                    print(f"Catalog load error (fallback): {e2}")
                    return jsonify([])

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
        sql = (
            f'''SELECT "Course_Number", "Course_Title", "Credits", "Instructor", "Day_and_Period", "Area_of_Study", "Prerequisite"
                FROM "{table_name}"
                WHERE (LOWER("Course_Number") LIKE LOWER(:q)
                   OR LOWER("Course_Title") LIKE LOWER(:q))'''
        )
        params = {'q': f'%{query_str}%'}
        if semester_label:
            sql += ' AND "Semester" = :semester_label'
            params['semester_label'] = semester_label
        sql += ' LIMIT 50'
        results = db.session.execute(text(sql), params).fetchall()
        payload = [_add_display_fields(dict(r._mapping)) for r in results]
        return jsonify(payload)
    except Exception as e:
        db.session.rollback()
        msg = str(e)
        if 'Area_of_Study' in msg and ('no such column' in msg.lower() or 'undefined column' in msg.lower()):
            try:
                sql = (
                    f'''SELECT "Course_Number", "Course_Title", "Credits", "Instructor", "Day_and_Period", "Prerequisite"
                        FROM "{table_name}"
                        WHERE (LOWER("Course_Number") LIKE LOWER(:q)
                           OR LOWER("Course_Title") LIKE LOWER(:q))'''
                )
                params = {'q': f'%{query_str}%'}
                if semester_label:
                    sql += ' AND "Semester" = :semester_label'
                    params['semester_label'] = semester_label
                sql += ' LIMIT 50'
                results = db.session.execute(text(sql), params).fetchall()
                payload = []
                for r in results:
                    d = dict(r._mapping)
                    d['Area_of_Study'] = None
                    payload.append(_add_display_fields(d))
                return jsonify(payload)
            except Exception as e2:
                db.session.rollback()
                print(f"Search error (fallback): {e2}")
                return jsonify([])

        print(f"Search error: {e}")
        return jsonify([])

# --- Premium Dashboard Logic ---
@student_bp.route('/dashboard')
@login_required
def user_dashboard():
    schedules = Schedule.query.filter_by(user_id=current_user.id).order_by(Schedule.year.desc()).all()
    past_schedules = [s for s in schedules if s.schedule_type == 'past']
    dataset_years = get_available_dataset_years()
    
    tables = get_course_data_tables()
    # Validate table identifiers before using them in SQL
    validated_tables = [t for t in tables if is_valid_table_name(t)]
    
    total_credits = 0.0
    area_credits = {}
    top_faculty = {}
    top_periods = {}
    overall_courses_for_gpa = []
    future_courses_for_predictive = []

    if validated_tables:
        table_union = " UNION ".join([f'SELECT "Course_Number", "Course_Title", "Credits", "Area_of_Study", "Instructor", "Day_and_Period" FROM "{t}"' for t in validated_tables])

        try:
            # 1. Past Courses (For overall GPA, total credits, areas, faculty, periods)
            past_query = text(f'''
                SELECT sc.grade, c."Credits", c."Area_of_Study", c."Instructor", c."Day_and_Period"
                FROM schedule_courses sc
                JOIN schedules s ON sc.schedule_id = s.id
                JOIN ({table_union}) c ON sc.course_number = c."Course_Number"
                WHERE s.user_id = :uid AND s.schedule_type = 'past'
            ''')
            past_results = db.session.execute(past_query, {'uid': current_user.id}).fetchall()

            for row in past_results:
                try:
                    cred = float(row.Credits) if row.Credits else 0.0
                except ValueError:
                    cred = 0.0

                if row.grade and row.grade.upper() != 'F':
                    total_credits += cred
                    area = row.Area_of_Study if row.Area_of_Study else 'Unspecified'
                    area_credits[area] = area_credits.get(area, 0) + cred

                if row.grade:
                    overall_courses_for_gpa.append({'grade': row.grade, 'Credits': row.Credits})

                instructor = row.Instructor
                if instructor and instructor.strip():
                    top_faculty[instructor] = top_faculty.get(instructor, 0) + 1

                dp = row.Day_and_Period
                if dp:
                    days = re.findall(r'[A-Za-z]+', dp)
                    for d in days:
                        if len(d) >= 3: # E.g., Mon, Tue
                            top_periods[d] = top_periods.get(d, 0) + 1

            # 2. Future Courses (For predictive GPA, assuming user inputs expected grades)
            future_query = text(f'''
                SELECT sc.grade, c."Credits"
                FROM schedule_courses sc
                JOIN schedules s ON sc.schedule_id = s.id
                JOIN ({table_union}) c ON sc.course_number = c."Course_Number"
                WHERE s.user_id = :uid AND s.schedule_type = 'future' AND sc.grade IS NOT NULL AND sc.grade != ''
            ''')
            future_results = db.session.execute(future_query, {'uid': current_user.id}).fetchall()
            for row in future_results:
                future_courses_for_predictive.append({'grade': row.grade, 'Credits': row.Credits})
                
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

    return render_template('Student/dashboard.html', 
                           schedules=schedules, 
                           has_past=len(past_schedules) > 0,
                           total_credits=earned_credits,
                           in_progress_credits=in_progress_credits,
                           projected_credits=projected_credits,
                           graduation_required_credits=graduation_required_credits,
                           remaining_to_graduate=remaining_to_graduate,
                           area_credits=area_credits,
                           overall_gpa=overall_gpa,
                           predictive_gpa=predictive_gpa,
                           favorite_faculty=favorite_faculty,
                           favorite_day=favorite_day,
                           ai_enabled=ai_enabled,
                           ai_data_collection_enabled=ai_data_collection_enabled,
                           has_dataset_years=bool(dataset_years),
                           dataset_years=dataset_years,
                           notifications=notifications,
                           unread_notifications_count=unread_notifications_count)


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

    if request.method == 'POST':
        full_name = (request.form.get('full_name') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        gender = (request.form.get('gender') or '').strip() or None
        career_interests = (request.form.get('career_interests') or '').strip() or None
        major_subject_area_id = request.form.get('major_subject_area_id', type=int)
        minor_subject_area_id = request.form.get('minor_subject_area_id', type=int)

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
            return render_template('Student/profile.html', majors=majors, minors=minors)

        if not major_subject_area_id or not minor_subject_area_id:
            flash('Major and minor are required.', 'error')
            return render_template('Student/profile.html', majors=majors, minors=minors)

        current_user.full_name = full_name
        current_user.email = email
        current_user.gender = gender
        current_user.career_interests = career_interests
        current_user.major_subject_area_id = major_subject_area_id
        current_user.minor_subject_area_id = minor_subject_area_id

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
                return render_template('Student/profile.html', majors=majors, minors=minors)
            if new_password != confirm_new_password:
                flash('New passwords do not match.', 'error')
                return render_template('Student/profile.html', majors=majors, minors=minors)
            if len(new_password) < 8:
                flash('New password must be at least 8 characters.', 'error')
                return render_template('Student/profile.html', majors=majors, minors=minors)
            current_user.password = generate_password_hash(new_password, method='pbkdf2:sha256')

        db.session.commit()
        flash('Profile updated successfully.', 'success')
        return redirect(url_for('student.profile'))

    return render_template('Student/profile.html', majors=majors, minors=minors)


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

    if request.method == 'POST':
        name = (request.form.get('schedule_name') or '').strip()[:150]
        year = request.form.get('year')
        semester = (request.form.get('semester') or '').strip()[:50]
        
        if not current_user.is_authenticated:
            sched_type = 'future'
        else:
            sched_type = request.form.get('schedule_type', 'future')

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

        if year_int not in dataset_years:
            flash("Selected year is not available in uploaded course datasets.", "error")
            return redirect(
                url_for('student.user_dashboard')
                if current_user.is_authenticated
                else url_for('student.create_schedule', schedule_type='future')
            )

        new_sched = Schedule(
            schedule_name=name,
            year=year_int,
            semester=semester,
            schedule_type=sched_type
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
            },
        )

        return redirect(url_for('student.edit_schedule', schedule_id=new_sched.id))
        
    if current_user.is_authenticated:
        requested_type = (request.args.get('schedule_type') or '').strip().lower()
        if not requested_type:
            return redirect(url_for('auth.choose_schedule_type'))

    current_year_setting = Setting.query.get('current_year')
    current_year = int(current_year_setting.value) if current_year_setting and current_year_setting.value.isdigit() else 2024

    schedule_type = (request.args.get('schedule_type') or 'future').strip().lower()
    if schedule_type not in {'past', 'future'}:
        schedule_type = 'future'
    if not current_user.is_authenticated:
        schedule_type = 'future'
    
    available_years = dataset_years

    suggestions_allowed = False
    suggestions_gate_message = None
    if current_user.is_authenticated and schedule_type == 'future':
        suggestions_allowed, suggestions_gate_message = _student_can_receive_ai_suggestions(current_user.id)

    return render_template(
        'Student/create_schedule.html',
        available_years=available_years,
        current_year=current_year,
        schedule_type=schedule_type,
        has_dataset_years=bool(available_years),
        ai_enabled=get_bool_setting('ai_enabled', False),
        ai_recommendations_enabled=get_bool_setting('ai_recommendations_enabled', True),
        ai_data_collection_enabled=get_bool_setting('ai_data_collection_enabled', False),
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
        
    table_name, needs_sem_filter = get_best_course_table(schedule.year, schedule.semester)
    semester_label = None
    if needs_sem_filter and table_name and table_has_column(table_name, 'Semester'):
        semester_label = semester_label_for_dataset(schedule.year, schedule.semester)
    
    current_courses = []
    if table_name and is_valid_table_name(table_name):
        try:
            sql = (
                f'''SELECT sc.id as entry_id, sc.grade, c.*
                    FROM schedule_courses sc
                    JOIN "{table_name}" c ON sc.course_number = c."Course_Number"
                    WHERE sc.schedule_id = :sched_id'''
            )
            params = {'sched_id': schedule.id}
            if semester_label:
                sql += ' AND c."Semester" = :semester_label'
                params['semester_label'] = semester_label
            result = db.session.execute(text(sql), params).fetchall()
            current_courses = [_add_display_fields(dict(r._mapping)) for r in result]
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
                           periods=periods,
                           lunch_start_time=lunch_start_time,
                           lunch_end_time=lunch_end_time)


@student_bp.route('/api/recommendations_preview', methods=['GET'])
@login_required
def api_recommendations_preview():
    year_raw = request.args.get('year')
    semester = (request.args.get('semester') or '').strip()

    try:
        year = int(year_raw)
    except Exception:
        return jsonify({'success': False, 'message': 'Invalid year'}), 400
    if semester not in {'Spring', 'Fall'}:
        return jsonify({'success': False, 'message': 'Invalid semester'}), 400

    schedule_type = (request.args.get('schedule_type') or 'future').strip().lower()
    if schedule_type != 'future':
        return jsonify({'success': True, 'recommendations': [], 'message': 'Suggestions are only available for future schedules.'})

    try:
        limit = int(request.args.get('limit', 5))
    except Exception:
        limit = 5
    limit = max(1, min(limit, 5))

    log_behavior_event_safe(
        'ai_preview_requested',
        metadata={'year': year, 'semester': semester, 'limit': limit},
    )

    if not get_bool_setting('ai_enabled', False) or not get_bool_setting('ai_recommendations_enabled', True):
        log_ai_event_safe(
            'ai_preview_blocked',
            metadata={'year': year, 'semester': semester, 'reason': 'feature_disabled'},
        )
        return jsonify({'success': False, 'message': 'AI recommendations are currently disabled.'}), 403

    if not bool(getattr(current_user, 'ai_opt_in', False)):
        log_ai_event_safe(
            'ai_preview_blocked',
            metadata={'year': year, 'semester': semester, 'reason': 'opt_in_required'},
        )
        return jsonify({'success': False, 'message': 'AI recommendations require opt-in.'}), 403

    catalog, table_name = _load_catalog_for_term(year, semester)
    if not table_name:
        log_ai_event_safe(
            'ai_preview_blocked',
            metadata={'year': year, 'semester': semester, 'reason': 'catalog_unavailable'},
        )
        return jsonify({'success': False, 'message': 'Catalog unavailable for selected term.'}), 400

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
            'year': year,
            'semester': semester,
            'table_name': table_name,
            'recommendation_count': len(payload),
            'parse_error_count': len(errors),
        },
    )

    return jsonify({
        'success': True,
        'recommendations': payload,
        'context': {
            'major': area_names.get('major') or None,
            'minor': area_names.get('minor') or None,
            'missing_credits_by_area': progress.get('missing_by_area', {}),
            'remaining_to_graduate': progress.get('remaining_to_graduate', 0.0),
            'graduation_required_credits': progress.get('graduation_required_credits', 124.0),
        },
    })


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

    table_name, needs_sem_filter = get_best_course_table(schedule.year, schedule.semester)
    if not table_name or not is_valid_table_name(table_name):
        return jsonify({'success': True, 'courses': _load_schedule_courses_fallback(schedule.id)})

    semester_label = None
    if needs_sem_filter and table_has_column(table_name, 'Semester'):
        semester_label = semester_label_for_dataset(schedule.year, schedule.semester)

    try:
        sql = (
            f'''SELECT sc.id as entry_id, sc.grade, c.*
                FROM schedule_courses sc
                JOIN "{table_name}" c ON sc.course_number = c."Course_Number"
                WHERE sc.schedule_id = :sched_id'''
        )
        params = {'sched_id': schedule.id}
        if semester_label:
            sql += ' AND c."Semester" = :semester_label'
            params['semester_label'] = semester_label
        result = db.session.execute(text(sql), params).fetchall()
        courses = [_add_display_fields(dict(r._mapping)) for r in result]
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

    # Prevent duplicates
    existing = ScheduleCourse.query.filter_by(schedule_id=sched.id, course_number=course_num).first()
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
    # Fetch the prerequisite string for the selected course from the schedule's course catalog.
    table_name, needs_sem_filter = get_best_course_table(sched.year, sched.semester)
    if not table_name or not is_valid_table_name(table_name):
        print(f"Prereq check blocked: invalid/missing course catalog table for schedule {sched.id}")
        return (
            jsonify(
                {
                    'success': False,
                    'error': 'Unable to validate prerequisites',
                    'message': 'Course catalog unavailable; cannot validate prerequisites.'
                }
            ),
            400,
        )

    prereq_str = ''
    candidate_day_period = ''
    try:
        semester_label = None
        if needs_sem_filter and table_has_column(table_name, 'Semester'):
            semester_label = semester_label_for_dataset(sched.year, sched.semester)

        sql = f'SELECT "Prerequisite", "Day_and_Period" FROM "{table_name}" WHERE "Course_Number" = :cn'
        params = {'cn': course_num}
        if semester_label:
            sql += ' AND "Semester" = :semester_label'
            params['semester_label'] = semester_label
        sql += ' LIMIT 1'

        prereq_row = db.session.execute(text(sql), params).fetchone()
        if not prereq_row:
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
        prereq_str = prereq_row[0]
        candidate_day_period = prereq_row[1]
    except Exception as e:
        db.session.rollback()
        print(f"Error fetching prerequisites for {course_num} from {table_name}: {e}")
        return (
            jsonify(
                {
                    'success': False,
                    'error': 'Unable to validate prerequisites',
                    'message': 'Could not validate prerequisites for this course.'
                }
            ),
            400,
        )

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
        semester_label = None
        if needs_sem_filter and table_has_column(table_name, 'Semester'):
            semester_label = semester_label_for_dataset(sched.year, sched.semester)

        sql = (
            f'''SELECT c."Day_and_Period"
                FROM schedule_courses sc
                JOIN "{table_name}" c ON sc.course_number = c."Course_Number"
                WHERE sc.schedule_id = :sid'''
        )
        params = {'sid': sched.id}
        if semester_label:
            sql += ' AND c."Semester" = :semester_label'
            params['semester_label'] = semester_label
        existing_rows = db.session.execute(text(sql), params).fetchall()
        occupied_blocks = set()
        for r in existing_rows:
            occupied_blocks |= parse_day_period_blocks(r[0])

        candidate_blocks = parse_day_period_blocks(candidate_day_period)
        if has_conflict(occupied_blocks, candidate_blocks):
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
        print(f"Error checking conflicts for {course_num} on schedule {sched.id}: {e}")
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

    new_course = ScheduleCourse(schedule_id=sched_id, course_number=course_num)
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
        return jsonify({'success': False, 'error': 'Catalog unavailable', 'message': 'Catalog unavailable'}), 400

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

    table_name, needs_sem_filter = get_best_course_table(sched.year, sched.semester)
    if not table_name or not is_valid_table_name(table_name):
        return jsonify({'success': False, 'error': 'Catalog unavailable', 'message': 'Catalog unavailable'}), 400

    semester_label = None
    if needs_sem_filter and table_has_column(table_name, 'Semester'):
        semester_label = semester_label_for_dataset(sched.year, sched.semester)

    completed_passing = []
    if current_user.is_authenticated:
        try:
            completed_passing = list(_completed_passing_prereq_identifiers(current_user.id))
        except Exception as e:
            db.session.rollback()
            print(f"Error fetching completed courses for generator (user {current_user.id}): {e}")
            return jsonify({'success': False, 'error': 'Unable to validate prerequisites', 'message': 'Unable to validate prerequisites'}), 400

    try:
        sql = (
            f'''SELECT c."Day_and_Period"
                FROM schedule_courses sc
                JOIN "{table_name}" c ON sc.course_number = c."Course_Number"
                WHERE sc.schedule_id = :sid'''
        )
        params = {'sid': sched.id}
        if semester_label:
            sql += ' AND c."Semester" = :semester_label'
            params['semester_label'] = semester_label
        existing_dp_rows = db.session.execute(text(sql), params).fetchall()
        existing_day_period = [r[0] for r in existing_dp_rows]
    except Exception as e:
        db.session.rollback()
        print(f"Error loading existing day/period for generator: {e}")
        existing_day_period = []

    try:
        sql = (
            f'''SELECT "Course_Number", "Course_Title", "Credits", "Instructor", "Day_and_Period", "Area_of_Study", "Prerequisite"
                FROM "{table_name}"'''
        )
        params = {}
        if semester_label:
            sql += ' WHERE "Semester" = :semester_label'
            params['semester_label'] = semester_label
        rows = db.session.execute(text(sql), params).fetchall()
        catalog = [dict(r._mapping) for r in rows]
    except Exception as e:
        db.session.rollback()
        print(f"Error loading catalog for generator from {table_name}: {e}")
        return jsonify({'success': False, 'error': 'Failed to load catalog', 'message': 'Failed to load catalog'}), 400

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
        metadata={'target_count': target_count, 'generated_count': len(generated)},
    )

    return jsonify({'success': True, 'courses': generated})
