# Routes/schedule_routes.py
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from models import db, Schedule, SubjectArea, Setting, ScheduleCourse, AIEvent, BehaviorEvent

schedule_bp = Blueprint('schedule', __name__)

def get_app_setting(key, default=""):
    setting = Setting.query.get(key)
    return setting.value if setting else default

@schedule_bp.route('/dashboard')
@login_required
def user_dashboard():
    # The canonical student dashboard is served under /student/dashboard.
    # Keep this route as a stable alias to avoid broken links.
    return redirect(url_for('student.user_dashboard'))

@schedule_bp.route('/delete/<int:schedule_id>', methods=['POST'])
@login_required
def delete_schedule(schedule_id):
    try:
        # Verify ownership via ORM
        sched = Schedule.query.filter_by(id=schedule_id, user_id=current_user.id).first()
        if sched:
            ScheduleCourse.query.filter_by(schedule_id=sched.id).delete(synchronize_session=False)
            AIEvent.query.filter_by(schedule_id=sched.id).delete(synchronize_session=False)
            BehaviorEvent.query.filter_by(schedule_id=sched.id).delete(synchronize_session=False)
            db.session.delete(sched)
            db.session.commit()
            flash('Schedule deleted successfully.', 'success')
        else:
            flash('Unauthorized action.', 'error')
    except Exception as e:
        db.session.rollback()
        flash('Error deleting schedule.', 'error')
        
    return redirect(url_for('schedule.user_dashboard'))