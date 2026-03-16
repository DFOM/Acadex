from flask import Blueprint, render_template, session
from flask_login import current_user

import uuid

from models import Announcement, Schedule, Setting
from utils import get_course_data_tables


main_bp = Blueprint('main', __name__)


def _get_guest_id() -> str:
    if 'guest_id' not in session:
        session['guest_id'] = str(uuid.uuid4())
    return session['guest_id']


@main_bp.route('/')
def landing_page():
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

    return render_template(
        'Student/index.html',
        about_us=about_us.value if about_us else "Welcome to our dynamic scheduling platform.",
        creator_message=creator_message.value if creator_message else "",
        current_year=current_year.value if current_year else "2025",
        current_semester=current_semester.value if current_semester else "Spring",
        announcements=announcements,
        guest_schedules=guest_schedules,
        available_tables=available_tables,
    )
