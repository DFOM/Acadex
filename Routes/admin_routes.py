# Routes/admin_routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session, current_app
from flask_login import login_required, current_user
import os
import csv
from io import TextIOWrapper
import re
import threading
import subprocess
from datetime import datetime, timezone
from functools import wraps
from sqlalchemy import inspect, text
import sqlalchemy as sa
from models import db, User, Schedule, SubjectArea, Period, Setting, Announcement, LandingPageSection, CatalogStatus, Notification, Role, Permission, RegistrationField, ScrapedDataset, ScrapedCourse, Course, Faculty, DatasetRegistry
from utils import (
    calculate_gpa,
    ensure_default_landing_sections,
    safe_json_loads,
    safe_json_dumps,
)
from flask import Response
import json


def _parse_hhmm_to_minutes(value: str):
    try:
        parts = str(value or '').strip().split(':')
        if len(parts) != 2:
            return None
        h = int(parts[0])
        m = int(parts[1])
        if h < 0 or h > 23 or m < 0 or m > 59:
            return None
        return h * 60 + m
    except Exception:
        return None


def _minutes_to_hhmm(total_minutes: int) -> str:
    total_minutes = int(total_minutes) % (24 * 60)
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"

admin_bp = Blueprint('admin', __name__)


ENDPOINT_PERMISSION_MAP = {
    'admin.manage_datasets': 'manage_datasets',
    'admin.upload_dataset': 'manage_datasets',
    'admin.set_active_dataset': 'manage_datasets',
    'admin.delete_dataset': 'manage_datasets',
    'admin.api_datasets': 'manage_datasets',
    'admin.api_activate_dataset': 'manage_datasets',
    'admin.api_delete_dataset': 'manage_datasets',
    'admin.api_refresh_dataset': 'manage_datasets',
    'admin.api_orphan_tables': 'manage_datasets',
    'admin.api_clean_orphan_tables': 'manage_datasets',
    'admin.manage_users': 'manage_users',
    'admin.update_user_demographics': 'manage_users',
    'admin.delete_user': 'manage_users',
    'admin.manage_roles': 'manage_users',
    'admin.assign_user_role': 'manage_users',
    'admin.manage_registration_fields': 'manage_users',
    'admin.analytics': 'view_analytics',
    'admin.analytics_data_api': 'view_analytics',
    'admin.analytics_overrides_api': 'view_analytics',
    'admin.analytics_export': 'view_analytics',
    'admin.manage_courses': 'edit_courses',
    'admin.manage_faculty': 'edit_courses',
    'admin.api_courses': 'edit_courses',
    'admin.api_course_item': 'edit_courses',
    'admin.api_course_duplicate': 'edit_courses',
    'admin.api_faculty': 'edit_courses',
    'admin.api_faculty_item': 'edit_courses',
    'admin.api_update_course': 'edit_courses',
    'admin.manage_subjects': 'edit_courses',
    'admin.api_update_subject_area': 'edit_courses',
    'admin.manage_instructors': 'edit_courses',
    'admin.api_update_instructor': 'edit_courses',
    'admin.api_delete_instructor': 'edit_courses',
    'admin.manage_periods': 'edit_courses',
    'admin.update_period': 'edit_courses',
    'admin.api_create_period': 'edit_courses',
    'admin.api_update_period_full': 'edit_courses',
    'admin.api_delete_period': 'edit_courses',
    'admin.api_update_lunch_settings': 'edit_courses',
    'admin.ai_dashboard': 'control_ai',
    'admin.ai_settings_update': 'control_ai',
    'admin.ai_export_events_csv': 'control_ai',
    'admin.ai_export_behavior_events_csv': 'control_ai',
    'admin.manage_landing': 'edit_landing_page',
    'admin.create_announcement': 'edit_landing_page',
    'admin.edit_announcement': 'edit_landing_page',
    'admin.toggle_announcement': 'edit_landing_page',
    'admin.delete_announcement': 'edit_landing_page',
    'admin.update_app_settings_route': 'manage_users',
    'admin.university_connection': 'manage_datasets',
    'admin.start_browser_session': 'manage_datasets',
    'admin.check_login_status': 'manage_datasets',
    'admin.probe_fields': 'manage_datasets',
    'admin.probe_status': 'manage_datasets',
    'admin.start_scraping': 'manage_datasets',
    'admin.pause_scraping': 'manage_datasets',
    'admin.resume_scraping': 'manage_datasets',
    'admin.stop_scraping': 'manage_datasets',
    'admin.scraper_config_api': 'manage_datasets',
    'admin.scraper_status': 'manage_datasets',
    'admin.scraper_diagnostics': 'manage_datasets',
    'admin.scraper_diagnostics_api': 'manage_datasets',
    'admin.admin_health': 'manage_users',
    'admin.health_metrics_api': 'manage_users',
    'admin.health_fix_api': 'manage_users',
    'admin.restore_version_placeholder': 'manage_users',
}


def _wants_json_response() -> bool:
    return (
        request.path.startswith('/admin/api/')
        or request.is_json
        or request.accept_mimetypes.best == 'application/json'
    )


def _deny_admin_access(message: str, status_code: int = 403):
    if _wants_json_response():
        return jsonify({'success': False, 'message': message}), status_code
    flash(message, 'error')
    if current_user.is_authenticated:
        return redirect(url_for('student.user_dashboard'))
    return redirect(url_for('auth.login', next=request.url))


@admin_bp.before_request
def _enforce_admin_access_control():
    if request.endpoint in {'admin.login', 'admin.logout'}:
        return None

    # Security: every admin request is validated server-side per request.
    # We never trust client-side UI visibility; permissions are checked here before route handlers run.
    if not current_user.is_authenticated:
        return _deny_admin_access('Authentication required.', status_code=401)

    if not current_user.can_access_admin_panel():
        return _deny_admin_access('Admin access required.')

    required_permission = ENDPOINT_PERMISSION_MAP.get(request.endpoint)
    # Security: role permissions are stored in DB and resolved from the authenticated user role.
    # This prevents privilege escalation by blocking operations when role permission is missing.
    if required_permission and not current_user.has_permission(required_permission):
        return _deny_admin_access('You do not have permission for this action.')
    return None

# --- SECURITY WRAPPER ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.can_access_admin_panel():
            return _deny_admin_access('Admin privileges required.')
        return f(*args, **kwargs)
    return decorated_function

# --- HELPER FUNCTIONS ---
def get_app_setting(key, default=""):
    setting = Setting.query.get(key)
    return setting.value if setting else default

def update_app_setting(key, value):
    setting = Setting.query.get(key)
    if not setting:
        setting = Setting(key=key, value=value)
        db.session.add(setting)
    else:
        setting.value = value
    db.session.commit()


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _active_session_count() -> int:
    table_name = str(current_app.config.get('SESSION_SQLALCHEMY_TABLE') or 'user_sessions')
    if not re.match(r'^[A-Za-z0-9_]+$', table_name):
        return 0
    try:
        insp = inspect(db.engine)
        if not insp.has_table(table_name):
            return 0
        value = db.session.execute(text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar()
        return int(value or 0)
    except Exception:
        db.session.rollback()
        return 0


def _recent_git_versions(limit: int = 12) -> list[dict]:
    app_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    max_rows = max(1, min(int(limit), 40))
    try:
        out = subprocess.check_output(
            [
                'git', '--no-pager', 'log', f'--max-count={max_rows}',
                '--pretty=format:%H\t%h\t%ad\t%s', '--date=iso-strict',
            ],
            cwd=app_root,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2.5,
        )
    except Exception:
        return []

    rows: list[dict] = []
    for line in (out or '').splitlines():
        parts = line.split('\t', 3)
        if len(parts) != 4:
            continue
        rows.append(
            {
                'sha': parts[0],
                'short_sha': parts[1],
                'date': parts[2],
                'message': parts[3],
            }
        )
    return rows


def _process_metrics() -> dict:
    try:
        import psutil  # type: ignore

        proc = psutil.Process(os.getpid())
        with proc.oneshot():
            mem = proc.memory_info()
            rss = int(getattr(mem, 'rss', 0) or 0)
            vms = int(getattr(mem, 'vms', 0) or 0)
            cpu = float(proc.cpu_percent(interval=0.0) or 0.0)
            started = datetime.fromtimestamp(proc.create_time(), tz=timezone.utc)
        return {
            'provider': 'psutil',
            'cpu_percent': round(cpu, 2),
            'memory_rss_mb': round(rss / (1024 * 1024), 2),
            'memory_vms_mb': round(vms / (1024 * 1024), 2),
            'process_started_at': started.isoformat(),
        }
    except Exception:
        return {
            'provider': 'unavailable',
            'cpu_percent': None,
            'memory_rss_mb': None,
            'memory_vms_mb': None,
            'process_started_at': None,
        }


def _course_merge_key(course_number: str, day_period: str, semester: str, year: str) -> tuple[str, str, str, str]:
    return (
        str(course_number or '').strip().upper(),
        str(day_period or '').strip().lower(),
        str(semester or '').strip().lower(),
        str(year or '').strip().lower(),
    )


def _source_priority(source: str | None) -> int:
    src = str(source or '').strip().lower()
    if src == 'manual':
        return 3
    if src == 'csv':
        return 2
    return 1


def _course_sort_ts(value):
    return value or datetime.min


def _faculty_name_key(name: str) -> str:
    return re.sub(r'\s+', ' ', str(name or '').strip()).lower()


def _normalize_faculty_name(name: str) -> str:
    cleaned = re.sub(r'\s+', ' ', str(name or '').strip())
    cleaned = re.sub(r'^(dr|prof|professor|mr|ms|mrs)\.?\s+', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+,', ',', cleaned)
    cleaned = cleaned.strip(' -;|/')
    return cleaned


def _extract_faculty_names(raw_instructor: str | None) -> list[str]:
    text = str(raw_instructor or '').strip()
    if not text:
        return []

    placeholder = {'tba', 'tbd', 'staff', 'n/a', 'na', 'none', 'unknown'}
    if _faculty_name_key(text) in placeholder:
        return []

    parts = re.split(r'\s*(?:;|\||/|&|\band\b)\s*', text, flags=re.IGNORECASE)
    normalized: list[str] = []
    seen: set[str] = set()
    for part in parts:
        name = _normalize_faculty_name(part)
        if not name:
            continue
        key = _faculty_name_key(name)
        if not key or key in placeholder or key in seen:
            continue
        seen.add(key)
        normalized.append(name)
    return normalized


def _sync_course_faculty_from_instructor(course: Course, instructor_value: str | None) -> None:
    names = _extract_faculty_names(instructor_value)
    if names:
        existing_rows = Faculty.query.filter(sa.func.lower(Faculty.name).in_([n.lower() for n in names])).all()
        existing_map = {_faculty_name_key(r.name): r for r in existing_rows}
        synced_rows: list[Faculty] = []
        for name in names:
            key = _faculty_name_key(name)
            row = existing_map.get(key)
            if not row:
                row = Faculty(name=name)
                db.session.add(row)
                existing_map[key] = row
            synced_rows.append(row)
        course.faculty = synced_rows
        course.instructor = '; '.join(names)
        return

    course.faculty = []
    clean_instructor = str(instructor_value or '').strip()
    course.instructor = clean_instructor or None


def _parse_course_ref(course_ref: str) -> tuple[str, int] | None:
    raw = str(course_ref or '').strip()
    if not raw:
        return None
    if ':' in raw:
        prefix, ident = raw.split(':', 1)
    else:
        prefix, ident = 'manual', raw
    prefix = prefix.strip().lower()
    try:
        row_id = int(ident)
    except Exception:
        return None
    if prefix not in {'manual', 'csv', 'scraped'}:
        return None
    return prefix, row_id


def _serialize_local_course(row: Course) -> dict:
    src = str(row.source or 'manual').strip().lower()
    if src not in {'manual', 'csv', 'scraped'}:
        src = 'manual'
    return {
        'id': row.id,
        'ref': f'{src}:{row.id}',
        'source': src,
        'course_number': row.course_number,
        'title': row.title,
        'credits': row.credits,
        'day_period': row.day_period,
        'semester': row.semester,
        'year': row.year,
        'prerequisite': row.prerequisite,
        'subject_area': row.subject_area,
        'instructor': row.instructor,
        'dataset_name': row.dataset_name,
        'last_updated': (row.last_updated or row.updated_at or row.created_at).isoformat() if (row.last_updated or row.updated_at or row.created_at) else None,
        'faculty_ids': [f.id for f in row.faculty],
        'faculty_names': [f.name for f in row.faculty],
        'editable': True,
        'deletable': src == 'manual',
    }


def _serialize_scraped_course(row: ScrapedCourse) -> dict:
    dataset_obj = getattr(row, 'dataset', None)
    dataset_name = getattr(dataset_obj, 'name', None)
    semester = getattr(dataset_obj, 'semester', None)
    year = getattr(dataset_obj, 'year', None)
    return {
        'id': row.id,
        'ref': f'scraped:{row.id}',
        'source': 'scraped',
        'course_number': row.course_number,
        'title': row.course_title,
        'credits': row.credits,
        'day_period': row.day_and_period,
        'semester': semester,
        'year': year,
        'prerequisite': row.prerequisite,
        'subject_area': row.area_of_study,
        'instructor': row.instructor,
        'dataset_name': dataset_name,
        'last_updated': getattr(dataset_obj, 'updated_at', None).isoformat() if getattr(dataset_obj, 'updated_at', None) else None,
        'faculty_ids': [],
        'faculty_names': _extract_faculty_names(row.instructor),
        'editable': True,
        'deletable': False,
    }


def _merged_courses_for_admin() -> list[dict]:
    active_dataset_name = str(get_app_setting('active_dataset_name', '') or '').strip().lower()

    local_rows = Course.query.order_by(Course.last_updated.desc(), Course.id.desc()).all()
    candidates: list[dict] = [_serialize_local_course(r) for r in local_rows]

    scraped_rows = (
        ScrapedCourse.query
        .join(ScrapedDataset, ScrapedCourse.dataset_id == ScrapedDataset.id)
        .filter(ScrapedDataset.status == 'completed')
        .order_by(ScrapedDataset.created_at.desc(), ScrapedCourse.id.desc())
        .all()
    )
    candidates.extend(_serialize_scraped_course(r) for r in scraped_rows)

    winner_by_key: dict[tuple[str, str, str, str], dict] = {}
    for row in candidates:
        key = _course_merge_key(
            row.get('course_number'),
            row.get('day_period'),
            row.get('semester'),
            row.get('year'),
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

        left_active = 1 if active_dataset_name and str(row.get('dataset_name') or '').strip().lower() == active_dataset_name else 0
        right_active = 1 if active_dataset_name and str(existing.get('dataset_name') or '').strip().lower() == active_dataset_name else 0
        if left_active > right_active:
            winner_by_key[key] = row
            continue
        if left_active < right_active:
            continue

        left_ts = _course_sort_ts(
            datetime.fromisoformat(row['last_updated']) if row.get('last_updated') else None
        )
        right_ts = _course_sort_ts(
            datetime.fromisoformat(existing['last_updated']) if existing.get('last_updated') else None
        )
        if left_ts >= right_ts:
            winner_by_key[key] = row

    rows = list(winner_by_key.values())
    rows.sort(
        key=lambda item: (
            str(item.get('year') or ''),
            str(item.get('semester') or ''),
            str(item.get('course_number') or ''),
            str(item.get('day_period') or ''),
        ),
        reverse=True,
    )
    return rows


def _serialize_faculty(row: Faculty) -> dict:
    def _ref_for_course(c: Course) -> str:
        src = str(getattr(c, 'source', 'manual') or 'manual').strip().lower()
        if src not in {'manual', 'csv'}:
            src = 'manual'
        return f'{src}:{c.id}'

    return {
        'id': row.id,
        'name': row.name,
        'email': row.email,
        'assigned_courses_count': len(row.courses or []),
        'assigned_course_refs': [_ref_for_course(c) for c in (row.courses or [])],
        'assigned_course_labels': [f"{c.course_number} — {c.title}" for c in (row.courses or [])],
        'course_refs': [_ref_for_course(c) for c in (row.courses or [])],
        'course_labels': [f"{c.course_number} — {c.title}" for c in (row.courses or [])],
        'advisor_area_ids': [a.id for a in (row.advisor_areas or [])],
        'advisor_area_names': [a.name for a in (row.advisor_areas or [])],
        'advisor_areas': [a.name for a in (row.advisor_areas or [])],
    }


def _all_selectable_dataset_names() -> list[str]:
    if _dataset_registry_supported():
        _ensure_dataset_registry_synced()
        names = [r.name for r in DatasetRegistry.query.order_by(DatasetRegistry.created_at.desc(), DatasetRegistry.id.desc()).all() if getattr(r, 'name', None)]
        if names:
            return sorted(set(names), reverse=True)

    scraped_dataset_names = [
        d.name
        for d in ScrapedDataset.query.filter(ScrapedDataset.status == 'completed').order_by(ScrapedDataset.created_at.desc()).all()
        if d.name
    ]
    csv_dataset_names = [
        r[0]
        for r in db.session.query(Course.dataset_name).filter(Course.dataset_name.isnot(None)).distinct().all()
        if r and r[0]
    ]
    return sorted(set(scraped_dataset_names + csv_dataset_names), reverse=True)


def _dataset_registry_supported() -> bool:
    try:
        DatasetRegistry.query.limit(1).all()
        return True
    except Exception:
        db.session.rollback()
        return False


def _dataset_table_name_from_name(name: str, source: str) -> str:
    safe_name = re.sub(r'[^a-z0-9]+', '_', str(name or '').strip().lower()).strip('_')
    if not safe_name:
        safe_name = 'dataset'
    if source == 'scraped' and safe_name.startswith('scraped_'):
        return safe_name
    return f'{source}_{safe_name}'


def _infer_dataset_type(semester: str | None) -> str:
    sem = str(semester or '').strip().lower()
    if sem in {'full', 'fullyear', 'full_year', 'year', 'annual', 'all'}:
        return 'full_year'
    return 'term'


def _set_active_registry_dataset(dataset_name: str | None) -> None:
    if not _dataset_registry_supported():
        update_app_setting('active_dataset_name', str(dataset_name or '').strip())
        return

    name = str(dataset_name or '').strip()
    DatasetRegistry.query.update({'is_active': False})
    if name:
        row = DatasetRegistry.query.filter(sa.func.lower(DatasetRegistry.name) == name.lower()).order_by(DatasetRegistry.created_at.desc(), DatasetRegistry.id.desc()).first()
        if row:
            row.is_active = True
            name = row.name
        else:
            name = ''
    update_app_setting('active_dataset_name', name)
    db.session.commit()


def _active_registry_row() -> DatasetRegistry | None:
    if not _dataset_registry_supported():
        return None
    row = DatasetRegistry.query.filter(DatasetRegistry.is_active.is_(True)).order_by(DatasetRegistry.created_at.desc(), DatasetRegistry.id.desc()).first()
    if row:
        return row
    return DatasetRegistry.query.order_by(DatasetRegistry.created_at.desc(), DatasetRegistry.id.desc()).first()


def _resolve_effective_active_dataset_name() -> str:
    setting_name = str(get_app_setting('active_dataset_name', '') or '').strip()
    if setting_name:
        return setting_name
    active_row = _active_registry_row()
    return str(active_row.name).strip() if active_row and active_row.name else ''


def _upsert_dataset_registry_entry(
    name: str,
    source: str,
    table_name: str | None = None,
    *,
    year: str | None = None,
    semester: str | None = None,
    dataset_type: str | None = None,
    make_active: bool = False,
) -> DatasetRegistry | None:
    if not _dataset_registry_supported():
        return None

    ds_name = str(name or '').strip()
    ds_source = str(source or '').strip().lower()
    if ds_source not in {'csv', 'scraped'} or not ds_name:
        return None

    table_value = str(table_name or '').strip() or _dataset_table_name_from_name(ds_name, ds_source)
    ds_year = str(year or '').strip() or None
    ds_semester = str(semester or '').strip() or None
    ds_type = str(dataset_type or '').strip().lower() or _infer_dataset_type(ds_semester)
    if ds_type not in {'term', 'full_year'}:
        ds_type = 'term'

    row = DatasetRegistry.query.filter(sa.func.lower(DatasetRegistry.name) == ds_name.lower(), sa.func.lower(DatasetRegistry.source) == ds_source).order_by(DatasetRegistry.created_at.desc(), DatasetRegistry.id.desc()).first()
    if not row:
        row = DatasetRegistry(
            name=ds_name,
            source=ds_source,
            year=ds_year,
            semester=ds_semester,
            dataset_type=ds_type,
            table_name=table_value,
        )
        db.session.add(row)
    else:
        row.year = ds_year
        row.semester = ds_semester
        row.dataset_type = ds_type
        row.table_name = table_value

    if make_active:
        DatasetRegistry.query.update({'is_active': False})
        row.is_active = True
        update_app_setting('active_dataset_name', ds_name)
    return row


def _ensure_dataset_registry_synced() -> None:
    if not _dataset_registry_supported():
        return

    scraped_rows = ScrapedDataset.query.filter(ScrapedDataset.status == 'completed').all()
    for ds in scraped_rows:
        ds_name = str(getattr(ds, 'name', '') or '').strip()
        if not ds_name:
            continue
        table_name = f'scraped_dataset_{getattr(ds, "id", "")}' if getattr(ds, 'id', None) else _dataset_table_name_from_name(ds_name, 'scraped')
        _upsert_dataset_registry_entry(
            ds_name,
            'scraped',
            table_name,
            year=str(getattr(ds, 'year', '') or '').strip() or None,
            semester=str(getattr(ds, 'semester', '') or '').strip() or None,
            dataset_type=_infer_dataset_type(getattr(ds, 'semester', None)),
        )

    csv_dataset_names = [
        r[0]
        for r in db.session.query(Course.dataset_name)
        .filter(Course.dataset_name.isnot(None), sa.func.lower(sa.func.coalesce(Course.source, '')) == 'csv')
        .distinct()
        .all()
        if r and r[0]
    ]
    for ds_name in csv_dataset_names:
        sample_row = Course.query.filter(
            sa.func.lower(sa.func.coalesce(Course.source, '')) == 'csv',
            sa.func.lower(sa.func.coalesce(Course.dataset_name, '')) == ds_name.lower(),
        ).order_by(Course.last_updated.desc(), Course.id.desc()).first()
        ds_year = str(getattr(sample_row, 'year', '') or '').strip() if sample_row else ''
        ds_sem = str(getattr(sample_row, 'semester', '') or '').strip() if sample_row else ''
        _upsert_dataset_registry_entry(
            ds_name,
            'csv',
            _dataset_table_name_from_name(ds_name, 'csv'),
            year=ds_year or None,
            semester=ds_sem or None,
            dataset_type=_infer_dataset_type(ds_sem),
        )

    active_setting = str(get_app_setting('active_dataset_name', '') or '').strip()
    active_row = DatasetRegistry.query.filter(DatasetRegistry.is_active.is_(True)).first()
    if not active_row and active_setting:
        match = DatasetRegistry.query.filter(sa.func.lower(DatasetRegistry.name) == active_setting.lower()).order_by(DatasetRegistry.created_at.desc(), DatasetRegistry.id.desc()).first()
        if match:
            match.is_active = True

    db.session.commit()


def _registry_rows() -> list[DatasetRegistry]:
    if not _dataset_registry_supported():
        return []
    _ensure_dataset_registry_synced()
    return DatasetRegistry.query.order_by(DatasetRegistry.created_at.desc(), DatasetRegistry.id.desc()).all()


def _protected_tables() -> set[str]:
    return {
        'users', 'roles', 'permissions', 'role_permissions', 'registration_fields', 'login_attempts',
        'schedules', 'schedule_courses', 'subject_areas', 'courses', 'faculty', 'course_faculty',
        'faculty_advisor_area', 'settings', 'announcements', 'landing_page_sections', 'catalog_statuses',
        'ai_events', 'behavior_events', 'notifications', 'scraped_datasets', 'scraped_courses',
        'dataset_registry', 'alembic_version',
    }


def _safe_drop_table_if_exists(table_name: str) -> bool:
    name = str(table_name or '').strip()
    if not name or name.lower() in _protected_tables() or not re.match(r'^[A-Za-z0-9_]+$', name):
        return False
    try:
        db.session.execute(text(f'DROP TABLE IF EXISTS "{name}"'))
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        return False


def _orphan_tables() -> list[str]:
    try:
        insp = inspect(db.engine)
        all_tables = set(insp.get_table_names())
    except Exception:
        return []

    registered = set()
    if _dataset_registry_supported():
        for row in DatasetRegistry.query.all():
            tname = str(getattr(row, 'table_name', '') or '').strip()
            if tname:
                registered.add(tname)

    candidates = []
    for name in sorted(all_tables):
        lower = name.lower()
        if lower in _protected_tables():
            continue
        if name in registered:
            continue
        if lower.startswith('scraped_') or lower.startswith('csv_') or lower.startswith('dataset_'):
            candidates.append(name)
    return candidates


def _build_integrity_payload() -> dict:
    checks: list[dict] = []

    active_dataset_name = _resolve_effective_active_dataset_name()
    selectable_dataset_names = _all_selectable_dataset_names()
    local_course_count = int(Course.query.count() or 0)
    scraped_course_count = int(
        ScrapedCourse.query
        .join(ScrapedDataset, ScrapedCourse.dataset_id == ScrapedDataset.id)
        .filter(ScrapedDataset.status == 'completed')
        .count()
        or 0
    )
    has_any_courses = (local_course_count + scraped_course_count) > 0

    if not has_any_courses:
        checks.append(
            {
                'id': 'catalog_data',
                'status': 'critical',
                'title': 'No catalog rows available',
                'message': 'No local or scraped courses were found. Upload CSV or run scraper.',
                'fixes': [],
            }
        )
    else:
        checks.append(
            {
                'id': 'catalog_data',
                'status': 'ok',
                'title': 'Catalog rows detected',
                'message': f'Local courses: {local_course_count}, scraped courses: {scraped_course_count}.',
                'fixes': [],
            }
        )

    active_in_options = bool(active_dataset_name) and active_dataset_name in selectable_dataset_names
    if selectable_dataset_names and not active_in_options:
        checks.append(
            {
                'id': 'active_dataset',
                'status': 'warning',
                'title': 'Active dataset is missing or stale',
                'message': (
                    'Active dataset is not set.'
                    if not active_dataset_name
                    else f'Active dataset "{active_dataset_name}" is not in selectable datasets.'
                ),
                'fixes': [
                    {
                        'action': 'set_active_dataset_auto',
                        'label': 'Set active dataset to latest',
                    }
                ],
            }
        )
    else:
        checks.append(
            {
                'id': 'active_dataset',
                'status': 'ok',
                'title': 'Active dataset selection',
                'message': active_dataset_name or 'Auto-resolution enabled (no explicit active dataset).',
                'fixes': [],
            }
        )

    courses_missing_faculty = int(
        Course.query.filter(~Course.faculty.any()).count() or 0
    )
    courses_missing_faculty_with_instructor = int(
        Course.query.filter(
            ~Course.faculty.any(),
            Course.instructor.isnot(None),
            sa.func.length(sa.func.trim(Course.instructor)) > 0,
        ).count()
        or 0
    )
    if courses_missing_faculty > 0:
        fixes = []
        if courses_missing_faculty_with_instructor > 0:
            fixes.append({'action': 'link_faculty_from_instructors', 'label': 'Auto-link faculty from instructor names'})
        checks.append(
            {
                'id': 'course_faculty_links',
                'status': 'warning',
                'title': 'Courses missing faculty links',
                'message': (
                    f'{courses_missing_faculty} courses have no faculty relations. '
                    f'{courses_missing_faculty_with_instructor} include parseable instructor text.'
                ),
                'fixes': fixes,
            }
        )
    else:
        checks.append(
            {
                'id': 'course_faculty_links',
                'status': 'ok',
                'title': 'Course/faculty links',
                'message': 'All local courses have at least one faculty relation.',
                'fixes': [],
            }
        )

    orphan_faculty_count = int(Faculty.query.filter(~Faculty.courses.any()).count() or 0)
    if orphan_faculty_count > 0:
        checks.append(
            {
                'id': 'orphan_faculty',
                'status': 'warning',
                'title': 'Orphan faculty records',
                'message': f'{orphan_faculty_count} faculty records are not assigned to any course.',
                'fixes': [{'action': 'remove_orphan_faculty', 'label': 'Remove orphan faculty'}],
            }
        )
    else:
        checks.append(
            {
                'id': 'orphan_faculty',
                'status': 'ok',
                'title': 'Faculty assignment coverage',
                'message': 'No orphan faculty records detected.',
                'fixes': [],
            }
        )

    missing_dataset_status_rows = CatalogStatus.query.filter(
        CatalogStatus.status == 'available',
        sa.or_(CatalogStatus.dataset.is_(None), sa.func.length(sa.func.trim(CatalogStatus.dataset)) == 0),
    ).all()
    if missing_dataset_status_rows:
        checks.append(
            {
                'id': 'catalog_status_dataset',
                'status': 'warning',
                'title': 'Catalog status rows missing dataset label',
                'message': f'{len(missing_dataset_status_rows)} available catalog statuses have empty dataset metadata.',
                'fixes': [{'action': 'backfill_catalog_status_dataset', 'label': 'Backfill dataset metadata'}],
            }
        )
    else:
        checks.append(
            {
                'id': 'catalog_status_dataset',
                'status': 'ok',
                'title': 'Catalog status metadata',
                'message': 'All available catalog statuses include dataset metadata.',
                'fixes': [],
            }
        )

    severity_rank = {'ok': 0, 'warning': 1, 'critical': 2}
    highest = 'ok'
    for row in checks:
        if severity_rank.get(row.get('status'), 0) > severity_rank.get(highest, 0):
            highest = row.get('status')

    return {
        'summary': {
            'status': highest,
            'active_dataset_name': active_dataset_name or None,
            'selectable_dataset_names': selectable_dataset_names,
            'local_course_count': local_course_count,
            'scraped_course_count': scraped_course_count,
        },
        'checks': checks,
    }


def _sanitize_session_fields(values, max_items: int = 80, max_item_len: int = 120, max_total_bytes: int = 2800) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []

    cleaned: list[str] = []
    total = 2  # []
    for raw in values:
        item = str(raw or '').strip()
        if not item:
            continue
        if len(item) > max_item_len:
            item = item[:max_item_len]
        size = len(item.encode('utf-8')) + 3  # quotes+comma overhead
        if total + size > max_total_bytes:
            break
        if item in cleaned:
            continue
        cleaned.append(item)
        total += size
        if len(cleaned) >= max_items:
            break
    return cleaned


def _setting_truthy(value: str) -> bool:
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'on'}


def _admin_user_count() -> int:
    admin_role = Role.query.filter_by(name='admin').first()
    admin_role_id = admin_role.id if admin_role else -1
    return User.query.filter(
        (User.is_admin.is_(True))
        | (User.role_id == admin_role_id)
        | (sa.func.lower(sa.func.coalesce(User.role, '')) == 'admin')
    ).count()


def _normalize_area_type(value: str) -> str:
    v = str(value or '').strip().lower()
    if v in {'major_minor', 'minor_major'}:
        return 'both'
    if v not in {'major', 'minor', 'both', 'generic'}:
        return 'generic'
    return v


def _area_type_flags(area_type: str) -> tuple[bool, bool]:
    normalized = _normalize_area_type(area_type)
    is_major = normalized in {'major', 'both'}
    is_minor = normalized in {'minor', 'both'}
    return is_major, is_minor


def _area_type_from_form(form) -> str:
    explicit = form.get('area_type')
    if explicit:
        return _normalize_area_type(explicit)

    major_selected = ('is_major' in form)
    minor_selected = ('is_minor' in form)
    if major_selected and minor_selected:
        return 'both'
    if major_selected:
        return 'major'
    if minor_selected:
        return 'minor'
    return 'generic'


def _sync_subject_areas_from_catalogs() -> int:
    """Ensure subject_areas includes all Area_of_Study values from the unified catalog."""
    rows = _merged_courses_for_admin()
    if not rows:
        return 0

    discovered = set()
    for row in rows:
        name = str(row.get('subject_area') or '').strip()
        if name:
            discovered.add(name)

    if not discovered:
        return 0

    existing_rows = SubjectArea.query.all()
    existing_by_lower = {(s.name or '').strip().lower(): s for s in existing_rows if (s.name or '').strip()}
    created = 0
    for name in sorted(discovered):
        if name.strip().lower() in existing_by_lower:
            continue
        db.session.add(
            SubjectArea(
                name=name,
                area_type='generic',
                is_active=True,
                is_required=False,
                min_depth_credits=0.0,
            )
        )
        created += 1

    if created:
        db.session.commit()
    return created


def _subject_area_credit_caps_from_catalogs() -> dict[str, float]:
    """Return max available credits by area from the unified catalog.

    Credits are summed by unique (area, course_number), using the maximum
    observed credits for that course to avoid inflating across duplicate rows.
    """
    rows = _merged_courses_for_admin()
    if not rows:
        return {}

    try:
        course_credit_by_area: dict[tuple[str, str], float] = {}
        area_case: dict[str, str] = {}

        for row in rows:
            area_name = str(row.get('subject_area') or '').strip()
            course_number = str(row.get('course_number') or '').strip().upper()
            if not area_name or not course_number:
                continue
            try:
                credits = max(0.0, float(row.get('credits') or 0.0))
            except Exception:
                credits = 0.0

            key = (area_name.lower(), course_number)
            area_case.setdefault(area_name.lower(), area_name)
            prev = float(course_credit_by_area.get(key, 0.0))
            if credits > prev:
                course_credit_by_area[key] = credits

        totals: dict[str, float] = {}
        for (area_lower, _course), credits in course_credit_by_area.items():
            totals[area_lower] = float(totals.get(area_lower, 0.0)) + float(credits)

        return {area_case.get(k, k): round(v, 2) for k, v in totals.items()}
    except Exception:
        db.session.rollback()
        return {}

# Redirect old login to the new unified Auth login
@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    return redirect(url_for('auth.login'))

@admin_bp.route('/logout')
def logout():
    return redirect(url_for('auth.logout'))

@admin_bp.route('/')
@login_required
@admin_required
def index():
    user_count = User.query.count()
    schedule_count = Schedule.query.count()
    course_tables = [d.name for d in ScrapedDataset.query.filter_by(status='completed').all()]
    notification_count = Notification.query.count()
    announcement_count = Announcement.query.count()
    
    # SQLAlchemy Core execution for complex joins
    all_schedules = db.session.execute(text('''
        SELECT s.schedule_name, s.year, s.semester, s.schedule_type, u.username
        FROM schedules s
        JOIN users u ON s.user_id = u.id
        ORDER BY s.id DESC
    ''')).fetchall()
    
    return render_template('Admin/admin_dashboard.html', 
                           user_count=user_count, 
                           schedule_count=schedule_count,
                           notification_count=notification_count,
                           announcement_count=announcement_count,
                           course_tables=course_tables,
                           all_schedules=[dict(row._mapping) for row in all_schedules],
                           current_year_setting=get_app_setting('current_year', '2025'), 
                           current_semester_setting=get_app_setting('current_semester', 'Spring'),
                           graduation_credits_setting=get_app_setting('graduation_credits', '124'),
                           scraper_popup_open_timeout_setting=get_app_setting('scraper_popup_open_timeout', '10'),
                           scraper_popup_close_delay_setting=get_app_setting('scraper_popup_close_delay', '0.35'),
                           scraper_row_transition_delay_setting=get_app_setting('scraper_row_transition_delay', '0.0'),
                           ai_enabled_setting=get_app_setting('ai_enabled', 'false'),
                           ai_data_collection_enabled_setting=get_app_setting('ai_data_collection_enabled', 'false'))

# ==========================================
# NEW: LANDING PAGE & ANNOUNCEMENT MANAGEMENT
# ==========================================
@admin_bp.route('/landing_management', methods=['GET', 'POST'])
@login_required
@admin_required
def manage_landing():
    sections = ensure_default_landing_sections()

    def _get_section(name: str):
        return next((s for s in sections if s.name == name), None)

    hero_section = _get_section('hero')
    features_section = _get_section('features')

    if request.method == 'POST':
        action = request.form.get('_action') or 'static'

        if action == 'static':
            update_app_setting('about_us', request.form.get('about_us', ''))
            update_app_setting('creator_message', request.form.get('creator_message', ''))
            flash("Landing page content updated.", "success")
            return redirect(url_for('admin.manage_landing'))

        if action == 'sections':
            order = (request.form.get('sections_order') or '').strip()
            enabled_names = set(request.form.getlist('enabled_names'))
            names = [n.strip() for n in order.split(',') if n.strip()]

            # Assign display_order in increments so future inserts are easy.
            for idx, name in enumerate(names):
                s = LandingPageSection.query.filter_by(name=name).first()
                if not s:
                    continue
                s.display_order = (idx + 1) * 10
                s.enabled = name in enabled_names
            db.session.commit()
            flash("Landing sections updated.", "success")
            return redirect(url_for('admin.manage_landing'))

        if action == 'hero' and hero_section:
            cfg = safe_json_loads(hero_section.content_json, {})
            cfg['headline'] = request.form.get('headline', '').strip() or cfg.get('headline', '')
            cfg['subtext'] = request.form.get('subtext', '').strip()
            cfg['primary_label'] = request.form.get('primary_label', '').strip() or cfg.get('primary_label', 'Start Planning')
            cfg['secondary_label'] = request.form.get('secondary_label', '').strip() or cfg.get('secondary_label', 'Register for Premium')
            hero_section.content_json = safe_json_dumps(cfg)
            db.session.commit()
            flash("Hero section updated.", "success")
            return redirect(url_for('admin.manage_landing'))

        if action == 'features' and features_section:
            features_json = (request.form.get('features_json') or '').strip()
            cfg = safe_json_loads(features_json, None)
            if not isinstance(cfg, dict):
                flash("Features update failed: invalid JSON payload.", "error")
                return redirect(url_for('admin.manage_landing'))
            features_section.content_json = safe_json_dumps(cfg)
            db.session.commit()
            flash("Features section updated.", "success")
            return redirect(url_for('admin.manage_landing'))

        if action == 'catalog_status':
            try:
                terms = request.form.getlist('catalog_term')
                statuses = request.form.getlist('catalog_status')
                for term, status in zip(terms, statuses):
                    t = (term or '').strip()
                    s = (status or '').strip().lower()
                    if not t:
                        continue
                    if s not in {'processing', 'available'}:
                        s = 'available'
                    row = CatalogStatus.query.filter_by(term=t).first()
                    if not row:
                        row = CatalogStatus(term=t, status=s)
                        db.session.add(row)
                    else:
                        row.status = s
                db.session.commit()
                flash("Course catalog status updated.", "success")
            except Exception:
                db.session.rollback()
                flash("Course catalog status update failed (database not migrated yet).", "error")
            return redirect(url_for('admin.manage_landing'))

        flash("Unsupported landing management action.", "error")
        return redirect(url_for('admin.manage_landing'))

    about_us = get_app_setting('about_us', "Welcome to our dynamic scheduling platform.")
    creator_message = get_app_setting('creator_message', "")
    announcements = Announcement.query.order_by(Announcement.date_posted.desc()).all()

    hero_cfg = safe_json_loads(getattr(hero_section, 'content_json', None), {})
    features_cfg = safe_json_loads(getattr(features_section, 'content_json', None), {})

    # Suggest catalog terms from existing tables
    discovered_terms = [
        f"{(d.semester or '').strip()} {(d.year or '').strip()}".strip()
        for d in ScrapedDataset.query.filter_by(status='completed').all()
    ]
    try:
        catalog_rows = CatalogStatus.query.order_by(CatalogStatus.term.asc()).all()
    except Exception:
        catalog_rows = []

    existing_terms = {r.term for r in catalog_rows}
    for term in discovered_terms:
        if term and term not in existing_terms:
            catalog_rows.append(CatalogStatus(term=term, status='available'))

    return render_template(
        'Admin/manage_landing.html',
        about_us=about_us,
        creator_message=creator_message,
        announcements=announcements,
        landing_sections=sections,
        hero_cfg=hero_cfg,
        features_cfg=features_cfg,
        catalog_statuses=catalog_rows,
    )

@admin_bp.route('/announcement/create', methods=['POST'])
@login_required
@admin_required
def create_announcement():
    title = request.form.get('title')
    content = request.form.get('content')
    ann_type = (request.form.get('ann_type') or 'info').strip().lower()
    if ann_type not in {'info', 'update', 'warning'}:
        ann_type = 'info'
    if title and content:
        new_announcement = Announcement(title=title, content=content, ann_type=ann_type)
        db.session.add(new_announcement)
        db.session.commit()
        flash("Announcement created.", "success")
    else:
        flash("Title and content are required.", "error")
    return redirect(url_for('admin.manage_landing'))


@admin_bp.route('/announcement/edit/<int:announcement_id>', methods=['POST'])
@login_required
@admin_required
def edit_announcement(announcement_id):
    ann = Announcement.query.get_or_404(announcement_id)
    title = (request.form.get('title') or '').strip()
    content = (request.form.get('content') or '').strip()
    ann_type = (request.form.get('ann_type') or 'info').strip().lower()
    if ann_type not in {'info', 'update', 'warning'}:
        ann_type = 'info'

    if not title or not content:
        flash('Title and content are required.', 'error')
        return redirect(url_for('admin.manage_landing'))

    ann.title = title
    ann.content = content
    ann.ann_type = ann_type
    db.session.commit()
    flash('Announcement updated.', 'success')
    return redirect(url_for('admin.manage_landing'))

@admin_bp.route('/announcement/toggle/<int:announcement_id>', methods=['POST'])
@login_required
@admin_required
def toggle_announcement(announcement_id):
    ann = Announcement.query.get_or_404(announcement_id)
    ann.is_active = not ann.is_active
    db.session.commit()
    status = 'active' if ann.is_active else 'hidden'
    flash(f"Announcement '{ann.title}' is now {status}.", "success")
    return redirect(url_for('admin.manage_landing'))

@admin_bp.route('/announcement/delete/<int:announcement_id>', methods=['POST'])
@login_required
@admin_required
def delete_announcement(announcement_id):
    ann = Announcement.query.get_or_404(announcement_id)
    db.session.delete(ann)
    db.session.commit()
    flash("Announcement deleted permanently.", "success")
    return redirect(url_for('admin.manage_landing'))

# ==========================================
# EXISTING ROUTES 
# ==========================================
@admin_bp.route('/datasets')
@login_required
@admin_required
def manage_datasets():
    from services.scraper_service import scraper_progress as _progress, is_scraper_running

    _ensure_dataset_registry_synced()

    recent_datasets = (
        ScrapedDataset.query.order_by(ScrapedDataset.created_at.desc()).limit(25).all()
    )
    active_dataset = ScrapedDataset.query.filter_by(status='running').order_by(ScrapedDataset.created_at.desc()).first()
    completed_count = ScrapedDataset.query.filter_by(status='completed').count()
    failed_count = ScrapedDataset.query.filter_by(status='failed').count()
    csv_count = Course.query.filter(sa.func.lower(sa.func.coalesce(Course.source, '')) == 'csv').count()
    latest_csv_upload = Course.query.filter(sa.func.lower(sa.func.coalesce(Course.source, '')) == 'csv').order_by(Course.last_updated.desc(), Course.id.desc()).first()
    active_dataset_name = _resolve_effective_active_dataset_name() or None
    registry_rows = _registry_rows()

    return render_template(
        'Admin/manage_datasets.html',
        recent_datasets=recent_datasets,
        registry_rows=registry_rows,
        active_dataset=active_dataset,
        completed_count=completed_count,
        failed_count=failed_count,
        csv_count=csv_count,
        latest_csv_upload=latest_csv_upload,
        active_dataset_name=active_dataset_name,
        scraper_progress=dict(_progress),
        scraper_running=is_scraper_running(),
        orphan_tables=_orphan_tables(),
    )

@admin_bp.route('/datasets/upload', methods=['POST'])
@admin_bp.route('/upload-dataset', methods=['POST'])
@login_required
@admin_required
def upload_dataset():
    file = request.files.get('file') or request.files.get('dataset_file')
    if not file or not str(file.filename or '').strip():
        flash('Please choose a CSV file to upload.', 'error')
        return redirect(url_for('admin.manage_datasets'))

    filename = str(file.filename or '').strip().lower()
    if not filename.endswith('.csv'):
        flash('Only CSV uploads are supported.', 'error')
        return redirect(url_for('admin.manage_datasets'))

    year_hint = str(request.form.get('year') or '').strip()
    semester_hint = str(request.form.get('semester') or '').strip()
    dataset_name = str(request.form.get('dataset_name') or '').strip()
    if not dataset_name:
        inferred_year = year_hint or get_app_setting('current_year', '')
        inferred_sem = semester_hint or get_app_setting('current_semester', '')
        dataset_name = f"{inferred_year}_{inferred_sem}_CSV".strip('_')

    def _norm_key(value: str) -> str:
        return re.sub(r'[^a-z0-9]+', '', str(value or '').strip().lower())

    alias = {
        'course_number': {'coursenumber', 'course', 'coursecode', 'courseid', 'number'},
        'title': {'coursetitle', 'title', 'subject', 'coursetitleen'},
        'credits': {'credits', 'credit'},
        'day_period': {'dayandperiod', 'dayperiod', 'dayandtime', 'period', 'day'},
        'semester': {'semester', 'term'},
        'year': {'year', 'academicyear', 'yearoffered', 'yearavailable'},
        'prerequisite': {'prerequisite', 'prerequisites', 'prereq'},
        'subject_area': {'subjectarea', 'areaofstudy', 'subject', 'area'},
        'instructor': {'instructor', 'faculty', 'teacher', 'lecturer'},
    }

    def _pick(row: dict, key: str) -> str:
        normalized_map = {_norm_key(k): (v or '') for k, v in (row or {}).items()}
        for name in alias.get(key, set()):
            if name in normalized_map and str(normalized_map[name]).strip():
                return str(normalized_map[name]).strip()
        return ''

    def _normalize_term_values(raw_semester: str, raw_year: str) -> tuple[str | None, str | None]:
        sem = str(raw_semester or '').strip()
        yr = str(raw_year or '').strip()

        if not sem and not yr:
            return None, None

        if sem:
            sem_match = re.search(r'(spring|summer|fall|winter)', sem, flags=re.IGNORECASE)
            if sem_match:
                sem = sem_match.group(1).capitalize()

            if not yr:
                year_match = re.search(r'\b(20\d{2})\b', str(raw_semester or ''), flags=re.IGNORECASE)
                if year_match:
                    yr = year_match.group(1)

        if yr:
            year_match = re.search(r'\b(20\d{2})\b', yr, flags=re.IGNORECASE)
            if year_match:
                yr = year_match.group(1)
            elif not yr.isdigit():
                yr = ''

        return (sem or None), (yr or None)

    inserted = 0
    updated = 0
    skipped_manual = 0
    skipped_invalid = 0

    try:
        reader = csv.DictReader(TextIOWrapper(file.stream, encoding='utf-8-sig', newline=''))
        for raw_row in reader:
            course_number = _pick(raw_row, 'course_number')
            title = _pick(raw_row, 'title')
            day_period = _pick(raw_row, 'day_period')
            if not course_number or not title or not day_period:
                skipped_invalid += 1
                continue

            credits_raw = _pick(raw_row, 'credits')
            try:
                credits = float(credits_raw) if str(credits_raw or '').strip() else 0.0
            except Exception:
                credits = 0.0

            semester_raw = _pick(raw_row, 'semester') or semester_hint or ''
            year_raw = _pick(raw_row, 'year') or year_hint or ''
            semester, year = _normalize_term_values(semester_raw, year_raw)

            existing = (
                Course.query.filter(
                    sa.func.lower(Course.course_number) == course_number.strip().lower(),
                    sa.func.lower(Course.day_period) == day_period.strip().lower(),
                )
                .order_by(Course.last_updated.desc(), Course.id.desc())
                .first()
            )

            payload = {
                'course_number': course_number,
                'title': title,
                'credits': credits,
                'day_period': day_period,
                'semester': semester,
                'year': year,
                'prerequisite': _pick(raw_row, 'prerequisite') or None,
                'subject_area': _pick(raw_row, 'subject_area') or None,
                'instructor': _pick(raw_row, 'instructor') or None,
                'dataset_name': dataset_name or None,
                'source': 'csv',
            }

            if existing:
                if str(existing.source or '').strip().lower() == 'manual':
                    skipped_manual += 1
                    continue
                for key, value in payload.items():
                    setattr(existing, key, value)
                _sync_course_faculty_from_instructor(existing, payload.get('instructor'))
                updated += 1
            else:
                new_row = Course(**payload)
                _sync_course_faculty_from_instructor(new_row, payload.get('instructor'))
                db.session.add(new_row)
                inserted += 1

        db.session.commit()

        _upsert_dataset_registry_entry(dataset_name, 'csv', _dataset_table_name_from_name(dataset_name, 'csv'))
        if not _active_registry_row():
            _set_active_registry_dataset(dataset_name)
        else:
            db.session.commit()

        if semester and year:
            term_key = f"{semester} {year}".strip()
            row = CatalogStatus.query.filter_by(term=term_key).first()
            if not row:
                row = CatalogStatus(term=term_key, status='available', dataset=dataset_name)
                db.session.add(row)
            else:
                row.status = 'available'
                row.dataset = dataset_name
            db.session.commit()

        flash(
            f'CSV uploaded. Inserted: {inserted}, Updated: {updated}, '
            f'Skipped manual: {skipped_manual}, Skipped invalid: {skipped_invalid}.',
            'success',
        )
    except Exception as exc:
        db.session.rollback()
        flash(f'CSV upload failed: {exc}', 'error')

    return redirect(url_for('admin.manage_datasets'))


@admin_bp.route('/active-dataset', methods=['POST'])
@login_required
@admin_required
def set_active_dataset():
    payload = request.get_json(silent=True) if request.is_json else {}
    payload = payload or {}
    dataset_name = str(payload.get('dataset_name') or request.form.get('dataset_name') or '').strip()
    if dataset_name:
        _set_active_registry_dataset(dataset_name)
        message = f'Active dataset set to {dataset_name}.'
    else:
        _set_active_registry_dataset(None)
        message = 'Active dataset cleared. System will auto-resolve latest dataset.'

    if request.is_json:
        return jsonify({'success': True, 'message': message, 'active_dataset_name': dataset_name or None})

    flash(message, 'success')
    return redirect(url_for('admin.university_connection'))

@admin_bp.route('/datasets/delete/<table_name>', methods=['POST'])
@login_required
@admin_required
def delete_dataset(table_name):
    dropped = _safe_drop_table_if_exists(table_name)
    flash('Dataset table deleted.' if dropped else 'Delete skipped (table missing/protected).', 'success' if dropped else 'warning')
    return redirect(url_for('admin.manage_datasets'))


def _delete_registry_dataset_row(row: DatasetRegistry) -> dict:
    dataset_name = str(getattr(row, 'name', '') or '').strip()
    source = str(getattr(row, 'source', '') or '').strip().lower()
    table_name = str(getattr(row, 'table_name', '') or '').strip()

    dropped_table = _safe_drop_table_if_exists(table_name)
    deleted_rows = 0

    if source == 'csv' and dataset_name:
        q = Course.query.filter(
            sa.func.lower(sa.func.coalesce(Course.source, '')) == 'csv',
            sa.func.lower(sa.func.coalesce(Course.dataset_name, '')) == dataset_name.lower(),
        )
        deleted_rows = int(q.count() or 0)
        q.delete(synchronize_session=False)

    if source == 'scraped' and dataset_name:
        ds_rows = ScrapedDataset.query.filter(sa.func.lower(ScrapedDataset.name) == dataset_name.lower()).all()
        deleted_rows = int(len(ds_rows))
        for ds in ds_rows:
            db.session.delete(ds)

    was_active = bool(getattr(row, 'is_active', False))
    db.session.delete(row)
    db.session.commit()

    if was_active:
        next_row = _active_registry_row()
        _set_active_registry_dataset(next_row.name if next_row else None)

    return {
        'dataset_name': dataset_name,
        'source': source,
        'table_name': table_name,
        'dropped_table': dropped_table,
        'deleted_rows': deleted_rows,
    }


@admin_bp.route('/api/datasets', methods=['GET'])
@login_required
@admin_required
def api_datasets():
    _ensure_dataset_registry_synced()
    rows = _registry_rows()
    payload = [
        {
            'id': r.id,
            'name': r.name,
            'source': r.source,
            'table_name': r.table_name,
            'created_at': r.created_at.isoformat() if r.created_at else None,
            'is_active': bool(r.is_active),
            'status': 'Active' if r.is_active else 'Inactive',
        }
        for r in rows
    ]
    return jsonify({'success': True, 'datasets': payload, 'active_dataset_name': _resolve_effective_active_dataset_name() or None})


@admin_bp.route('/api/datasets/<int:dataset_id>/activate', methods=['POST'])
@login_required
@admin_required
def api_activate_dataset(dataset_id: int):
    if not _dataset_registry_supported():
        return jsonify({'success': False, 'message': 'Dataset registry is unavailable (run migrations).'}), 500
    row = DatasetRegistry.query.get(dataset_id)
    if not row:
        return jsonify({'success': False, 'message': 'Dataset not found.'}), 404
    _set_active_registry_dataset(row.name)
    return jsonify({'success': True, 'message': f'Active dataset set to {row.name}.', 'active_dataset_name': row.name})


@admin_bp.route('/api/datasets/<int:dataset_id>/delete', methods=['POST'])
@login_required
@admin_required
def api_delete_dataset(dataset_id: int):
    if not _dataset_registry_supported():
        return jsonify({'success': False, 'message': 'Dataset registry is unavailable (run migrations).'}), 500
    row = DatasetRegistry.query.get(dataset_id)
    if not row:
        return jsonify({'success': False, 'message': 'Dataset not found.'}), 404
    result = _delete_registry_dataset_row(row)
    return jsonify({'success': True, 'message': f'Dataset {result.get("dataset_name")} deleted.', 'result': result, 'active_dataset_name': _resolve_effective_active_dataset_name() or None})


@admin_bp.route('/api/datasets/<int:dataset_id>/refresh', methods=['POST'])
@login_required
@admin_required
def api_refresh_dataset(dataset_id: int):
    if not _dataset_registry_supported():
        return jsonify({'success': False, 'message': 'Dataset registry is unavailable (run migrations).'}), 500
    row = DatasetRegistry.query.get(dataset_id)
    if not row:
        return jsonify({'success': False, 'message': 'Dataset not found.'}), 404

    row.created_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True, 'message': f'Dataset {row.name} metadata refreshed.'})


@admin_bp.route('/api/datasets/orphans', methods=['GET'])
@login_required
@admin_required
def api_orphan_tables():
    return jsonify({'success': True, 'orphans': _orphan_tables()})


@admin_bp.route('/api/datasets/orphans/clean', methods=['POST'])
@login_required
@admin_required
def api_clean_orphan_tables():
    deleted: list[str] = []
    skipped: list[str] = []
    for table_name in _orphan_tables():
        if _safe_drop_table_if_exists(table_name):
            deleted.append(table_name)
        else:
            skipped.append(table_name)
    return jsonify(
        {
            'success': True,
            'message': f'Orphan cleanup finished. Deleted {len(deleted)} table(s).',
            'deleted': deleted,
            'skipped': skipped,
        }
    )

@admin_bp.route('/update_app_settings', methods=['POST'])
@login_required
@admin_required
def update_app_settings_route():
    update_app_setting('current_year', request.form.get('current_year_setting'))
    update_app_setting('current_semester', request.form.get('current_semester_setting'))

    grad_credits = _safe_float(request.form.get('graduation_credits_setting'), 124.0)
    if grad_credits <= 0:
        grad_credits = 124.0
    update_app_setting('graduation_credits', str(grad_credits))

    popup_open_timeout = _safe_float(request.form.get('scraper_popup_open_timeout_setting'), 10.0)
    if popup_open_timeout < 2.0:
        popup_open_timeout = 2.0
    if popup_open_timeout > 40.0:
        popup_open_timeout = 40.0
    update_app_setting('scraper_popup_open_timeout', str(popup_open_timeout))

    popup_close_delay = _safe_float(request.form.get('scraper_popup_close_delay_setting'), 0.35)
    if popup_close_delay < 0.0:
        popup_close_delay = 0.0
    if popup_close_delay > 5.0:
        popup_close_delay = 5.0
    update_app_setting('scraper_popup_close_delay', str(popup_close_delay))

    row_transition_delay = _safe_float(request.form.get('scraper_row_transition_delay_setting'), _safe_float(get_app_setting('scraper_row_transition_delay', '0.0'), 0.0))
    if row_transition_delay < 0.0:
        row_transition_delay = 0.0
    if row_transition_delay > 15.0:
        row_transition_delay = 15.0
    update_app_setting('scraper_row_transition_delay', str(row_transition_delay))

    ai_enabled = 'ai_enabled_setting' in request.form
    ai_data_collection_enabled = 'ai_data_collection_enabled_setting' in request.form
    update_app_setting('ai_enabled', 'true' if ai_enabled else 'false')
    update_app_setting('ai_data_collection_enabled', 'true' if ai_data_collection_enabled else 'false')

    flash("System settings updated.", "success")
    return redirect(url_for('admin.index'))

@admin_bp.route('/periods')
@login_required
@admin_required
def manage_periods():
    periods = Period.query.order_by(Period.name.asc()).all()
    return render_template(
        'Admin/periods.html',
        periods=periods,
        lunch_start_time=get_app_setting('lunch_start_time', '11:40'),
        lunch_duration_minutes=get_app_setting('lunch_duration_minutes', '60'),
    )

@admin_bp.route('/users')
@login_required
@admin_required
def manage_users():
    users = User.query.order_by(User.id.asc()).all()
    roles = Role.query.order_by(Role.name.asc()).all()
    return render_template('Admin/manage_users.html', users=users, roles=roles)


@admin_bp.route('/roles', methods=['GET', 'POST'])
@login_required
@admin_required
def manage_roles():
    permissions = Permission.query.order_by(Permission.label.asc()).all()
    if request.method == 'POST':
        action = (request.form.get('action') or '').strip()

        if action == 'create':
            name = (request.form.get('name') or '').strip().lower()
            description = (request.form.get('description') or '').strip()
            if not name:
                flash('Role name is required.', 'error')
                return redirect(url_for('admin.manage_roles'))
            existing = Role.query.filter(sa.func.lower(Role.name) == name).first()
            if existing:
                flash('Role already exists.', 'error')
                return redirect(url_for('admin.manage_roles'))

            role = Role(name=name, description=description, is_system=False)
            selected = set(request.form.getlist('permission_keys'))
            role.permissions = [p for p in permissions if p.key in selected]
            db.session.add(role)
            db.session.commit()
            flash('Role created.', 'success')
            return redirect(url_for('admin.manage_roles'))

        if action == 'update':
            role_id = request.form.get('role_id', type=int)
            role = Role.query.get(role_id)
            if not role:
                flash('Role not found.', 'error')
                return redirect(url_for('admin.manage_roles'))
            if role.is_system and role.name == 'admin':
                role.name = 'admin'

            if not role.is_system:
                role.name = (request.form.get('name') or role.name).strip().lower() or role.name
            role.description = (request.form.get('description') or '').strip() or role.description

            selected = set(request.form.getlist('permission_keys'))
            if role.name == 'admin':
                role.permissions = permissions
            else:
                role.permissions = [p for p in permissions if p.key in selected]
            db.session.commit()
            flash('Role updated.', 'success')
            return redirect(url_for('admin.manage_roles'))

        if action == 'delete':
            role_id = request.form.get('role_id', type=int)
            role = Role.query.get(role_id)
            if not role:
                flash('Role not found.', 'error')
                return redirect(url_for('admin.manage_roles'))
            if role.is_system:
                flash('System roles cannot be deleted.', 'error')
                return redirect(url_for('admin.manage_roles'))
            affected_users = User.query.filter_by(role_id=role.id).all()
            user_role = Role.query.filter_by(name='user').first()
            fallback_role_id = user_role.id if user_role else None
            for user in affected_users:
                user.role_id = fallback_role_id
                user.role = 'user'
                user.is_admin = False
            db.session.delete(role)
            db.session.commit()
            flash('Role deleted.', 'success')
            return redirect(url_for('admin.manage_roles'))

    roles = Role.query.order_by(Role.name.asc()).all()
    return render_template('Admin/roles.html', roles=roles, permissions=permissions)


@admin_bp.route('/roles/assign', methods=['POST'])
@login_required
@admin_required
def assign_user_role():
    user_id = request.form.get('user_id', type=int)
    role_id = request.form.get('role_id', type=int)
    target = User.query.get(user_id)
    role = Role.query.get(role_id)

    if not target or not role:
        flash('Invalid role assignment request.', 'error')
        return redirect(url_for('admin.manage_users'))

    if current_user.id == target.id and role.name == 'admin' and not current_user.is_admin:
        flash('You cannot self-assign admin access.', 'error')
        return redirect(url_for('admin.manage_users'))

    if not current_user.is_admin and role.name == 'admin':
        flash('Only admins can assign the admin role.', 'error')
        return redirect(url_for('admin.manage_users'))

    if not current_user.is_admin:
        missing = [p.key for p in role.permissions if not current_user.has_permission(p.key)]
        if missing:
            flash('You cannot assign a role with permissions you do not have.', 'error')
            return redirect(url_for('admin.manage_users'))

    previous_was_admin = bool(target.is_admin or target.effective_role_name() == 'admin')

    target.role_id = role.id
    target.role = role.name
    target.is_admin = (role.name == 'admin')

    if previous_was_admin and role.name != 'admin' and _admin_user_count() <= 1:
        db.session.rollback()
        flash('At least one admin account must exist.', 'error')
        return redirect(url_for('admin.manage_users'))

    db.session.commit()
    flash('User role updated.', 'success')
    return redirect(url_for('admin.manage_users'))


@admin_bp.route('/registration_fields', methods=['GET', 'POST'])
@login_required
@admin_required
def manage_registration_fields():
    if request.method == 'POST':
        action = (request.form.get('action') or '').strip()
        if action == 'create':
            field_key = (request.form.get('field_key') or '').strip().lower()
            label = (request.form.get('label') or '').strip()
            input_type = (request.form.get('input_type') or 'text').strip().lower()
            options_raw = (request.form.get('options_csv') or '').strip()
            if not field_key or not label:
                flash('Field key and label are required.', 'error')
                return redirect(url_for('admin.manage_registration_fields'))
            if RegistrationField.query.filter_by(field_key=field_key).first():
                flash('Field key already exists.', 'error')
                return redirect(url_for('admin.manage_registration_fields'))
            if input_type not in {'text', 'textarea', 'select'}:
                input_type = 'text'

            options_json = None
            if input_type == 'select' and options_raw:
                options = [x.strip() for x in options_raw.split(',') if x.strip()]
                options_json = json.dumps(options)

            db.session.add(
                RegistrationField(
                    field_key=field_key,
                    label=label,
                    input_type=input_type,
                    options_json=options_json,
                    is_required=('is_required' in request.form),
                    is_active=('is_active' in request.form),
                    display_order=request.form.get('display_order', type=int) or 0,
                )
            )
            db.session.commit()
            flash('Registration field created.', 'success')
            return redirect(url_for('admin.manage_registration_fields'))

        if action == 'update':
            field_id = request.form.get('field_id', type=int)
            field = RegistrationField.query.get(field_id)
            if not field:
                flash('Field not found.', 'error')
                return redirect(url_for('admin.manage_registration_fields'))

            field.label = (request.form.get('label') or field.label).strip() or field.label
            field.is_required = ('is_required' in request.form)
            field.is_active = ('is_active' in request.form)
            field.display_order = request.form.get('display_order', type=int) or field.display_order

            input_type = (request.form.get('input_type') or field.input_type).strip().lower()
            if input_type not in {'text', 'textarea', 'select'}:
                input_type = 'text'
            field.input_type = input_type

            options_raw = (request.form.get('options_csv') or '').strip()
            if field.input_type == 'select':
                options = [x.strip() for x in options_raw.split(',') if x.strip()]
                field.options_json = json.dumps(options)
            else:
                field.options_json = None
            db.session.commit()
            flash('Registration field updated.', 'success')
            return redirect(url_for('admin.manage_registration_fields'))

        if action == 'delete':
            field_id = request.form.get('field_id', type=int)
            field = RegistrationField.query.get(field_id)
            if field and field.field_key not in {'gender', 'career_interests'}:
                db.session.delete(field)
                db.session.commit()
                flash('Registration field deleted.', 'success')
            else:
                flash('This field cannot be deleted.', 'error')
            return redirect(url_for('admin.manage_registration_fields'))

    fields = RegistrationField.query.order_by(RegistrationField.display_order.asc(), RegistrationField.id.asc()).all()
    return render_template('Admin/registration_fields.html', fields=fields)

@admin_bp.route('/api/update_user_demographics', methods=['POST'])
@login_required
@admin_required
def update_user_demographics():
    data = request.get_json(silent=True) or {}
    user_id = data.get('id')
    user = User.query.get(user_id)
    if not user:
        return jsonify({'success': False, 'message': 'User not found'}), 404

    gender = (data.get('gender') or '').strip() or None
    expected_graduation = data.get('expected_graduation')

    if expected_graduation in (None, ''):
        expected_graduation = None
    else:
        try:
            expected_graduation = int(expected_graduation)
        except Exception:
            return jsonify({'success': False, 'message': 'Expected graduation must be a year.'}), 400

    try:
        user.gender = gender
        user.expected_graduation = expected_graduation

        # Backward-compatible best-effort update for databases that still have year_of_study.
        try:
            insp = inspect(db.engine)
            user_cols = {c['name'] for c in insp.get_columns('users')}
            if 'year_of_study' in user_cols:
                db.session.execute(
                    text('UPDATE users SET year_of_study = :y WHERE id = :id'),
                    {'y': expected_graduation, 'id': user.id},
                )
        except Exception:
            pass

        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': 'Unable to update user demographics.'}), 500

@admin_bp.route('/api/delete_user/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    try:
        user = User.query.get(user_id)
        if user:
            if user.id == current_user.id:
                return jsonify({'success': False, 'message': 'You cannot delete your own account.'}), 400
            if (user.is_admin or user.effective_role_name() == 'admin') and _admin_user_count() <= 1:
                return jsonify({'success': False, 'message': 'At least one admin account must exist.'}), 400
            db.session.delete(user)
            db.session.commit()
            return jsonify({'success': True})
        return jsonify({'success': False, 'message': 'User not found'}), 404
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': 'Unable to delete user.'}), 500

@admin_bp.route('/subjects', methods=['GET', 'POST'])
@login_required
@admin_required
def manage_subjects():
    try:
        _sync_subject_areas_from_catalogs()
    except Exception:
        db.session.rollback()

    caps = _subject_area_credit_caps_from_catalogs()
    cap_by_lower = {k.strip().lower(): float(v or 0.0) for k, v in caps.items()}

    rows = SubjectArea.query.order_by(SubjectArea.name.asc()).all()
    if cap_by_lower:
        rows = [r for r in rows if (r.name or '').strip().lower() in cap_by_lower]

    subjects_rows = []
    for row in rows:
        role = _normalize_area_type(getattr(row, 'area_type', 'generic'))
        if role not in {'major', 'minor', 'both', 'generic'}:
            role = 'generic'

        major_min = float(getattr(row, 'min_major_credits', 0.0) or 0.0)
        minor_min = float(getattr(row, 'min_minor_credits', 0.0) or 0.0)
        depth_min = float(getattr(row, 'min_depth_credits', 0.0) or 0.0)

        if major_min <= 0 and role in {'major', 'both'}:
            major_min = depth_min
        if minor_min <= 0 and role in {'minor', 'both'}:
            minor_min = depth_min

        cap = float(cap_by_lower.get((row.name or '').strip().lower(), 0.0))
        subjects_rows.append(
            {
                'id': row.id,
                'name': row.name,
                'counts_as_area': bool(getattr(row, 'is_active', True)),
                'role': role,
                'major_min_credits': round(max(0.0, major_min), 2),
                'minor_min_credits': round(max(0.0, minor_min), 2),
                'available_credits': round(max(0.0, cap), 2),
            }
        )

    return render_template('Admin/subjects.html', subjects_rows=subjects_rows, has_datasets=bool(cap_by_lower))


@admin_bp.route('/api/subjects/<int:subject_id>', methods=['POST'])
@login_required
@admin_required
def api_update_subject_area(subject_id: int):
    payload = request.get_json(silent=True) or {}
    subject = SubjectArea.query.get(subject_id)
    if not subject:
        return jsonify({'success': False, 'message': 'Study area not found.'}), 404

    try:
        counts_as_area = bool(payload.get('counts_as_area', getattr(subject, 'is_active', True)))
        role = _normalize_area_type(payload.get('role') or getattr(subject, 'area_type', 'major'))
        if role not in {'major', 'minor', 'both', 'generic'}:
            return jsonify({'success': False, 'message': 'Role must be Major, Minor, Both, or Generic.'}), 400

        try:
            major_min = float(payload.get('major_min_credits', getattr(subject, 'min_major_credits', 0.0)) or 0.0)
            minor_min = float(payload.get('minor_min_credits', getattr(subject, 'min_minor_credits', 0.0)) or 0.0)
        except Exception:
            return jsonify({'success': False, 'message': 'Credit minimums must be numeric.'}), 400

        if major_min < 0 or minor_min < 0:
            return jsonify({'success': False, 'message': 'Credit minimums cannot be negative.'}), 400

        caps = _subject_area_credit_caps_from_catalogs()
        caps_lower = {k.strip().lower(): float(v or 0.0) for k, v in caps.items()}
        cap = float(caps_lower.get((subject.name or '').strip().lower(), 0.0) or 0.0)

        if role == 'major':
            minor_min = 0.0
        elif role == 'minor':
            major_min = 0.0
        elif role == 'generic':
            major_min = 0.0
            minor_min = 0.0

        if major_min > cap or minor_min > cap:
            return jsonify(
                {
                    'success': False,
                    'message': f'Credit minimum cannot exceed available dataset credits ({round(cap, 2)}).',
                }
            ), 400

        subject.is_active = counts_as_area
        subject.area_type = role if counts_as_area else 'generic'
        subject.min_major_credits = round(max(0.0, major_min), 2)
        subject.min_minor_credits = round(max(0.0, minor_min), 2)

        effective_major = subject.min_major_credits if role in {'major', 'both'} else 0.0
        effective_minor = subject.min_minor_credits if role in {'minor', 'both'} else 0.0
        subject.min_depth_credits = round(max(effective_major, effective_minor), 2)

        db.session.commit()
        return jsonify(
            {
                'success': True,
                'message': 'Study area updated.',
                'subject': {
                    'id': subject.id,
                    'counts_as_area': bool(subject.is_active),
                    'role': role,
                    'major_min_credits': subject.min_major_credits,
                    'minor_min_credits': subject.min_minor_credits,
                    'available_credits': round(cap, 2),
                },
            }
        )
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': 'Unable to update subject area.'}), 500

@admin_bp.route('/instructors')
@login_required
@admin_required
def manage_instructors():
    return redirect(url_for('admin.manage_faculty'))


@admin_bp.route('/faculty')
@login_required
@admin_required
def manage_faculty():
    return render_template('Admin/manage_faculty.html')

@admin_bp.route('/api/update_instructor/<int:instructor_id>', methods=['POST'])
@login_required
@admin_required
def api_update_instructor(instructor_id):
    data = request.get_json(silent=True) or {}
    new_name = (data.get('name') or '').strip()
    if not new_name:
        return jsonify({'success': False, 'message': 'New instructor name is required.'}), 400
    row = Faculty.query.get(instructor_id)
    if not row:
        return jsonify({'success': False, 'message': 'Faculty not found.'}), 404
    row.name = new_name
    db.session.commit()
    return jsonify({'success': True, 'updated_rows': 1})


@admin_bp.route('/api/instructors/delete', methods=['POST'])
@login_required
@admin_required
def api_delete_instructor():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'success': False, 'message': 'Instructor name is required.'}), 400
    row = Faculty.query.filter(sa.func.lower(Faculty.name) == name.lower()).first()
    if not row:
        return jsonify({'success': False, 'message': 'Faculty not found.'}), 404
    db.session.delete(row)
    db.session.commit()
    return jsonify({'success': True, 'updated_rows': 1})

@admin_bp.route('/courses')
@login_required
@admin_required
def manage_courses():
    faculties = Faculty.query.order_by(Faculty.name.asc()).all()
    subject_areas = SubjectArea.query.order_by(SubjectArea.name.asc()).all()
    return render_template('Admin/manage_courses.html', faculties=faculties, subject_areas=subject_areas)


def _validate_course_payload(payload: dict, *, exclude_ref: str | None = None) -> tuple[bool, str | None]:
    course_number = str(payload.get('course_number') or '').strip()
    title = str(payload.get('title') or '').strip()
    day_period = str(payload.get('day_period') or '').strip()
    semester = str(payload.get('semester') or '').strip()
    year = str(payload.get('year') or '').strip()

    if not course_number:
        return False, 'course_number is required.'
    if not title:
        return False, 'title is required.'
    if not day_period:
        return False, 'day_period is required.'

    new_key = _course_merge_key(course_number, day_period, semester, year)
    merged = _merged_courses_for_admin()
    for row in merged:
        if exclude_ref and str(row.get('ref') or '').strip() == exclude_ref:
            continue
        row_key = _course_merge_key(
            row.get('course_number'),
            row.get('day_period'),
            row.get('semester'),
            row.get('year'),
        )
        if row_key == new_key:
            return False, 'A course with the same course_number, day_period, semester, and year already exists.'
    return True, None


def _manual_course_from_scraped(scraped_row: ScrapedCourse, overrides: dict | None = None) -> Course:
    payload = {
        'course_number': scraped_row.course_number,
        'title': scraped_row.course_title,
        'credits': scraped_row.credits,
        'day_period': scraped_row.day_and_period,
        'semester': scraped_row.dataset.semester if scraped_row.dataset else None,
        'year': scraped_row.dataset.year if scraped_row.dataset else None,
        'prerequisite': scraped_row.prerequisite,
        'subject_area': scraped_row.area_of_study,
        'instructor': scraped_row.instructor,
        'source': 'manual',
        'dataset_name': getattr(scraped_row.dataset, 'name', None) if getattr(scraped_row, 'dataset', None) else None,
    }
    if overrides:
        payload.update({k: v for k, v in overrides.items() if v is not None})
    return Course(**payload)


def _manual_course_from_local(local_row: Course, overrides: dict | None = None) -> Course:
    payload = {
        'course_number': local_row.course_number,
        'title': local_row.title,
        'credits': local_row.credits,
        'day_period': local_row.day_period,
        'semester': local_row.semester,
        'year': local_row.year,
        'prerequisite': local_row.prerequisite,
        'subject_area': local_row.subject_area,
        'instructor': local_row.instructor,
        'source': 'manual',
        'dataset_name': local_row.dataset_name,
    }
    if overrides:
        payload.update({k: v for k, v in overrides.items() if v is not None})
    return Course(**payload)


def _resolve_manual_course_ref(course_ref: str) -> Course | None:
    parsed = _parse_course_ref(course_ref)
    if not parsed:
        return None
    source, row_id = parsed
    if source in {'manual', 'csv'}:
        return Course.query.get(row_id)
    scraped = ScrapedCourse.query.get(row_id)
    if not scraped:
        return None
    return _manual_course_from_scraped(scraped)


@admin_bp.route('/api/courses', methods=['GET', 'POST'])
@login_required
@admin_required
def api_courses():
    if request.method == 'GET':
        courses = _merged_courses_for_admin()
        faculty_rows = Faculty.query.order_by(Faculty.name.asc()).all()
        areas = SubjectArea.query.order_by(SubjectArea.name.asc()).all()
        return jsonify(
            {
                'success': True,
                'courses': courses,
                'faculty': [_serialize_faculty(r) for r in faculty_rows],
                'advisor_areas': [{'id': a.id, 'name': a.name} for a in areas],
            }
        )

    payload = request.get_json(silent=True) or {}
    ok, err = _validate_course_payload(payload)
    if not ok:
        return jsonify({'success': False, 'message': err}), 400

    row = Course(
        course_number=str(payload.get('course_number') or '').strip(),
        title=str(payload.get('title') or '').strip(),
        credits=_safe_float(payload.get('credits'), 0.0),
        day_period=str(payload.get('day_period') or '').strip(),
        semester=str(payload.get('semester') or '').strip() or None,
        year=str(payload.get('year') or '').strip() or None,
        prerequisite=str(payload.get('prerequisite') or '').strip() or None,
        subject_area=str(payload.get('subject_area') or '').strip() or None,
        instructor=str(payload.get('instructor') or '').strip() or None,
        source='manual',
    )
    faculty_ids = payload.get('faculty_ids') or []
    if isinstance(faculty_ids, list):
        row.faculty = Faculty.query.filter(Faculty.id.in_(faculty_ids)).all() if faculty_ids else []
    if not row.faculty:
        _sync_course_faculty_from_instructor(row, row.instructor)
    elif not row.instructor:
        row.instructor = '; '.join(f.name for f in row.faculty) or None

    db.session.add(row)
    db.session.commit()
    return jsonify({'success': True, 'course': _serialize_local_course(row)})


@admin_bp.route('/api/courses/<path:course_ref>', methods=['PUT', 'DELETE'])
@login_required
@admin_required
def api_course_item(course_ref: str):
    parsed = _parse_course_ref(course_ref)
    if not parsed:
        return jsonify({'success': False, 'message': 'Invalid course reference.'}), 400
    source, row_id = parsed

    if request.method == 'DELETE':
        if source != 'manual':
            return jsonify({'success': False, 'message': 'Only manual courses can be deleted.'}), 400
        row = Course.query.get(row_id)
        if not row:
            return jsonify({'success': False, 'message': 'Course not found.'}), 404
        db.session.delete(row)
        db.session.commit()
        return jsonify({'success': True})

    payload = request.get_json(silent=True) or {}
    if source == 'manual':
        row = Course.query.get(row_id)
        if not row:
            return jsonify({'success': False, 'message': 'Course not found.'}), 404
        editing_ref = f'manual:{row.id}'
    elif source == 'csv':
        csv_row = Course.query.get(row_id)
        if not csv_row:
            return jsonify({'success': False, 'message': 'Course not found.'}), 404
        row = _manual_course_from_local(csv_row)
        row.faculty = list(csv_row.faculty or [])
        editing_ref = f'csv:{csv_row.id}'
    else:
        scraped = ScrapedCourse.query.get(row_id)
        if not scraped:
            return jsonify({'success': False, 'message': 'Course not found.'}), 404
        row = _manual_course_from_scraped(scraped)
        editing_ref = f'scraped:{scraped.id}'

    merged = {
        'course_number': str(payload.get('course_number') or row.course_number or '').strip(),
        'title': str(payload.get('title') or row.title or '').strip(),
        'credits': payload.get('credits') if payload.get('credits') is not None else row.credits,
        'day_period': str(payload.get('day_period') or row.day_period or '').strip(),
        'semester': str(payload.get('semester') or row.semester or '').strip(),
        'year': str(payload.get('year') or row.year or '').strip(),
        'prerequisite': str(payload.get('prerequisite') or row.prerequisite or '').strip(),
        'subject_area': str(payload.get('subject_area') or row.subject_area or '').strip(),
        'instructor': str(payload.get('instructor') or row.instructor or '').strip(),
    }

    ok, err = _validate_course_payload(merged, exclude_ref=editing_ref)
    if not ok:
        return jsonify({'success': False, 'message': err}), 400

    row.course_number = merged['course_number']
    row.title = merged['title']
    row.credits = _safe_float(merged['credits'], 0.0)
    row.day_period = merged['day_period']
    row.semester = merged['semester'] or None
    row.year = merged['year'] or None
    row.prerequisite = merged['prerequisite'] or None
    row.subject_area = merged['subject_area'] or None
    row.instructor = merged['instructor'] or None
    row.source = 'manual'

    faculty_ids = payload.get('faculty_ids')
    if isinstance(faculty_ids, list):
        row.faculty = Faculty.query.filter(Faculty.id.in_(faculty_ids)).all() if faculty_ids else []
        if row.faculty and not row.instructor:
            row.instructor = '; '.join(f.name for f in row.faculty) or None
    if not isinstance(faculty_ids, list):
        _sync_course_faculty_from_instructor(row, row.instructor)

    if source != 'manual':
        db.session.add(row)
    db.session.commit()
    return jsonify({'success': True, 'course': _serialize_local_course(row)})


@admin_bp.route('/api/courses/<path:course_ref>/duplicate', methods=['POST'])
@login_required
@admin_required
def api_course_duplicate(course_ref: str):
    parsed = _parse_course_ref(course_ref)
    if not parsed:
        return jsonify({'success': False, 'message': 'Invalid course reference.'}), 400
    source, row_id = parsed
    if source in {'manual', 'csv'}:
        row = Course.query.get(row_id)
        if not row:
            return jsonify({'success': False, 'message': 'Course not found.'}), 404
        payload = _serialize_local_course(row)
    else:
        row = ScrapedCourse.query.get(row_id)
        if not row:
            return jsonify({'success': False, 'message': 'Course not found.'}), 404
        payload = _serialize_scraped_course(row)

    duplicate_payload = {
        'course_number': payload.get('course_number'),
        'title': payload.get('title'),
        'credits': payload.get('credits'),
        'day_period': payload.get('day_period'),
        'semester': payload.get('semester'),
        'year': payload.get('year'),
        'prerequisite': payload.get('prerequisite'),
        'subject_area': payload.get('subject_area'),
        'instructor': payload.get('instructor'),
        'faculty_ids': payload.get('faculty_ids') or [],
    }
    return jsonify({'success': True, 'course': duplicate_payload})


@admin_bp.route('/api/faculty', methods=['GET', 'POST'])
@login_required
@admin_required
def api_faculty():
    if request.method == 'GET':
        rows = Faculty.query.order_by(Faculty.name.asc()).all()
        courses = _merged_courses_for_admin()
        areas = SubjectArea.query.order_by(SubjectArea.name.asc()).all()
        return jsonify(
            {
                'success': True,
                'faculty': [_serialize_faculty(r) for r in rows],
                'courses': courses,
                'advisor_areas': [{'id': a.id, 'name': a.name} for a in areas],
            }
        )

    payload = request.get_json(silent=True) or {}
    name = str(payload.get('name') or '').strip()
    if not name:
        return jsonify({'success': False, 'message': 'Faculty name is required.'}), 400

    row = Faculty(name=name, email=str(payload.get('email') or '').strip() or None)

    course_refs = payload.get('course_refs') or []
    advisor_area_ids = payload.get('advisor_area_ids') or []
    if isinstance(course_refs, list):
        manual_courses: list[Course] = []
        for ref in course_refs:
            course_obj = _resolve_manual_course_ref(str(ref))
            if course_obj is None:
                continue
            if course_obj.id is None:
                db.session.add(course_obj)
                db.session.flush()
            manual_courses.append(course_obj)
        row.courses = manual_courses

    if isinstance(advisor_area_ids, list):
        row.advisor_areas = SubjectArea.query.filter(SubjectArea.id.in_(advisor_area_ids)).all() if advisor_area_ids else []

    db.session.add(row)
    db.session.commit()
    return jsonify({'success': True, 'faculty': _serialize_faculty(row)})


@admin_bp.route('/api/faculty/<int:faculty_id>', methods=['PUT', 'DELETE'])
@login_required
@admin_required
def api_faculty_item(faculty_id: int):
    row = Faculty.query.get(faculty_id)
    if not row:
        return jsonify({'success': False, 'message': 'Faculty not found.'}), 404

    if request.method == 'DELETE':
        db.session.delete(row)
        db.session.commit()
        return jsonify({'success': True})

    payload = request.get_json(silent=True) or {}
    name = str(payload.get('name') or '').strip()
    if not name:
        return jsonify({'success': False, 'message': 'Faculty name is required.'}), 400
    row.name = name
    row.email = str(payload.get('email') or '').strip() or None

    course_refs = payload.get('course_refs') or []
    advisor_area_ids = payload.get('advisor_area_ids') or []
    if isinstance(course_refs, list):
        manual_courses: list[Course] = []
        for ref in course_refs:
            course_obj = _resolve_manual_course_ref(str(ref))
            if course_obj is None:
                continue
            if course_obj.id is None:
                db.session.add(course_obj)
                db.session.flush()
            manual_courses.append(course_obj)
        row.courses = manual_courses

    if isinstance(advisor_area_ids, list):
        row.advisor_areas = SubjectArea.query.filter(SubjectArea.id.in_(advisor_area_ids)).all() if advisor_area_ids else []

    db.session.commit()
    return jsonify({'success': True, 'faculty': _serialize_faculty(row)})


# ─────────────────────────────────────────────────────────────────────────────
#  University Connection — Scraping Control System
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/university_connection', methods=['GET'])
@login_required
@admin_required
def university_connection():
    from services.scraper_service import (
        scraper_progress,
        _is_chrome_running,
        is_scraper_running,
        is_scraper_paused,
        scraper_state_label,
    )
    import json as _json

    year = get_app_setting('current_year', '')
    semester = get_app_setting('current_semester', '')
    debugger_address = get_app_setting('scraper_debugger_address', os.environ.get('CHROME_DEBUGGER_ADDRESS', 'localhost:9222'))
    row_transition_delay = _safe_float(get_app_setting('scraper_row_transition_delay', '0.0'), 0.0)
    popup_open_timeout = _safe_float(get_app_setting('scraper_popup_open_timeout', '10'), 10.0)
    popup_close_delay = _safe_float(get_app_setting('scraper_popup_close_delay', '0.35'), 0.35)
    _ensure_dataset_registry_synced()
    active_dataset_name = _resolve_effective_active_dataset_name()

    # Past datasets for history panel
    past_datasets = ScrapedDataset.query.order_by(ScrapedDataset.created_at.desc()).limit(10).all()
    latest_scrape_run = ScrapedDataset.query.order_by(ScrapedDataset.created_at.desc()).first()
    latest_csv_upload = Course.query.filter(sa.func.lower(sa.func.coalesce(Course.source, '')) == 'csv').order_by(Course.last_updated.desc(), Course.id.desc()).first()

    selectable_dataset_names = _all_selectable_dataset_names()

    chrome_connected = _is_chrome_running(debugger_address)
    return render_template(
        'Admin/university_connection.html',
        year=year,
        semester=semester,
        debugger_address=debugger_address,
        chrome_connected=chrome_connected,
        past_datasets=past_datasets,
        progress=dict(scraper_progress),
        probed_fields=session.get('scraper_probed_fields', []),
        selected_fields=session.get('scraper_selected_fields', []),
        scraper_is_running=is_scraper_running(),
        scraper_is_paused=is_scraper_paused(),
        scraper_state=scraper_state_label(),
        row_transition_delay=row_transition_delay,
        popup_open_timeout=popup_open_timeout,
        popup_close_delay=popup_close_delay,
        latest_scrape_run=latest_scrape_run,
        latest_csv_upload=latest_csv_upload,
        active_dataset_name=active_dataset_name,
        selectable_dataset_names=selectable_dataset_names,
    )


# ── POST /admin/start-session ─────────────────────────────────────────────
@admin_bp.route('/start-session', methods=['POST'])
@login_required
@admin_required
def start_browser_session():
    from services.scraper_service import start_browser_session as _start, _set_last_backend_error
    try:
        data = request.get_json(force=True, silent=True) or {}
        debugger_address = (data.get('debugger_address') or get_app_setting('scraper_debugger_address', os.environ.get('CHROME_DEBUGGER_ADDRESS', 'localhost:9222'))).strip()
        if debugger_address:
            update_app_setting('scraper_debugger_address', debugger_address)
        result = _start(debugger_address=debugger_address)
        return jsonify(result)
    except Exception as exc:
        _set_last_backend_error('admin.start_browser_session', exc)
        return jsonify({'success': False, 'message': f'Start session failed: {exc}'}), 500


# ── GET /admin/check-login ────────────────────────────────────────────────
@admin_bp.route('/check-login', methods=['GET'])
@login_required
@admin_required
def check_login_status():
    from services.scraper_service import get_driver, check_login_status as _check, _set_last_backend_error
    try:
        debugger_address = get_app_setting('scraper_debugger_address', os.environ.get('CHROME_DEBUGGER_ADDRESS', 'localhost:9222'))
        driver = get_driver(debugger_address=debugger_address)
    except Exception as exc:
        _set_last_backend_error('admin.check_login_status.get_driver', exc)
        return jsonify({'logged_in': False, 'message': str(exc)})
    try:
        result = _check(driver)
        return jsonify(result)
    except Exception as exc:
        _set_last_backend_error('admin.check_login_status', exc)
        return jsonify({'logged_in': False, 'message': f'Login check failed: {exc}'}), 500


# ── POST /admin/probe-fields ──────────────────────────────────────────────
@admin_bp.route('/probe-fields', methods=['POST'])
@login_required
@admin_required
def probe_fields():
    from services.scraper_service import start_probe_job as _start_probe, _set_last_backend_error
    try:
        data = request.get_json(force=True, silent=True) or {}
        debugger_address = (data.get('debugger_address') or get_app_setting('scraper_debugger_address', os.environ.get('CHROME_DEBUGGER_ADDRESS', 'localhost:9222'))).strip()
        popup_open_timeout = _safe_float(get_app_setting('scraper_popup_open_timeout', '10'), 10.0)
        popup_close_delay = _safe_float(get_app_setting('scraper_popup_close_delay', '0.35'), 0.35)

        if debugger_address:
            update_app_setting('scraper_debugger_address', debugger_address)
    except Exception as exc:
        _set_last_backend_error('admin.probe_fields.payload', exc)
        return jsonify({'success': False, 'fields': [], 'message': str(exc)})

    try:
        result = _start_probe(
            debugger_address=debugger_address,
            popup_open_timeout=popup_open_timeout,
            popup_close_delay=popup_close_delay,
        )
        return jsonify(result)
    except Exception as exc:
        _set_last_backend_error('admin.probe_fields', exc)
        return jsonify({'success': False, 'fields': [], 'message': f'Probe failed: {exc}'}), 500


@admin_bp.route('/probe-status', methods=['GET'])
@login_required
@admin_required
def probe_status():
    from services.scraper_service import get_probe_progress as _get_probe_progress, _set_last_backend_error
    try:
        payload = dict(_get_probe_progress())
        status = str(payload.get('status') or 'idle')

        if status == 'completed' and payload.get('fields'):
            safe_fields = _sanitize_session_fields(payload.get('fields') or [])
            session['scraper_probed_fields'] = safe_fields
            payload['fields'] = safe_fields

        return jsonify({'success': True, **payload})
    except Exception as exc:
        _set_last_backend_error('admin.probe_status', exc)
        return jsonify({'success': False, 'status': 'failed', 'fields': [], 'message': f'Probe status failed: {exc}'}), 500


# ── POST /admin/start-scraping ────────────────────────────────────────────
@admin_bp.route('/start-scraping', methods=['POST'])
@login_required
@admin_required
def start_scraping():
    import json as _json
    from flask import current_app
    from services.scraper_service import run_controlled_scraper as _run, scraper_progress, is_scraper_running

    # Reject if already running
    if scraper_progress.get('status') == 'running' or is_scraper_running():
        return jsonify({'success': False, 'message': 'A scraping job is already running.'})

    try:
        data = request.get_json(force=True, silent=True) or {}
    except Exception as exc:
        return jsonify({'success': False, 'message': f'Invalid request payload: {exc}'}), 400
    selected_fields = data.get('selected_fields') or []
    if isinstance(selected_fields, tuple):
        selected_fields = list(selected_fields)
    if not isinstance(selected_fields, list):
        selected_fields = []
    selected_fields = [str(f).strip() for f in selected_fields if str(f).strip()]
    year = (data.get('year') or get_app_setting('current_year', '')).strip()
    semester = (data.get('semester') or get_app_setting('current_semester', '')).strip()
    debugger_address = (data.get('debugger_address') or get_app_setting('scraper_debugger_address', os.environ.get('CHROME_DEBUGGER_ADDRESS', 'localhost:9222'))).strip()
    popup_open_timeout = _safe_float(get_app_setting('scraper_popup_open_timeout', '10'), 10.0)
    popup_close_delay = _safe_float(get_app_setting('scraper_popup_close_delay', '0.35'), 0.35)
    row_transition_delay = _safe_float(data.get('row_transition_delay', get_app_setting('scraper_row_transition_delay', '0.0')), 0.0)
    if row_transition_delay < 0.0:
        row_transition_delay = 0.0
    if row_transition_delay > 15.0:
        row_transition_delay = 15.0

    if not selected_fields:
        return jsonify({'success': False, 'message': 'No fields selected.'})
    if not year or not semester:
        return jsonify({'success': False, 'message': 'Year and Semester must be configured in Settings.'})

    try:
        session['scraper_selected_fields'] = _sanitize_session_fields(selected_fields)
        if debugger_address:
            update_app_setting('scraper_debugger_address', debugger_address)
        update_app_setting('scraper_row_transition_delay', str(row_transition_delay))
        flask_app = current_app._get_current_object()
        result = _run(
            selected_fields,
            year,
            semester,
            flask_app,
            debugger_address=debugger_address,
            popup_open_timeout=popup_open_timeout,
            popup_close_delay=popup_close_delay,
            inter_row_delay=row_transition_delay,
        )
        return jsonify(result)
    except Exception as exc:
        return jsonify({'success': False, 'message': f'Start scraping failed: {exc}'}), 500


@admin_bp.route('/pause-scraping', methods=['POST'])
@login_required
@admin_required
def pause_scraping():
    from services.scraper_service import pause_scraper as _pause
    try:
        payload = _pause()
        return jsonify(payload), (200 if payload.get('success') else 409)
    except Exception as exc:
        return jsonify({'success': False, 'message': f'Pause failed: {exc}', 'status': 'failed'}), 500


@admin_bp.route('/resume-scraping', methods=['POST'])
@login_required
@admin_required
def resume_scraping():
    from services.scraper_service import resume_scraper as _resume
    try:
        payload = _resume()
        return jsonify(payload), (200 if payload.get('success') else 409)
    except Exception as exc:
        return jsonify({'success': False, 'message': f'Resume failed: {exc}', 'status': 'failed'}), 500


@admin_bp.route('/stop-scraping', methods=['POST'])
@login_required
@admin_required
def stop_scraping():
    from services.scraper_service import request_stop_scraper as _stop
    try:
        payload = _stop()
        return jsonify(payload), (200 if payload.get('success') else 409)
    except Exception as exc:
        return jsonify({'success': False, 'message': f'Stop failed: {exc}', 'status': 'failed'}), 500


@admin_bp.route('/api/scraper-config', methods=['GET', 'POST'])
@login_required
@admin_required
def scraper_config_api():
    from services.scraper_service import update_runtime_config as _update_runtime_config, get_runtime_config as _get_runtime_config

    if request.method == 'GET':
        cfg = {
            'current_year': get_app_setting('current_year', ''),
            'current_semester': get_app_setting('current_semester', ''),
            'scraper_debugger_address': get_app_setting('scraper_debugger_address', os.environ.get('CHROME_DEBUGGER_ADDRESS', 'localhost:9222')),
            'scraper_popup_open_timeout': _safe_float(get_app_setting('scraper_popup_open_timeout', '10'), 10.0),
            'scraper_popup_close_delay': _safe_float(get_app_setting('scraper_popup_close_delay', '0.35'), 0.35),
            'scraper_row_transition_delay': _safe_float(get_app_setting('scraper_row_transition_delay', '0.0'), 0.0),
            'runtime': _get_runtime_config(),
        }
        return jsonify({'success': True, 'config': cfg})

    data = request.get_json(silent=True) or {}

    year = str(data.get('year') or '').strip()
    semester = str(data.get('semester') or '').strip()
    debugger_address = str(data.get('debugger_address') or '').strip()
    popup_open_timeout = _safe_float(data.get('popup_open_timeout', get_app_setting('scraper_popup_open_timeout', '10')), 10.0)
    popup_close_delay = _safe_float(data.get('popup_close_delay', get_app_setting('scraper_popup_close_delay', '0.35')), 0.35)
    row_transition_delay = _safe_float(data.get('row_transition_delay', get_app_setting('scraper_row_transition_delay', '0.0')), 0.0)

    popup_open_timeout = max(2.0, min(popup_open_timeout, 40.0))
    popup_close_delay = max(0.0, min(popup_close_delay, 5.0))
    row_transition_delay = max(0.0, min(row_transition_delay, 15.0))

    if year:
        update_app_setting('current_year', year)
    if semester:
        update_app_setting('current_semester', semester)
    if debugger_address:
        update_app_setting('scraper_debugger_address', debugger_address)

    update_app_setting('scraper_popup_open_timeout', str(popup_open_timeout))
    update_app_setting('scraper_popup_close_delay', str(popup_close_delay))
    update_app_setting('scraper_row_transition_delay', str(row_transition_delay))

    runtime_cfg = _update_runtime_config(
        popup_open_timeout=popup_open_timeout,
        popup_close_delay=popup_close_delay,
        inter_row_delay=row_transition_delay,
    )

    return jsonify(
        {
            'success': True,
            'message': 'Scraper configuration updated.',
            'config': {
                'current_year': get_app_setting('current_year', ''),
                'current_semester': get_app_setting('current_semester', ''),
                'scraper_debugger_address': get_app_setting('scraper_debugger_address', os.environ.get('CHROME_DEBUGGER_ADDRESS', 'localhost:9222')),
                'scraper_popup_open_timeout': popup_open_timeout,
                'scraper_popup_close_delay': popup_close_delay,
                'scraper_row_transition_delay': row_transition_delay,
                'runtime': runtime_cfg,
            },
        }
    )


# ── GET /admin/scraper-status ─────────────────────────────────────────────
@admin_bp.route('/scraper-status', methods=['GET'])
@login_required
@admin_required
def scraper_status():
    try:
        from services.scraper_service import (
            scraper_progress as _p,
            _is_chrome_running,
            is_scraper_running,
            is_scraper_paused,
            scraper_state_label,
            get_runtime_config,
        )
        import copy
        debugger_address = get_app_setting('scraper_debugger_address', os.environ.get('CHROME_DEBUGGER_ADDRESS', 'localhost:9222'))
        payload = copy.deepcopy(_p)
        payload['chrome_connected'] = _is_chrome_running(debugger_address)
        payload['debugger_address'] = debugger_address
        payload['is_running_lock'] = is_scraper_running()
        payload['is_paused'] = is_scraper_paused()
        payload['state'] = scraper_state_label()
        payload['runtime_config'] = get_runtime_config()

        # Enrich with DB data when a dataset is active
        ds_id = payload.get('dataset_id')
        if ds_id:
            ds = db.session.get(ScrapedDataset, ds_id)
            if ds:
                payload['db_status'] = ds.status
                payload['db_current'] = ds.current_index
                payload['db_total'] = ds.total_courses
                payload['db_last_course'] = ds.last_course

        return jsonify(payload)
    except Exception as exc:
        return jsonify({'success': False, 'message': f'Status failed: {exc}'}), 500


@admin_bp.route('/scraper-diagnostics', methods=['GET'])
@login_required
@admin_required
def scraper_diagnostics():
    from services.scraper_service import get_scraper_diagnostics as _diag
    debugger_address = (request.args.get('debugger_address') or get_app_setting('scraper_debugger_address', os.environ.get('CHROME_DEBUGGER_ADDRESS', 'localhost:9222'))).strip()
    try:
        payload = _diag(debugger_address=debugger_address)
        return jsonify({'success': True, 'diagnostics': payload})
    except Exception as exc:
        return jsonify({'success': False, 'message': f'Diagnostics failed: {exc}'}), 500


@admin_bp.route('/api/scraper-diagnostics', methods=['GET'])
@login_required
@admin_required
def scraper_diagnostics_api():
    return scraper_diagnostics()


@admin_bp.route('/health', methods=['GET'])
@login_required
@admin_required
def admin_health():
    return render_template(
        'Admin/admin_health.html',
        app_version=str(current_app.config.get('APP_VERSION') or 'unknown'),
        app_started_at=current_app.config.get('APP_STARTED_AT'),
        recent_versions=_recent_git_versions(limit=10),
    )


@admin_bp.route('/api/health-metrics', methods=['GET'])
@login_required
@admin_required
def health_metrics_api():
    from services.scraper_service import (
        _is_chrome_running,
        _debugger_http_health,
        scraper_progress,
        is_scraper_running,
        is_scraper_paused,
        scraper_state_label,
    )

    now_utc = datetime.now(timezone.utc)

    db_ok = True
    db_error = None
    try:
        db.session.execute(text('SELECT 1'))
    except Exception as exc:
        db_ok = False
        db_error = str(exc)
        db.session.rollback()

    debugger_address = get_app_setting('scraper_debugger_address', os.environ.get('CHROME_DEBUGGER_ADDRESS', 'localhost:9222'))
    dbg_http_ok, dbg_http_msg, dbg_http_addr = _debugger_http_health(debugger_address)

    app_started_at = current_app.config.get('APP_STARTED_AT')
    uptime_seconds = None
    try:
        if app_started_at:
            started = datetime.fromisoformat(str(app_started_at).replace('Z', '+00:00'))
            uptime_seconds = int(max(0, (now_utc - started).total_seconds()))
    except Exception:
        uptime_seconds = None

    payload = {
        'success': True,
        'timestamp': now_utc.isoformat(),
        'version': str(current_app.config.get('APP_VERSION') or 'unknown'),
        'core': {
            'database_ok': db_ok,
            'database_error': db_error,
            'uptime_seconds': uptime_seconds,
            'app_started_at': app_started_at,
            'active_sessions': _active_session_count(),
            'process': _process_metrics(),
        },
        'scraper': {
            'state': scraper_state_label(),
            'is_running': is_scraper_running(),
            'is_paused': is_scraper_paused(),
            'progress': {
                'status': scraper_progress.get('status'),
                'dataset_id': scraper_progress.get('dataset_id'),
                'current_index': scraper_progress.get('current_index'),
                'total_courses': scraper_progress.get('total_courses'),
                'last_course': scraper_progress.get('last_course'),
                'error': scraper_progress.get('error'),
            },
            'remote_browser_connected': _is_chrome_running(debugger_address),
            'devtools_http_ok': dbg_http_ok,
            'devtools_http_message': dbg_http_msg,
            'devtools_working_address': dbg_http_addr,
            'debugger_address': debugger_address,
        },
        'background': {
            'thread_count': len(threading.enumerate()),
            'thread_names': [t.name for t in threading.enumerate()][:80],
        },
        'version_control': {
            'recent_versions': _recent_git_versions(limit=15),
            'rollback_supported': False,
        },
        'integrity': _build_integrity_payload(),
    }
    return jsonify(payload)


@admin_bp.route('/api/health-fix', methods=['POST'])
@login_required
@admin_required
def health_fix_api():
    data = request.get_json(silent=True) or {}
    action = str(data.get('action') or '').strip().lower()

    if not action:
        return jsonify({'success': False, 'message': 'Action is required.'}), 400

    try:
        if action == 'set_active_dataset_auto':
            names = _all_selectable_dataset_names()
            if not names:
                return jsonify({'success': False, 'message': 'No datasets available to set active.'}), 400
            chosen = names[0]
            _set_active_registry_dataset(chosen)
            return jsonify({'success': True, 'message': f'Active dataset set to {chosen}.', 'action': action, 'active_dataset_name': chosen})

        if action == 'link_faculty_from_instructors':
            updated_courses = 0
            rows = Course.query.filter(
                ~Course.faculty.any(),
                Course.instructor.isnot(None),
                sa.func.length(sa.func.trim(Course.instructor)) > 0,
            ).all()
            for row in rows:
                before = len(row.faculty or [])
                _sync_course_faculty_from_instructor(row, row.instructor)
                after = len(row.faculty or [])
                if before == 0 and after > 0:
                    updated_courses += 1
            db.session.commit()
            return jsonify({'success': True, 'message': f'Linked faculty for {updated_courses} courses.', 'action': action, 'updated_courses': updated_courses})

        if action == 'remove_orphan_faculty':
            orphan_rows = Faculty.query.filter(~Faculty.courses.any()).all()
            removed = len(orphan_rows)
            for row in orphan_rows:
                db.session.delete(row)
            db.session.commit()
            return jsonify({'success': True, 'message': f'Removed {removed} orphan faculty records.', 'action': action, 'removed': removed})

        if action == 'backfill_catalog_status_dataset':
            active_name = _resolve_effective_active_dataset_name()
            fallback_names = _all_selectable_dataset_names()
            default_name = active_name or (fallback_names[0] if fallback_names else '')
            if not default_name:
                return jsonify({'success': False, 'message': 'No dataset label available for backfill.'}), 400

            rows = CatalogStatus.query.filter(
                CatalogStatus.status == 'available',
                sa.or_(CatalogStatus.dataset.is_(None), sa.func.length(sa.func.trim(CatalogStatus.dataset)) == 0),
            ).all()
            updated = 0
            for row in rows:
                row.dataset = default_name
                updated += 1
            db.session.commit()
            return jsonify({'success': True, 'message': f'Backfilled {updated} catalog status rows.', 'action': action, 'updated': updated})

    except Exception as exc:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Fix action failed: {exc}', 'action': action}), 500

    return jsonify({'success': False, 'message': 'Unknown fix action.', 'action': action}), 400


@admin_bp.route('/api/system/restore', methods=['POST'])
@login_required
@admin_required
def restore_version_placeholder():
    data = request.get_json(silent=True) or {}
    target_sha = str(data.get('sha') or '').strip()
    return jsonify(
        {
            'success': False,
            'message': 'Restore is environment-specific and currently disabled in-app. Use deployment pipeline rollback.',
            'target_sha': target_sha or None,
        }
    ), 501

@admin_bp.route('/api/update_course', methods=['POST'])
@login_required
@admin_required
def api_update_course():
    data = request.get_json()
    try:
        course_id = data.get('_rowid')
        try:
            course_id = int(course_id)
        except Exception:
            return jsonify({'success': False, 'message': 'Invalid course identifier'})

        row = db.session.get(ScrapedCourse, course_id)
        if not row:
            return jsonify({'success': False, 'message': 'Course not found'})

        row.course_title = (data.get('Course_Title') or '').strip() or row.course_title
        try:
            row.credits = float(data.get('Credits'))
        except Exception:
            pass

        payload = safe_json_loads(row.data_json, {})
        if isinstance(payload, dict):
            for key in list(payload.keys()):
                key_norm = ' '.join(str(key or '').strip().lower().replace('_', ' ').replace('-', ' ').split())
                if key_norm in {'course title', 'subject'}:
                    payload[key] = row.course_title
                if key_norm in {'credits', 'credit'} and row.credits is not None:
                    payload[key] = str(row.credits)
            row.data_json = safe_json_dumps(payload)

        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': 'Unable to update course.'}), 500

@admin_bp.route('/api/update_period', methods=['POST'])
@login_required
@admin_required
def update_period():
    data = request.get_json()
    try:
        period = Period.query.get(data['id'])
        if period:
            period.start_time = data.get('start_time')
            period.end_time = data.get('end_time')
            db.session.commit()
            return jsonify({'success': True})
        return jsonify({'success': False, 'message': 'Period not found'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': 'Unable to update period.'}), 500


@admin_bp.route('/api/periods/create', methods=['POST'])
@login_required
@admin_required
def api_create_period():
    data = request.get_json(silent=True) or {}
    name = str(data.get('name') or '').strip()
    start_time = str(data.get('start_time') or '').strip()
    duration_minutes = data.get('duration_minutes', 75)

    if not name or not name.isdigit():
        return jsonify({'success': False, 'message': 'Period name must be a number (e.g., 1, 2, 3).'}), 400
    if Period.query.filter_by(name=name).first():
        return jsonify({'success': False, 'message': f'Period {name} already exists.'}), 400

    start_mins = _parse_hhmm_to_minutes(start_time)
    try:
        duration_int = int(duration_minutes)
    except Exception:
        duration_int = 75

    if start_mins is None:
        return jsonify({'success': False, 'message': 'Invalid start time.'}), 400
    if duration_int <= 0 or duration_int > 24 * 60:
        return jsonify({'success': False, 'message': 'Invalid duration.'}), 400

    end_time = _minutes_to_hhmm(start_mins + duration_int)

    period = Period(name=name, start_time=start_time, end_time=end_time)
    try:
        db.session.add(period)
        db.session.commit()
        return jsonify({'success': True, 'period': {'id': period.id, 'name': period.name, 'start_time': period.start_time, 'end_time': period.end_time}})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': 'Unable to create period.'}), 500


@admin_bp.route('/api/periods/<int:period_id>/update', methods=['POST'])
@login_required
@admin_required
def api_update_period_full(period_id: int):
    data = request.get_json(silent=True) or {}
    name = str(data.get('name') or '').strip()
    start_time = str(data.get('start_time') or '').strip()
    duration_minutes = data.get('duration_minutes', 75)

    period = Period.query.get(period_id)
    if not period:
        return jsonify({'success': False, 'message': 'Period not found'}), 404

    if not name or not name.isdigit():
        return jsonify({'success': False, 'message': 'Period name must be a number (e.g., 1, 2, 3).'}), 400

    other = Period.query.filter(Period.name == name, Period.id != period_id).first()
    if other:
        return jsonify({'success': False, 'message': f'Period {name} already exists.'}), 400

    start_mins = _parse_hhmm_to_minutes(start_time)
    try:
        duration_int = int(duration_minutes)
    except Exception:
        duration_int = 75

    if start_mins is None:
        return jsonify({'success': False, 'message': 'Invalid start time.'}), 400
    if duration_int <= 0 or duration_int > 24 * 60:
        return jsonify({'success': False, 'message': 'Invalid duration.'}), 400

    period.name = name
    period.start_time = start_time
    period.end_time = _minutes_to_hhmm(start_mins + duration_int)

    try:
        db.session.commit()
        return jsonify({'success': True, 'period': {'id': period.id, 'name': period.name, 'start_time': period.start_time, 'end_time': period.end_time}})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': 'Unable to update period.'}), 500


@admin_bp.route('/api/periods/<int:period_id>/delete', methods=['POST'])
@login_required
@admin_required
def api_delete_period(period_id: int):
    period = Period.query.get(period_id)
    if not period:
        return jsonify({'success': False, 'message': 'Period not found'}), 404
    try:
        db.session.delete(period)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': 'Unable to delete period.'}), 500


@admin_bp.route('/api/lunch_settings', methods=['POST'])
@login_required
@admin_required
def api_update_lunch_settings():
    data = request.get_json(silent=True) or {}
    lunch_start_time = str(data.get('lunch_start_time') or '').strip()
    duration_minutes = data.get('lunch_duration_minutes', 60)

    start_mins = _parse_hhmm_to_minutes(lunch_start_time)
    try:
        duration_int = int(duration_minutes)
    except Exception:
        duration_int = 60

    if start_mins is None:
        return jsonify({'success': False, 'message': 'Invalid lunch start time.'}), 400
    if duration_int <= 0 or duration_int > 24 * 60:
        return jsonify({'success': False, 'message': 'Invalid lunch duration.'}), 400

    update_app_setting('lunch_start_time', lunch_start_time)
    update_app_setting('lunch_duration_minutes', str(duration_int))
    return jsonify({'success': True})

@admin_bp.route('/analytics')
@login_required
@admin_required
def analytics():
    selected = {
        'year': (request.args.get('year') or '').strip(),
        'semester': (request.args.get('semester') or '').strip(),
        'major': (request.args.get('major') or '').strip(),
        'minor': (request.args.get('minor') or '').strip(),
        'gender': (request.args.get('gender') or '').strip().lower(),
        'year_of_study': (request.args.get('year_of_study') or '').strip(),
    }
    payload = _build_admin_analytics_payload(selected)
    return render_template('Admin/admin_analytics.html', selected=selected, payload=payload)


def _normalize_gender_bucket(value: str) -> str:
    raw = str(value or '').strip().lower()
    if raw in {'m', 'male', 'man', 'boy'}:
        return 'male'
    if raw in {'f', 'female', 'woman', 'girl'}:
        return 'female'
    if raw in {'prefer not to say', 'prefer_not_to_say', 'prefer-not-to-say', 'none', 'n/a'}:
        return 'prefer_not_to_say'
    if not raw:
        return 'unspecified'
    return raw.replace(' ', '_')


def _year_of_study_label(expected_graduation) -> str:
    try:
        value = int(expected_graduation)
    except Exception:
        value = None
    mapping = {1: '1st Year', 2: '2nd Year', 3: '3rd Year', 4: '4th Year'}
    if value in mapping:
        return mapping[value]
    if value is None:
        return 'Unspecified'
    return str(value)


def _round_metric(value: float, digits: int = 2):
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except Exception:
        return None


def _mean(values):
    vals = [float(v) for v in values if v is not None]
    return (sum(vals) / len(vals)) if vals else None


def _group_value(values_by_group: dict, group: str):
    vals = values_by_group.get(group, [])
    return _round_metric(_mean(vals), 2)


def _load_analytics_metric_overrides() -> dict:
    raw = get_app_setting('analytics_metric_overrides_json', '{}')
    try:
        parsed = json.loads(raw or '{}')
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _apply_payload_override(payload: dict, path: str, value):
    if not path or not isinstance(path, str):
        return
    parts = [p for p in path.split('.') if p]
    if not parts:
        return

    cursor = payload
    for key in parts[:-1]:
        if isinstance(cursor, dict) and key in cursor:
            cursor = cursor[key]
        else:
            return

    leaf = parts[-1]
    if isinstance(cursor, dict) and leaf in cursor:
        cursor[leaf] = value


def _gpa_bucket_value(bucket: dict):
    credits = float(bucket.get('credits') or 0.0)
    if credits <= 0:
        return None
    return (float(bucket.get('points') or 0.0) / credits)


def _subject_area_options_by_type():
    majors = set()
    minors = set()
    for area in SubjectArea.query.order_by(SubjectArea.name.asc()).all():
        normalized = _normalize_area_type(getattr(area, 'area_type', 'generic'))
        name = (area.name or '').strip()
        if not name:
            continue
        if normalized in {'major', 'both'}:
            majors.add(name)
        if normalized in {'minor', 'both'}:
            minors.add(name)
    return sorted(majors), sorted(minors)


def _build_admin_catalog_index():
    catalog = {}
    rows = _merged_courses_for_admin()
    if not rows:
        return catalog

    try:
        for sc in rows:
            course_number = str(sc.get('course_number') or '').strip()
            if not course_number or course_number in catalog:
                continue

            day = ''
            period = ''
            dp = str(sc.get('day_period') or '').strip()
            if dp:
                try:
                    day_m = re.search(r'(Mon|Tue|Wed|Thu|Fri)', dp, re.I)
                    period_m = re.search(r'\b(\d{1,2})\b', dp)
                    if day_m:
                        day = day_m.group(1).title()[:3]
                    if period_m:
                        period = period_m.group(1)
                except Exception:
                    day = ''
                    period = ''

            catalog[course_number] = {
                'course_title': str(sc.get('title') or '').strip() or course_number,
                'credits': _safe_float(sc.get('credits'), 0.0),
                'instructor': str(sc.get('instructor') or 'TBA').strip() or 'TBA',
                'area_of_study': str(sc.get('subject_area') or '').strip(),
                'day': day,
                'period': period,
                'day_and_period': dp,
            }
    except Exception:
        db.session.rollback()
    return catalog


def _build_admin_analytics_payload(selected: dict):
    selected_year = (selected.get('year') or '').strip()
    selected_semester = (selected.get('semester') or '').strip()
    selected_major = (selected.get('major') or '').strip()
    selected_minor = (selected.get('minor') or '').strip()
    selected_gender = (selected.get('gender') or '').strip().lower()
    selected_year_of_study = (selected.get('year_of_study') or '').strip()

    schedules_q = Schedule.query
    if selected_year:
        try:
            schedules_q = schedules_q.filter(Schedule.year == int(selected_year))
        except Exception:
            pass
    if selected_semester:
        schedules_q = schedules_q.filter(Schedule.semester == selected_semester)

    schedules = schedules_q.all()
    schedule_ids = [s.id for s in schedules]
    schedule_by_id = {s.id: s for s in schedules}
    user_ids = sorted({s.user_id for s in schedules if s.user_id})

    users = []
    if user_ids:
        users = User.query.filter(User.id.in_(user_ids)).all()
    user_by_id = {u.id: u for u in users}

    major_name_by_id = {a.id: a.name for a in SubjectArea.query.all()}

    eligible_user_ids = set(user_ids)
    if selected_major:
        eligible_user_ids = {
            uid for uid in eligible_user_ids
            if (major_name_by_id.get(getattr(user_by_id.get(uid), 'major_subject_area_id', None), '') or '') == selected_major
        }
    if selected_minor:
        eligible_user_ids = {
            uid for uid in eligible_user_ids
            if (major_name_by_id.get(getattr(user_by_id.get(uid), 'minor_subject_area_id', None), '') or '') == selected_minor
        }
    if selected_year_of_study:
        eligible_user_ids = {
            uid for uid in eligible_user_ids
            if _year_of_study_label(getattr(user_by_id.get(uid), 'expected_graduation', None)) == selected_year_of_study
        }
    if selected_gender in {'male', 'female'}:
        eligible_user_ids = {
            uid for uid in eligible_user_ids
            if _normalize_gender_bucket(getattr(user_by_id.get(uid), 'gender', None)) == selected_gender
        }

    schedules = [s for s in schedules if not s.user_id or s.user_id in eligible_user_ids]
    schedule_ids = [s.id for s in schedules]
    schedule_by_id = {s.id: s for s in schedules}

    users = [u for u in users if u.id in eligible_user_ids]
    user_by_id = {u.id: u for u in users}

    years = [r[0] for r in db.session.query(Schedule.year).distinct().order_by(Schedule.year.desc()).all() if r[0] is not None]
    semesters = [r[0] for r in db.session.query(Schedule.semester).distinct().order_by(Schedule.semester.asc()).all() if r[0]]
    majors, minors = _subject_area_options_by_type()
    year_of_study_options = ['1st Year', '2nd Year', '3rd Year', '4th Year']
    gender_options = ['general', 'male', 'female']

    schedule_courses = []
    if schedule_ids:
        try:
            schedule_courses = db.session.execute(
                text(
                    '''SELECT sc.schedule_id, sc.course_number, sc.grade
                       FROM schedule_courses sc
                       WHERE sc.schedule_id IN :schedule_ids'''
                ).bindparams(sa.bindparam('schedule_ids', expanding=True)),
                {'schedule_ids': schedule_ids},
            ).fetchall()
        except Exception:
            db.session.rollback()

    catalog = _build_admin_catalog_index()

    tracked_genders = ['male', 'female', 'prefer_not_to_say']
    all_groups = ['general'] + tracked_genders + ['unspecified']
    group_labels = {
        'general': 'General',
        'male': 'Male',
        'female': 'Female',
        'prefer_not_to_say': 'Prefer not to say',
        'unspecified': 'Unspecified',
    }

    credits_totals = {g: [] for g in all_groups}
    credits_per_semester = {}
    credits_per_year = {}
    credits_total_by_year_of_study = {}

    gpa_total = {g: [] for g in all_groups}
    gpa_by_semester = {}
    gpa_by_year = {}
    gpa_by_major = {}

    grades_by_course = {}
    grades_by_faculty = {}
    grades_by_period_day = {}
    popular_courses_by_major = {}
    popular_courses_by_minor = {}
    preferred_faculty = {}
    electives_by_year = {}
    retake_tracker = {}
    area_credits_by_user = {}
    course_demand_by_term = {}

    user_credit_total = {}
    user_credit_by_semester = {}
    user_credit_by_year = {}

    user_gpa_total = {}
    user_gpa_by_semester = {}
    user_gpa_by_year = {}

    enrollment_count = 0

    for row in schedule_courses:
        sched = schedule_by_id.get(row.schedule_id)
        if not sched or not sched.user_id:
            continue
        user = user_by_id.get(sched.user_id)
        if not user:
            continue

        course_meta = catalog.get(row.course_number, {})
        course_title = (course_meta.get('course_title') or '').strip() or 'Untitled Course'
        course_area = (course_meta.get('area_of_study') or '').strip()
        instructor_name = (course_meta.get('instructor') or 'TBA').strip() or 'TBA'
        major_name = (major_name_by_id.get(getattr(user, 'major_subject_area_id', None), 'Undeclared') or 'Undeclared').strip() or 'Undeclared'
        minor_name = (major_name_by_id.get(getattr(user, 'minor_subject_area_id', None), 'Undeclared') or 'Undeclared').strip() or 'Undeclared'

        enrollment_count += 1

        gender_group = _normalize_gender_bucket(getattr(user, 'gender', None))
        groups = ['general', gender_group if gender_group in all_groups else 'unspecified']

        credits = max(_safe_float(course_meta.get('credits'), 0.0), 0.0)
        semester_key = f"{sched.year} {sched.semester}".strip()
        year_key = str(sched.year)
        year_of_study_label = _year_of_study_label(getattr(user, 'expected_graduation', None))
        course_demand_by_term[(semester_key, course_title)] = int(course_demand_by_term.get((semester_key, course_title), 0)) + 1

        if course_area:
            key = (user.id, course_area)
            area_credits_by_user[key] = float(area_credits_by_user.get(key, 0.0)) + credits

        if course_area and course_area not in {major_name, minor_name}:
            electives_by_year[(year_key, course_title)] = int(electives_by_year.get((year_key, course_title), 0)) + 1

        retake_key = (user.id, course_title)
        tracker = retake_tracker.setdefault(retake_key, {'attempts': 0, 'grades': []})
        tracker['attempts'] = int(tracker.get('attempts', 0)) + 1

        for group in groups:
            user_credit_total.setdefault((group, user.id), 0.0)
            user_credit_total[(group, user.id)] += credits

            user_credit_by_semester.setdefault((group, semester_key, user.id), 0.0)
            user_credit_by_semester[(group, semester_key, user.id)] += credits

            user_credit_by_year.setdefault((group, year_key, user.id), 0.0)
            user_credit_by_year[(group, year_key, user.id)] += credits

            credits_total_by_year_of_study.setdefault((group, year_of_study_label), {})
            credits_total_by_year_of_study[(group, year_of_study_label)].setdefault(user.id, 0.0)
            credits_total_by_year_of_study[(group, year_of_study_label)][user.id] += credits

            popular_courses_by_major[(group, major_name, course_title)] = int(popular_courses_by_major.get((group, major_name, course_title), 0)) + 1
            popular_courses_by_minor[(group, minor_name, course_title)] = int(popular_courses_by_minor.get((group, minor_name, course_title), 0)) + 1

            fac_bucket = preferred_faculty.setdefault((group, instructor_name), {'enrollments': 0, 'points': 0.0, 'graded_count': 0})
            fac_bucket['enrollments'] = int(fac_bucket.get('enrollments', 0)) + 1

        grade = (row.grade or '').strip().upper()
        grade_points = {'A': 4.0, 'B': 3.0, 'C': 2.0, 'D': 1.0, 'F': 0.0}
        points = grade_points.get(grade)
        if points is None or credits <= 0:
            continue

        tracker['grades'].append(points)

        for group in groups:
            user_gpa_total.setdefault((group, user.id), {'points': 0.0, 'credits': 0.0})
            user_gpa_total[(group, user.id)]['points'] += points * credits
            user_gpa_total[(group, user.id)]['credits'] += credits

            user_gpa_by_semester.setdefault((group, semester_key, user.id), {'points': 0.0, 'credits': 0.0})
            user_gpa_by_semester[(group, semester_key, user.id)]['points'] += points * credits
            user_gpa_by_semester[(group, semester_key, user.id)]['credits'] += credits

            user_gpa_by_year.setdefault((group, year_key, user.id), {'points': 0.0, 'credits': 0.0})
            user_gpa_by_year[(group, year_key, user.id)]['points'] += points * credits
            user_gpa_by_year[(group, year_key, user.id)]['credits'] += credits

            gpa_by_major.setdefault((group, major_name), {})
            gpa_by_major[(group, major_name)].setdefault(user.id, {'points': 0.0, 'credits': 0.0})
            gpa_by_major[(group, major_name)][user.id]['points'] += points * credits
            gpa_by_major[(group, major_name)][user.id]['credits'] += credits

            grades_by_course.setdefault((group, course_title), {'points': 0.0, 'count': 0})
            grades_by_course[(group, course_title)]['points'] += points
            grades_by_course[(group, course_title)]['count'] += 1

            grades_by_faculty.setdefault((group, instructor_name), {'points': 0.0, 'count': 0})
            grades_by_faculty[(group, instructor_name)]['points'] += points
            grades_by_faculty[(group, instructor_name)]['count'] += 1

            fac_bucket = preferred_faculty.setdefault((group, instructor_name), {'enrollments': 0, 'points': 0.0, 'graded_count': 0})
            fac_bucket['points'] = float(fac_bucket.get('points', 0.0)) + points
            fac_bucket['graded_count'] = int(fac_bucket.get('graded_count', 0)) + 1

            day = (course_meta.get('day') or '').strip()
            period = (course_meta.get('period') or '').strip()
            period_day_label = f"{day}{period}".strip() if (day or period) else 'Unspecified'
            grades_by_period_day.setdefault((group, period_day_label), {'points': 0.0, 'count': 0})
            grades_by_period_day[(group, period_day_label)]['points'] += points
            grades_by_period_day[(group, period_day_label)]['count'] += 1

    for (group, uid), value in user_credit_total.items():
        credits_totals.setdefault(group, []).append(value)

    for (group, semester_key, uid), value in user_credit_by_semester.items():
        credits_per_semester.setdefault(semester_key, {}).setdefault(group, []).append(value)

    for (group, year_key, uid), value in user_credit_by_year.items():
        credits_per_year.setdefault(year_key, {}).setdefault(group, []).append(value)

    for (group, uid), bucket in user_gpa_total.items():
        gpa_value = _gpa_bucket_value(bucket)
        if gpa_value is not None:
            gpa_total.setdefault(group, []).append(gpa_value)

    for (group, semester_key, uid), bucket in user_gpa_by_semester.items():
        gpa_value = _gpa_bucket_value(bucket)
        if gpa_value is not None:
            gpa_by_semester.setdefault(semester_key, {}).setdefault(group, []).append(gpa_value)

    for (group, year_key, uid), bucket in user_gpa_by_year.items():
        gpa_value = _gpa_bucket_value(bucket)
        if gpa_value is not None:
            gpa_by_year.setdefault(year_key, {}).setdefault(group, []).append(gpa_value)

    credit_series_semester = []
    for label in sorted(credits_per_semester.keys()):
        row = {'label': label}
        for group in all_groups:
            row[group] = _group_value(credits_per_semester.get(label, {}), group)
        credit_series_semester.append(row)

    credit_series_year = []
    for label in sorted(credits_per_year.keys(), key=lambda x: int(x) if str(x).isdigit() else 9999):
        row = {'label': label}
        for group in all_groups:
            row[group] = _group_value(credits_per_year.get(label, {}), group)
        credit_series_year.append(row)

    credit_totals_by_year_study = []
    year_order = ['1st Year', '2nd Year', '3rd Year', '4th Year']
    seen_year_study = set([y for _, y in credits_total_by_year_of_study.keys()])
    ordered_year_study = year_order + sorted([y for y in seen_year_study if y not in set(year_order)])
    for label in ordered_year_study:
        row = {'label': label}
        for group in all_groups:
            user_map = credits_total_by_year_of_study.get((group, label), {})
            row[group] = _round_metric(_mean(list(user_map.values())), 2)
        if any(row.get(g) is not None for g in all_groups):
            credit_totals_by_year_study.append(row)

    gpa_series_semester = []
    for label in sorted(gpa_by_semester.keys()):
        row = {'label': label}
        for group in all_groups:
            row[group] = _group_value(gpa_by_semester.get(label, {}), group)
        gpa_series_semester.append(row)

    gpa_series_year = []
    for label in sorted(gpa_by_year.keys(), key=lambda x: int(x) if str(x).isdigit() else 9999):
        row = {'label': label}
        for group in all_groups:
            row[group] = _group_value(gpa_by_year.get(label, {}), group)
        gpa_series_year.append(row)

    gpa_major_series = []
    major_labels = sorted({label for _, label in gpa_by_major.keys()})
    for label in major_labels:
        row = {'label': label}
        for group in all_groups:
            per_user = gpa_by_major.get((group, label), {})
            values = [_gpa_bucket_value(bucket) for bucket in per_user.values()]
            row[group] = _round_metric(_mean([v for v in values if v is not None]), 2)
        if any(row.get(g) is not None for g in all_groups):
            gpa_major_series.append(row)

    def _build_grade_series(source_map, limit=12):
        labels_by_volume = {}
        for (group, label), aggregate in source_map.items():
            if group != 'general':
                continue
            labels_by_volume[label] = aggregate.get('count', 0)
        top_labels = [label for label, _ in sorted(labels_by_volume.items(), key=lambda x: x[1], reverse=True)[:limit]]
        rows = []
        for label in top_labels:
            row = {'label': label}
            for group in all_groups:
                agg = source_map.get((group, label), {'points': 0.0, 'count': 0})
                count = int(agg.get('count') or 0)
                row[group] = _round_metric((float(agg.get('points') or 0.0) / count), 2) if count > 0 else None
            rows.append(row)
        return rows

    def _build_popular_path_series(source_map, path_index: int, limit=12):
        paths = sorted({k[path_index] for k in source_map.keys() if k[path_index] and k[path_index] != 'Undeclared'})
        rows = []
        for path in paths:
            top_course = None
            top_count = -1
            for (group, path_name, course_title), count in source_map.items():
                if group == 'general' and path_name == path and int(count) > top_count:
                    top_count = int(count)
                    top_course = course_title
            if not top_course:
                continue
            row = {'label': path, 'course_title': top_course}
            for group in ['general', 'male', 'female']:
                row[group] = int(source_map.get((group, path, top_course), 0))
            rows.append(row)
        rows.sort(key=lambda x: x.get('general', 0), reverse=True)
        return rows[:limit]

    preferred_faculty_rows = []
    general_faculty = []
    for (group, faculty), stats in preferred_faculty.items():
        if group != 'general':
            continue
        enrollments = int(stats.get('enrollments') or 0)
        graded_count = int(stats.get('graded_count') or 0)
        avg_grade = (float(stats.get('points') or 0.0) / graded_count) if graded_count > 0 else None
        score = ((avg_grade or 0.0) * 0.7) + (min(4.0, enrollments / 20.0) * 0.3)
        general_faculty.append((faculty, enrollments, _round_metric(avg_grade, 2), _round_metric(score, 2)))
    general_faculty.sort(key=lambda x: (x[3] or 0.0, x[1]), reverse=True)
    for faculty, enrollments, avg_grade, score in general_faculty[:12]:
        preferred_faculty_rows.append({'label': faculty, 'enrollments': enrollments, 'avg_grade': avg_grade, 'score': score})

    electives_rows = []
    elective_years = sorted({k[0] for k in electives_by_year.keys()}, key=lambda x: int(x) if str(x).isdigit() else 9999)
    for year_label in elective_years:
        best_course = None
        best_count = -1
        for (y, title), count in electives_by_year.items():
            if y == year_label and int(count) > best_count:
                best_course = title
                best_count = int(count)
        if best_course:
            electives_rows.append({'label': year_label, 'course_title': best_course, 'general': best_count})

    retake_by_course = {}
    for (_uid, title), tracker in retake_tracker.items():
        attempts = int(tracker.get('attempts') or 0)
        grades = tracker.get('grades') or []
        if attempts <= 1:
            continue
        bucket = retake_by_course.setdefault(title, {'students': 0, 'attempts': 0, 'first': [], 'latest': []})
        bucket['students'] += 1
        bucket['attempts'] += attempts
        if grades:
            bucket['first'].append(float(grades[0]))
            bucket['latest'].append(float(grades[-1]))

    retaken_rows = []
    for title, bucket in retake_by_course.items():
        students = int(bucket.get('students') or 0)
        attempts = int(bucket.get('attempts') or 0)
        retaken_rows.append(
            {
                'label': title,
                'retake_students': students,
                'avg_attempts': _round_metric((attempts / students) if students > 0 else 0.0, 2),
                'first_avg_grade': _round_metric(_mean(bucket.get('first') or []), 2),
                'latest_avg_grade': _round_metric(_mean(bucket.get('latest') or []), 2),
                'general': students,
            }
        )
    retaken_rows.sort(key=lambda x: x.get('retake_students', 0), reverse=True)
    retaken_rows = retaken_rows[:12]

    predicted_major_alignment = {'aligned': 0, 'switch_likely': 0, 'unknown': 0}
    for uid in user_by_id.keys():
        user = user_by_id.get(uid)
        if not user:
            continue
        per_area = [(area, credits) for (u, area), credits in area_credits_by_user.items() if u == uid]
        if not per_area:
            predicted_major_alignment['unknown'] += 1
            continue
        predicted_area = max(per_area, key=lambda x: x[1])[0]
        declared_major = (major_name_by_id.get(getattr(user, 'major_subject_area_id', None), '') or '').strip()
        if not declared_major:
            predicted_major_alignment['unknown'] += 1
        elif declared_major == predicted_area:
            predicted_major_alignment['aligned'] += 1
        else:
            predicted_major_alignment['switch_likely'] += 1

    ai_optimal_courses = []
    course_enroll_counts = {
        label: int(aggregate.get('count', 0))
        for (group, label), aggregate in grades_by_course.items()
        if group == 'general'
    }
    for row in _build_grade_series(grades_by_course, limit=20):
        title = row.get('label')
        avg_grade = _safe_float(row.get('general'), 0.0)
        enroll_count = int(course_enroll_counts.get(title, 0))
        score = _round_metric((avg_grade * 0.8) + min(2.0, enroll_count / 25.0), 2)
        ai_optimal_courses.append({'label': title, 'score': score, 'avg_grade': _round_metric(avg_grade, 2), 'enrollments': enroll_count})
    ai_optimal_courses.sort(key=lambda x: (x.get('score') or 0.0, x.get('enrollments') or 0), reverse=True)
    ai_optimal_courses = ai_optimal_courses[:12]

    gpa_major_general = []
    for row in gpa_major_series:
        raw_value = row.get('general')
        if raw_value is None:
            continue
        value = _safe_float(raw_value, 0.0)
        gpa_major_general.append({'label': row.get('label'), 'value': _round_metric(value, 2)})
    gpa_major_general.sort(key=lambda x: x.get('value', 0), reverse=True)

    course_demand_rows = []
    term_labels = sorted({term for (term, _title) in course_demand_by_term.keys()})
    for term in term_labels:
        best = None
        best_count = -1
        for (t, title), count in course_demand_by_term.items():
            if t == term and int(count) > best_count:
                best = title
                best_count = int(count)
        if best:
            course_demand_rows.append({'label': term, 'course_title': best, 'general': best_count})

    payload = {
        'summary': {
            'student_count': len(users),
            'schedule_count': len(schedules),
            'enrollment_count': int(enrollment_count),
        },
        'filters': {
            'options': {
                'years': years,
                'semesters': semesters,
                'majors': majors,
                'minors': minors,
                'genders': gender_options,
                'year_of_study': year_of_study_options,
            },
            'selected': {
                'year': selected_year,
                'semester': selected_semester,
                'major': selected_major,
                'minor': selected_minor,
                'gender': selected_gender,
                'year_of_study': selected_year_of_study,
            },
        },
        'groups': {
            'keys': all_groups,
            'labels': group_labels,
            'primary_compare': ['general', 'male', 'female'],
        },
        'credit': {
            'avg_total_credits': {group: _round_metric(_mean(credits_totals.get(group, [])), 2) for group in all_groups},
            'avg_credits_per_semester': credit_series_semester,
            'avg_credits_per_year': credit_series_year,
            'avg_total_credits_by_year_of_study': credit_totals_by_year_study,
        },
        'gpa': {
            'avg_total_gpa': {group: _round_metric(_mean(gpa_total.get(group, [])), 2) for group in all_groups},
            'avg_gpa_per_semester': gpa_series_semester,
            'avg_gpa_per_year': gpa_series_year,
            'avg_gpa_by_major': gpa_major_series,
        },
        'course_faculty': {
            'avg_grade_per_course': _build_grade_series(grades_by_course, limit=12),
            'avg_grade_per_faculty': _build_grade_series(grades_by_faculty, limit=12),
            'avg_grade_by_period_day': _build_grade_series(grades_by_period_day, limit=12),
        },
        'insights': {
            'gender_note': 'Prefer not to say responses are included in General.',
            'popular_courses_by_major': _build_popular_path_series(popular_courses_by_major, 1, limit=12),
            'popular_courses_by_minor': _build_popular_path_series(popular_courses_by_minor, 1, limit=12),
            'preferred_faculty_trends': preferred_faculty_rows,
            'common_electives_by_year': electives_rows,
            'retaken_courses': retaken_rows,
            'course_demand_top_by_term': course_demand_rows,
        },
        'ai': {
            'predicted_major_alignment': [
                {'label': 'Aligned', 'value': int(predicted_major_alignment.get('aligned', 0))},
                {'label': 'Switch Likely', 'value': int(predicted_major_alignment.get('switch_likely', 0))},
                {'label': 'Unknown', 'value': int(predicted_major_alignment.get('unknown', 0))},
            ],
            'optimal_schedule_courses': ai_optimal_courses,
            'predicted_gpa_by_major': gpa_major_general[:12],
            'recommended_credit_load_by_year_of_study': [
                {'label': row.get('label'), 'value': row.get('general')}
                for row in credit_totals_by_year_study
                if row.get('general') is not None
            ],
        },
        'future_ready': {
            'course_demand_trends_available': True,
            'student_progression_trends_available': True,
            'ai_performance_prediction_hooks': True,
            'difficulty_faculty_success_correlation_ready': True,
        },
    }

    overrides = _load_analytics_metric_overrides()
    for path, value in overrides.items():
        _apply_payload_override(payload, str(path), value)
    payload['overrides'] = {'count': len(overrides), 'items': overrides}

    return payload


@admin_bp.route('/api/analytics/data')
@login_required
@admin_required
def analytics_data_api():
    selected = {
        'year': (request.args.get('year') or '').strip(),
        'semester': (request.args.get('semester') or '').strip(),
        'major': (request.args.get('major') or '').strip(),
        'minor': (request.args.get('minor') or '').strip(),
        'gender': (request.args.get('gender') or '').strip().lower(),
        'year_of_study': (request.args.get('year_of_study') or '').strip(),
    }
    return jsonify({'success': True, 'data': _build_admin_analytics_payload(selected)})


@admin_bp.route('/api/analytics/overrides', methods=['GET', 'POST'])
@login_required
@admin_required
def analytics_overrides_api():
    if request.method == 'GET':
        overrides = _load_analytics_metric_overrides()
        return jsonify({'success': True, 'overrides': overrides})

    payload = request.get_json(silent=True) or {}
    overrides = payload.get('overrides')
    if not isinstance(overrides, dict):
        return jsonify({'success': False, 'message': 'Invalid overrides payload; expected object.'}), 400

    cleaned = {}
    for path, value in overrides.items():
        key = str(path or '').strip()
        if not key:
            continue
        cleaned[key] = value

    update_app_setting('analytics_metric_overrides_json', json.dumps(cleaned))
    return jsonify({'success': True, 'message': 'Analytics overrides saved.', 'count': len(cleaned)})


@admin_bp.route('/analytics/export')
@login_required
@admin_required
def analytics_export():
    selected = {
        'year': (request.args.get('year') or '').strip(),
        'semester': (request.args.get('semester') or '').strip(),
        'major': (request.args.get('major') or '').strip(),
        'minor': (request.args.get('minor') or '').strip(),
        'gender': (request.args.get('gender') or '').strip().lower(),
        'year_of_study': (request.args.get('year_of_study') or '').strip(),
    }
    payload = _build_admin_analytics_payload(selected)

    export_format = (request.args.get('format') or 'csv').strip().lower()
    if export_format == 'json':
        return Response(
            json.dumps(payload, indent=2),
            mimetype='application/json',
            headers={'Content-Disposition': 'attachment; filename=acadex_analytics.json'},
        )

    rows = []
    compare_groups = payload.get('groups', {}).get('primary_compare', ['general', 'male', 'female', 'prefer_not_to_say'])

    def add_series(section, metric, series):
        for item in series:
            row = {'section': section, 'metric': metric, 'label': item.get('label', '')}
            for group in compare_groups:
                row[group] = item.get(group)
            rows.append(row)

    overall_credit = {'label': 'Overall'}
    overall_credit.update(payload.get('credit', {}).get('avg_total_credits', {}))
    add_series('credit', 'avg_total_credits', [overall_credit])
    add_series('credit', 'avg_credits_per_semester', payload.get('credit', {}).get('avg_credits_per_semester', []))
    add_series('credit', 'avg_credits_per_year', payload.get('credit', {}).get('avg_credits_per_year', []))
    add_series('credit', 'avg_total_credits_by_year_of_study', payload.get('credit', {}).get('avg_total_credits_by_year_of_study', []))

    overall_gpa = {'label': 'Overall'}
    overall_gpa.update(payload.get('gpa', {}).get('avg_total_gpa', {}))
    add_series('gpa', 'avg_total_gpa', [overall_gpa])
    add_series('gpa', 'avg_gpa_per_semester', payload.get('gpa', {}).get('avg_gpa_per_semester', []))
    add_series('gpa', 'avg_gpa_per_year', payload.get('gpa', {}).get('avg_gpa_per_year', []))
    add_series('gpa', 'avg_gpa_by_major', payload.get('gpa', {}).get('avg_gpa_by_major', []))

    add_series('course_faculty', 'avg_grade_per_course', payload.get('course_faculty', {}).get('avg_grade_per_course', []))
    add_series('course_faculty', 'avg_grade_per_faculty', payload.get('course_faculty', {}).get('avg_grade_per_faculty', []))
    add_series('course_faculty', 'avg_grade_by_period_day', payload.get('course_faculty', {}).get('avg_grade_by_period_day', []))

    import io
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=['section', 'metric', 'label'] + compare_groups)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=acadex_analytics.csv'},
    )


@admin_bp.route('/ai', methods=['GET'])
@login_required
@admin_required
def ai_dashboard():
    # Filters
    year = (request.args.get('year') or '').strip()
    semester = (request.args.get('semester') or '').strip()
    schedule_type = (request.args.get('schedule_type') or '').strip().lower()
    area = (request.args.get('area') or '').strip()

    if schedule_type and schedule_type not in {'past', 'future'}:
        schedule_type = ''

    schedules_q = Schedule.query
    if year:
        try:
            schedules_q = schedules_q.filter(Schedule.year == int(year))
        except Exception:
            pass
    if semester:
        schedules_q = schedules_q.filter(Schedule.semester == semester)
    if schedule_type:
        schedules_q = schedules_q.filter(Schedule.schedule_type == schedule_type)

    # Options for filter dropdowns
    years = [r[0] for r in db.session.query(Schedule.year).distinct().order_by(Schedule.year.desc()).all()]
    semesters = [r[0] for r in db.session.query(Schedule.semester).distinct().order_by(Schedule.semester.asc()).all()]

    schedules = schedules_q.all()
    schedule_ids = [s.id for s in schedules]

    # Pull schedule_courses (optionally filtered by computed area)
    sc_rows = []
    if schedule_ids:
        try:
            sc_rows = db.session.execute(
                text(
                    '''
                    SELECT sc.schedule_id, sc.course_number, sc.grade
                    FROM schedule_courses sc
                    WHERE sc.schedule_id IN :sched_ids
                    '''
                ).bindparams(sa.bindparam('sched_ids', expanding=True)),
                {'sched_ids': schedule_ids},
            ).fetchall()
        except Exception as e:
            db.session.rollback()
            print(f"AI dashboard schedule_courses query failed: {e}")

    # Build metadata lookup from the unified precedence-resolved catalog.
    catalog = {}
    areas = set()

    course_numbers_needed = sorted({r.course_number for r in sc_rows if getattr(r, 'course_number', None)})
    if course_numbers_needed:
        try:
            unified_catalog = _build_admin_catalog_index()
            for cn in course_numbers_needed:
                key = str(cn or '').strip()
                if not key:
                    continue
                d = unified_catalog.get(key)
                if not d:
                    continue
                catalog[key] = d
                a = (d.get('area_of_study') or '').strip()
                if a:
                    areas.add(a)
        except Exception as e:
            db.session.rollback()
            print(f"AI dashboard catalog build failed: {e}")

    # Aggregate stats
    top_courses = {}
    top_faculty = {}
    credits_by_area = {}
    day_period_counts = {}  # key: (day, period)
    max_period = 0
    included_course_rows = 0

    def infer_area(course_number: str) -> str:
        import re as _re
        m = _re.match(r'^([A-Za-z]{2,6})', str(course_number or '').strip())
        return (m.group(1).upper() if m else 'General')

    import re as _re
    for row in sc_rows:
        course_number = row.course_number
        meta = catalog.get(course_number, {})
        title = meta.get('course_title') or course_number
        instructor = (meta.get('instructor') or '').strip() or 'TBA'
        dp = meta.get('day_and_period') or ''

        # Area filter (treated as "major")
        effective_area = (meta.get('area_of_study') or '').strip() or infer_area(course_number)
        if area and effective_area != area:
            continue
        included_course_rows += 1
        credits_by_area[effective_area] = credits_by_area.get(effective_area, 0.0) + float(meta.get('credits') or 0 or 0)

        top_courses[(course_number, title)] = top_courses.get((course_number, title), 0) + 1
        top_faculty[instructor] = top_faculty.get(instructor, 0) + 1

        # Day/period heatmap
        if dp and str(dp).upper() != 'TBA':
            tokens = str(dp).strip().split()
            current_day = ''
            for tok in tokens:
                day_m = _re.search(r'(Mon|Tue|Wed|Thu|Fri)', tok, _re.I)
                num_m = _re.search(r'(\d+)', tok)
                if day_m:
                    current_day = day_m.group(1).title()[:3]
                if current_day and num_m:
                    p = int(num_m.group(1))
                    max_period = max(max_period, p)
                    key = (current_day, str(p))
                    day_period_counts[key] = day_period_counts.get(key, 0) + 1

    # GPA trend by term (only past schedules with grades)
    gpa_points = {'A': 4.0, 'B': 3.0, 'C': 2.0, 'D': 1.0, 'F': 0.0}
    gpa_by_term = {}
    for s in schedules:
        if s.schedule_type != 'past':
            continue
        term_key = f"{s.semester} {s.year}"
        # gather grades for this schedule
        schedule_grades = [r for r in sc_rows if r.schedule_id == s.id and (r.grade or '').strip()]
        if not schedule_grades:
            continue

        total_points = 0.0
        total_credits = 0.0
        for r in schedule_grades:
            grade = (r.grade or '').strip().upper()
            if grade not in gpa_points:
                continue
            meta = catalog.get(r.course_number, {})
            try:
                creds = float(meta.get('credits') or 0)
            except Exception:
                creds = 0.0
            if creds <= 0:
                continue
            total_points += gpa_points[grade] * creds
            total_credits += creds
        if total_credits <= 0:
            continue
        gpa = total_points / total_credits
        gpa_by_term.setdefault(term_key, []).append(gpa)

    gpa_terms = list(gpa_by_term.keys())
    # simple sort: year then semester
    def _term_sort_key(t):
        try:
            sem, yr = t.split(' ', 1)
            return (int(yr), sem)
        except Exception:
            return (0, t)
    gpa_terms.sort(key=_term_sort_key)
    gpa_series = [sum(gpa_by_term[t]) / len(gpa_by_term[t]) for t in gpa_terms]

    # Prepare heatmap matrix
    days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    periods = [str(i) for i in range(1, max(1, max_period) + 1)]
    heat_rows = []
    heat_max = 0
    for p in periods:
        row_counts = []
        for d in days:
            c = day_period_counts.get((d, p), 0)
            heat_max = max(heat_max, c)
            row_counts.append(c)
        heat_rows.append({'period': p, 'counts': row_counts})

    top_courses_list = [
        {'course_number': cn, 'course_title': title, 'count': count}
        for (cn, title), count in sorted(top_courses.items(), key=lambda x: x[1], reverse=True)[:20]
    ]
    top_faculty_list = [
        {'instructor': instr, 'count': count}
        for instr, count in sorted(top_faculty.items(), key=lambda x: x[1], reverse=True)[:20]
    ]

    credits_by_area_list = [
        {'area': a, 'credits': round(v, 2)}
        for a, v in sorted(credits_by_area.items(), key=lambda x: x[1], reverse=True)[:20]
    ]

    # Anonymized AI/behavior usage summaries.
    ai_event_type_counts = []
    behavior_event_type_counts = []
    ai_event_day_counts = []
    behavior_event_day_counts = []
    try:
        insp = inspect(db.engine)
        if insp.has_table('ai_events'):
            ai_event_type_counts = db.session.execute(
                text(
                    '''SELECT event_type, COUNT(*) as count
                       FROM ai_events
                       GROUP BY event_type
                       ORDER BY count DESC
                       LIMIT 10'''
                )
            ).fetchall()
            ai_event_day_counts = db.session.execute(
                text(
                    '''SELECT DATE(created_at) as day, COUNT(*) as count
                       FROM ai_events
                       GROUP BY DATE(created_at)
                       ORDER BY day DESC
                       LIMIT 30'''
                )
            ).fetchall()

        if insp.has_table('behavior_events'):
            behavior_event_type_counts = db.session.execute(
                text(
                    '''SELECT event_type, COUNT(*) as count
                       FROM behavior_events
                       GROUP BY event_type
                       ORDER BY count DESC
                       LIMIT 10'''
                )
            ).fetchall()
            behavior_event_day_counts = db.session.execute(
                text(
                    '''SELECT DATE(created_at) as day, COUNT(*) as count
                       FROM behavior_events
                       GROUP BY DATE(created_at)
                       ORDER BY day DESC
                       LIMIT 30'''
                )
            ).fetchall()
    except Exception:
        db.session.rollback()

    export_kind = (request.args.get('export') or '').strip().lower()
    if export_kind:
        import io
        output = io.StringIO()
        writer = csv.writer(output)

        def _resp(name: str):
            return Response(
                output.getvalue(),
                mimetype='text/csv',
                headers={'Content-Disposition': f'attachment; filename={name}'},
            )

        if export_kind == 'top_courses':
            writer.writerow(['course_number', 'course_title', 'count'])
            for r in top_courses_list:
                writer.writerow([r['course_number'], r['course_title'], r['count']])
            return _resp('ai_top_courses.csv')

        if export_kind == 'top_instructors':
            writer.writerow(['instructor', 'count'])
            for r in top_faculty_list:
                writer.writerow([r['instructor'], r['count']])
            return _resp('ai_top_instructors.csv')

        if export_kind == 'credits_by_area':
            writer.writerow(['area', 'credits'])
            for r in credits_by_area_list:
                writer.writerow([r['area'], r['credits']])
            return _resp('ai_credits_by_area.csv')

        if export_kind == 'heatmap':
            writer.writerow(['period'] + days)
            for r in heat_rows:
                writer.writerow([r['period']] + r['counts'])
            return _resp('ai_heatmap.csv')

    ai_enabled_setting = get_app_setting('ai_enabled', 'false')
    ai_data_collection_enabled_setting = get_app_setting('ai_data_collection_enabled', 'false')

    # Admin controls (settings only; feature implementations may be separate)
    ai_notifications_enabled_setting = get_app_setting('ai_notifications_enabled', 'false')
    ai_notifications_frequency_setting = get_app_setting('ai_notifications_frequency', 'daily')
    ai_recommendations_enabled_setting = get_app_setting('ai_recommendations_enabled', 'true')
    ai_predictive_analysis_enabled_setting = get_app_setting('ai_predictive_analysis_enabled', 'true')
    behavior_tracking_enabled_setting = get_app_setting('behavior_tracking_enabled', 'false')
    behavior_tracking_level_setting = get_app_setting('behavior_tracking_level', 'standard')

    # Provide area dropdown options (major-like)
    if not areas:
        areas = set([d['area'] for d in credits_by_area_list])

    return render_template(
        'Admin/ai_dashboard.html',
        ai_enabled_setting=ai_enabled_setting,
        ai_data_collection_enabled_setting=ai_data_collection_enabled_setting,
        ai_notifications_enabled_setting=ai_notifications_enabled_setting,
        ai_notifications_frequency_setting=ai_notifications_frequency_setting,
        ai_recommendations_enabled_setting=ai_recommendations_enabled_setting,
        ai_predictive_analysis_enabled_setting=ai_predictive_analysis_enabled_setting,
        behavior_tracking_enabled_setting=behavior_tracking_enabled_setting,
        behavior_tracking_level_setting=behavior_tracking_level_setting,
        years=years,
        semesters=semesters,
        areas=sorted([a for a in areas if a]),
        selected_year=year,
        selected_semester=semester,
        selected_schedule_type=schedule_type,
        selected_area=area,
        schedule_count=len(schedules),
        included_course_rows=included_course_rows,
        unique_courses=len(top_courses),
        unique_instructors=len(top_faculty),
        gpa_terms_json=json.dumps(gpa_terms),
        gpa_series_json=json.dumps(gpa_series),
        credits_by_area_json=json.dumps([d['area'] for d in credits_by_area_list]),
        credits_series_json=json.dumps([d['credits'] for d in credits_by_area_list]),
        heat_days=days,
        heat_rows=heat_rows,
        heat_max=heat_max,
        top_courses=top_courses_list,
        top_faculty=top_faculty_list,
        ai_event_type_counts=[dict(r._mapping) for r in ai_event_type_counts],
        behavior_event_type_counts=[dict(r._mapping) for r in behavior_event_type_counts],
        ai_event_days_json=json.dumps([str(r.day) for r in ai_event_day_counts][::-1]),
        ai_event_counts_json=json.dumps([int(r.count) for r in ai_event_day_counts][::-1]),
        behavior_event_days_json=json.dumps([str(r.day) for r in behavior_event_day_counts][::-1]),
        behavior_event_counts_json=json.dumps([int(r.count) for r in behavior_event_day_counts][::-1]),
    )


@admin_bp.route('/ai/settings', methods=['POST'])
@login_required
@admin_required
def ai_settings_update():
    ai_enabled = 'ai_enabled_setting' in request.form
    ai_data_collection_enabled = 'ai_data_collection_enabled_setting' in request.form
    update_app_setting('ai_enabled', 'true' if ai_enabled else 'false')
    update_app_setting('ai_data_collection_enabled', 'true' if ai_data_collection_enabled else 'false')

    ai_notifications_enabled = 'ai_notifications_enabled_setting' in request.form
    ai_notifications_frequency = (request.form.get('ai_notifications_frequency_setting') or 'daily').strip().lower()
    if ai_notifications_frequency not in {'realtime', 'daily', 'weekly'}:
        ai_notifications_frequency = 'daily'
    update_app_setting('ai_notifications_enabled', 'true' if ai_notifications_enabled else 'false')
    update_app_setting('ai_notifications_frequency', ai_notifications_frequency)

    ai_recommendations_enabled = 'ai_recommendations_enabled_setting' in request.form
    ai_predictive_analysis_enabled = 'ai_predictive_analysis_enabled_setting' in request.form
    update_app_setting('ai_recommendations_enabled', 'true' if ai_recommendations_enabled else 'false')
    update_app_setting('ai_predictive_analysis_enabled', 'true' if ai_predictive_analysis_enabled else 'false')

    behavior_tracking_enabled = 'behavior_tracking_enabled_setting' in request.form
    behavior_tracking_level = (request.form.get('behavior_tracking_level_setting') or 'standard').strip().lower()
    if behavior_tracking_level not in {'minimal', 'standard', 'detailed'}:
        behavior_tracking_level = 'standard'
    update_app_setting('behavior_tracking_enabled', 'true' if behavior_tracking_enabled else 'false')
    update_app_setting('behavior_tracking_level', behavior_tracking_level)

    flash('AI settings updated.', 'success')
    return redirect(url_for('admin.ai_dashboard'))


@admin_bp.route('/ai/export_events.csv', methods=['GET'])
@login_required
@admin_required
def ai_export_events_csv():
    # Optional export of AIEvent logs (if table exists). This export is for admins only.
    try:
        insp = inspect(db.engine)
        if not insp.has_table('ai_events'):
            return jsonify({'success': False, 'message': 'ai_events table not found'}), 404

        rows = db.session.execute(
            text('SELECT id, created_at, user_id, schedule_id, event_type, route, metadata_json FROM ai_events ORDER BY id DESC')
        ).fetchall()

        import csv
        import io
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['id', 'created_at', 'user_id', 'schedule_id', 'event_type', 'route', 'metadata_json'])
        for r in rows:
            m = r._mapping
            writer.writerow([m['id'], m['created_at'], m['user_id'], m['schedule_id'], m['event_type'], m['route'], m['metadata_json']])

        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=ai_events.csv'},
        )
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': 'Unable to export AI events.'}), 500


@admin_bp.route('/ai/export_behavior_events.csv', methods=['GET'])
@login_required
@admin_required
def ai_export_behavior_events_csv():
    # Optional export of BehaviorEvent logs (if table exists). This export is for admins only.
    try:
        insp = inspect(db.engine)
        if not insp.has_table('behavior_events'):
            return jsonify({'success': False, 'message': 'behavior_events table not found'}), 404

        rows = db.session.execute(
            text(
                'SELECT id, created_at, user_id, session_id, schedule_id, event_type, route, metadata_json '
                'FROM behavior_events ORDER BY id DESC'
            )
        ).fetchall()

        import csv
        import io

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['id', 'created_at', 'user_id', 'session_id', 'schedule_id', 'event_type', 'route', 'metadata_json'])
        for r in rows:
            m = r._mapping
            writer.writerow(
                [
                    m['id'],
                    m['created_at'],
                    m['user_id'],
                    m['session_id'],
                    m['schedule_id'],
                    m['event_type'],
                    m['route'],
                    m['metadata_json'],
                ]
            )

        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=behavior_events.csv'},
        )
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': 'Unable to export behavior events.'}), 500