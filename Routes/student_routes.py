# Routes/student_routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from sqlalchemy import text
from models import db, Schedule, ScheduleCourse, Period, Setting
from utils import get_course_data_tables, get_best_course_table
import re

student_bp = Blueprint('student', __name__, url_prefix='/student')

def get_app_setting_local(key, default=""):
    setting = Setting.query.get(key)
    return setting.value if setting else default

@student_bp.route('/schedule/create', methods=['GET', 'POST'])
@login_required
def create_schedule():
    if request.method == 'POST':
        schedule_name = request.form.get('schedule_name')
        year = request.form.get('year')
        semester = request.form.get('semester')
        schedule_type = request.form.get('schedule_type', 'future')

        if not schedule_name or not year or not semester:
            flash("Please fill out all fields.", "error")
            return redirect(url_for('auth.choose_schedule_type'))

        try:
            # Using SQLAlchemy ORM to create the schedule
            new_schedule = Schedule(
                user_id=current_user.id,
                schedule_name=schedule_name,
                year=int(year),
                semester=semester,
                schedule_type=schedule_type
            )
            db.session.add(new_schedule)
            db.session.commit()
            
            flash(f"Schedule '{schedule_name}' created successfully!", "success")
            return redirect(url_for('student.edit_schedule', schedule_id=new_schedule.id))
        except Exception as e:
            db.session.rollback()
            flash(f"Database error: {e}", "error")
            return redirect(url_for('auth.choose_schedule_type'))

    schedule_type = request.args.get('type', 'future')
    
    # Fetch current year from settings using ORM
    current_year = int(get_app_setting_local('current_year', '2025'))
    
    # Extract available years from dataset names
    tables = get_course_data_tables()
    available_years = set()
    for t in tables:
        match = re.search(r'\d{4}', t)
        if match:
            available_years.add(int(match.group()))
            
    # Fallback if no tables are uploaded yet
    if not available_years:
        available_years = {current_year - 1, current_year, current_year + 1}
        
    available_years = sorted(list(available_years))

    return render_template('Student/create_schedule.html', 
                           schedule_type=schedule_type,
                           current_year=current_year,
                           available_years=available_years)

@student_bp.route('/schedule/edit/<int:schedule_id>')
@login_required
def edit_schedule(schedule_id):
    # Fetch schedule using ORM
    schedule = Schedule.query.filter_by(id=schedule_id, user_id=current_user.id).first()

    if not schedule:
        flash("Plan not found or you do not have permission to view it.", "error")
        return redirect(url_for('schedule.user_dashboard'))

    target_table, needs_filter = get_best_course_table(schedule.year, schedule.semester)

    current_courses = []
    if target_table:
        # Dynamic query mapped to text() for safety
        sql = f'''
            SELECT sc.id as entry_id, sc.grade, c.*
            FROM schedule_courses sc
            JOIN "{target_table}" c ON sc.course_number = c.Course_Number
            WHERE sc.schedule_id = :sched_id
        '''
        params = {'sched_id': schedule.id}

        if needs_filter:
            sql += " AND c.Semester LIKE :sem"
            params['sem'] = f"%{schedule.semester}%"

        try:
            rows = db.session.execute(text(sql), params).fetchall()
            current_courses = [dict(row._mapping) for row in rows]
        except Exception as e:
            print(f"Error fetching current courses: {e}")

    obtained_courses = []
    try:
        # ORM Join query to find past courses and filter out Fails
        past_courses = ScheduleCourse.query.join(Schedule).filter(
            Schedule.user_id == current_user.id,
            Schedule.schedule_type == 'past',
            ScheduleCourse.grade.isnot(None),
            ScheduleCourse.grade != '',
            ScheduleCourse.grade != 'F'
        ).all()

        for course in past_courses:
            base_code = re.sub(r'-S\d+-.*', '', course.course_number).strip()
            obtained_courses.append(base_code)
    except Exception as e:
        print(f"Error fetching obtained courses: {e}")

    periods = Period.query.order_by(Period.name.asc()).all()

    return render_template('Student/edit_schedule.html',
                           schedule=schedule,
                           periods=periods,
                           current_courses=current_courses,
                           obtained_courses=list(set(obtained_courses)))

@student_bp.route('/courses/search')
@login_required
def search_courses():
    query = request.args.get('query', '').strip()
    schedule_id = request.args.get('schedule_id', type=int)

    schedule = Schedule.query.filter_by(id=schedule_id, user_id=current_user.id).first()

    if not schedule:
        return jsonify([])

    target_table, needs_filter = get_best_course_table(schedule.year, schedule.semester)

    courses = []
    if target_table:
        sql = f'''
            SELECT c.*
            FROM "{target_table}" c
            WHERE 1=1
        '''
        params = {}

        if query:
            # FIXED: Wrapped in LOWER() for PostgreSQL universal case-insensitive search
            sql += " AND (LOWER(c.Course_Title) LIKE LOWER(:q) OR LOWER(c.Course_Number) LIKE LOWER(:q))"
            params['q'] = f'%{query}%'

        if needs_filter:
            sql += " AND c.Semester LIKE :sem"
            params['sem'] = f"%{schedule.semester}%"

        sql += " LIMIT 250"

        try:
            rows = db.session.execute(text(sql), params).fetchall()
            courses = [dict(row._mapping) for row in rows]
        except Exception as e:
            print(f"Search Error: {e}")

    return jsonify(courses)

@student_bp.route('/api/add_course', methods=['POST'])
@login_required
def api_add_course():
    data = request.get_json()
    schedule_id = data.get('schedule_id')
    course_number = data.get('course_number')

    if not schedule_id or not course_number:
        return jsonify({'success': False, 'message': 'Missing data.'}), 400

    try:
        schedule = Schedule.query.filter_by(id=schedule_id, user_id=current_user.id).first()
        if not schedule:
            return jsonify({'success': False, 'message': 'Unauthorized access.'}), 403

        existing = ScheduleCourse.query.filter_by(schedule_id=schedule_id, course_number=course_number).first()
        if existing:
            return jsonify({'success': False, 'message': 'Course is already in your plan.'}), 400

        new_course = ScheduleCourse(schedule_id=schedule_id, course_number=course_number)
        db.session.add(new_course)
        db.session.commit()
        
        return jsonify({'success': True})

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@student_bp.route('/api/remove_course/<int:entry_id>', methods=['POST'])
@login_required
def api_remove_course(entry_id):
    try:
        # Securely find the course ensuring it belongs to a schedule owned by the current user
        entry = ScheduleCourse.query.join(Schedule).filter(
            ScheduleCourse.id == entry_id, 
            Schedule.user_id == current_user.id
        ).first()

        if not entry:
            return jsonify({'success': False, 'message': 'Unauthorized or course not found.'}), 403

        db.session.delete(entry)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@student_bp.route('/api/update_grade', methods=['POST'])
@login_required
def api_update_grade():
    data = request.get_json()
    entry_id = data.get('entry_id')
    grade = data.get('grade')

    try:
        entry = ScheduleCourse.query.join(Schedule).filter(
            ScheduleCourse.id == entry_id, 
            Schedule.user_id == current_user.id
        ).first()

        if not entry:
            return jsonify({'success': False, 'message': 'Unauthorized access.'}), 403

        entry.grade = grade
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500