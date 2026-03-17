from flask import Blueprint, render_template, session, jsonify, request
from flask_login import current_user
from datetime import datetime, timezone, timedelta
from typing import Optional

import uuid

from models import Announcement, Schedule, Setting, CatalogStatus
from utils import (
    get_course_data_tables,
    ensure_default_landing_sections,
    safe_json_loads,
    format_course_catalog_name,
    parse_course_catalog_metadata,
)


main_bp = Blueprint('main', __name__)


def _get_guest_id() -> str:
    if 'guest_id' not in session:
        session['guest_id'] = str(uuid.uuid4())
    return session['guest_id']


@main_bp.route('/')
def landing_page():
    landing_sections = ensure_default_landing_sections()

    hero_section = next((s for s in landing_sections if s.name == 'hero'), None)
    features_section = next((s for s in landing_sections if s.name == 'features'), None)
    hero_cfg = safe_json_loads(getattr(hero_section, 'content_json', None), {})
    features_cfg = safe_json_loads(getattr(features_section, 'content_json', None), {})

    about_us = Setting.query.get('about_us')
    creator_message = Setting.query.get('creator_message')
    current_year = Setting.query.get('current_year')
    current_semester = Setting.query.get('current_semester')

    announcements = (
        Announcement.query.filter_by(is_active=True)
        .order_by(Announcement.date_posted.desc())
        .all()
    )

    guest_schedules = []
    if not current_user.is_authenticated:
        guest_schedules = (
            Schedule.query.filter_by(session_id=_get_guest_id(), schedule_type='future')
            .order_by(Schedule.year.desc())
            .all()
        )

    available_tables = get_course_data_tables()

    # Course Catalog Status panel: merge DB statuses with discovered tables.
    catalog_status_map = {}
    try:
        for row in CatalogStatus.query.all():
            catalog_status_map[row.term] = row
    except Exception:
        catalog_status_map = {}

    parsed_catalogs = []
    for table_name in available_tables:
        parsed = parse_course_catalog_metadata(table_name)
        if not parsed:
            continue
        label = format_course_catalog_name(table_name)
        if not label:
            continue
        parsed_catalogs.append({'label': label, **parsed})

    parsed_catalogs.sort(key=lambda c: (int(c['year']), {'spring': 0, 'fall': 1, 'full': 2}.get(str(c['semester_type']), 9)), reverse=True)

    seen_labels = set()
    course_catalog_statuses = []
    for item in parsed_catalogs:
        label = item['label']
        if label in seen_labels:
            continue
        seen_labels.add(label)
        row = catalog_status_map.get(label)
        status = (row.status if row else 'available')
        updated_at = (row.updated_at if row else None)
        course_catalog_statuses.append({'term': label, 'status': status, 'updated_at': updated_at})

    return render_template(
        'Student/index.html',
        landing_sections=landing_sections,
        hero_cfg=hero_cfg,
        features_cfg=features_cfg,
        about_us=about_us.value if about_us else "Welcome to our dynamic scheduling platform.",
        creator_message=creator_message.value if creator_message else "",
        current_year=current_year.value if current_year else "2025",
        current_semester=current_semester.value if current_semester else "Spring",
        announcements=announcements,
        guest_schedules=guest_schedules,
        available_tables=available_tables,
        course_catalog_statuses=course_catalog_statuses,
    )


def _dt_to_iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def _get_last_seen() -> Optional[datetime]:
    raw = session.get('acadex_ann_last_seen')
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except Exception:
        return None


@main_bp.route('/api/landing/announcements')
def api_landing_announcements():
    limit = request.args.get('limit', type=int) or 50
    limit = max(1, min(limit, 100))

    last_seen = _get_last_seen()
    recent_cutoff = None
    if not last_seen:
        recent_cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    anns = (
        Announcement.query.filter_by(is_active=True)
        .order_by(Announcement.date_posted.desc())
        .limit(limit)
        .all()
    )
    payload = []
    for a in anns:
        is_new = False
        if last_seen:
            try:
                is_new = (a.date_posted and a.date_posted > last_seen)
            except Exception:
                is_new = False
        elif recent_cutoff:
            try:
                dt = a.date_posted
                if dt and dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                is_new = bool(dt and dt > recent_cutoff)
            except Exception:
                is_new = False
        payload.append(
            {
                'id': a.id,
                'title': a.title,
                'content': a.content,
                'ann_type': getattr(a, 'ann_type', 'info') or 'info',
                'date_posted': _dt_to_iso(a.date_posted),
                'updated_at': _dt_to_iso(getattr(a, 'updated_at', None)),
                'is_new': is_new,
            }
        )
    return jsonify({'success': True, 'announcements': payload})


@main_bp.route('/api/landing/announcements/mark_seen', methods=['POST'])
def api_mark_announcements_seen():
    session['acadex_ann_last_seen'] = datetime.now(timezone.utc).isoformat()
    return jsonify({'success': True})
