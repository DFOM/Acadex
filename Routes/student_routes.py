# Routes/student_routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import login_required, current_user
from sqlalchemy import text
import uuid
from models import db, Schedule, ScheduleCourse, Period, Setting
# FIXED: Removed the missing get_available_datasets import to prevent the 500 boot crash
from utils import get_best_course_table

student_bp = Blueprint('student', __name__)

def get_guest_id():
    if 'guest_id' not in session:
        session['guest_id'] = str(uuid.uuid4())
    return session['guest_id']

@student_bp.route('/courses/search')
def search_courses():
    query_str = request.args.get('q', '').strip()
    year = request.args.get('year')
    semester = request.args.get('semester')

    if not query_str or len(query_str) < 2:
        return jsonify([])

    table_name, _ = get_best_course_table(year, semester)
    if not table_name:
        return jsonify([])

    try:
        search_sql = text(f'''
            SELECT * FROM "{table_name}"
            WHERE LOWER("Course_Number") LIKE LOWER(:q)
            OR LOWER("Course_Name") LIKE LOWER(:q)
            LIMIT 20
        ''')
        results = db.session.execute(search_sql, {'q': f'%{query_str}%'}).fetchall()
        return jsonify([dict(r._mapping) for r in results])
    except Exception as e:
        db.session.rollback()
        print(f"Search error: {e}")
        return jsonify([])

@student_bp.route('/dashboard')
@login_required
def user_dashboard():
    schedules = Schedule.query.filter_by(user_id=current_user.id).order_by(Schedule.year.desc()).all()
    past_schedules = [s for s in schedules if s.schedule_type == 'past']
    return render_template('Student/dashboard.html', 
                           schedules=schedules, 
                           has_past=len(past_schedules) > 0)

@student_bp.route('/student/schedule/create', methods=['GET', 'POST'])
def create_schedule():
    if request.method == 'POST':
        name = request.form.get('schedule_name')
        year = request.form.get('year')
        semester = request.form.get('semester')
        
        if not current_user.is_authenticated:
            sched_type = 'future'
        else:
            sched_type = request.form.get('schedule_type', 'future')

        if not all([name, year, semester]):
            flash("Please fill out all fields.", "error")
            return redirect(url_for('student.user_dashboard') if current_user.is_authenticated else url_for('auth.login'))

        new_sched = Schedule(
            schedule_name=name,
            year=int(year),
            semester=semester,
            schedule_type=sched_type
        )

        if current_user.is_authenticated:
            new_sched.user_id = current_user.id
        else:
            new_sched.session_id = get_guest_id()

        db.session.add(new_sched)
        db.session.commit()
        
        return redirect(url_for('student.edit_schedule', schedule_id=new_sched.id))
        
    # GET method logic for populating dropdowns (FIXED to not rely on utils.py)
    current_year_setting = Setting.query.get('current_year')
    current_year = int(current_year_setting.value) if current_year_setting and current_year_setting.value.isdigit() else 2024
    
    # Generate a dynamic list of years for the user to choose from
    available_years = [current_year, current_year + 1, current_year + 2]

    return render_template('Student/create_schedule.html', available_years=available_years)

@student_bp.route('/student/schedule/edit/<int:schedule_id>')
def edit_schedule(schedule_id):
    if current_user.is_authenticated:
        schedule = Schedule.query.filter_by(id=schedule_id, user_id=current_user.id).first_or_404()
    else:
        schedule = Schedule.query.filter_by(id=schedule_id, session_id=session.get('guest_id')).first_or_404()
        
    table_name, _ = get_best_course_table(schedule.year, schedule.semester)
    
    current_courses = []
    if table_name:
        try:
            query = text(f'''
                SELECT sc.id as entry_id, sc.grade, c.*
                FROM schedule_courses sc
                JOIN "{table_name}" c ON sc.course_number = c."Course_Number"
                WHERE sc.schedule_id = :sched_id
            ''')
            result = db.session.execute(query, {'sched_id': schedule.id}).fetchall()
            current_courses = [dict(r._mapping) for r in result]
        except Exception as e:
            db.session.rollback()
            print(f"Error fetching current courses: {e}")

    # ALREADY OBTAINED TRACKER
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
    return render_template('Student/edit_schedule.html', 
                           schedule=schedule, 
                           current_courses=current_courses,
                           obtained_courses=obtained_courses,
                           periods=periods)

@student_bp.route('/api/add_course', methods=['POST'])
def add_course():
    data = request.get_json()
    sched_id = data.get('schedule_id')
    course_num = data.get('course_number')

    if not sched_id or not course_num:
        return jsonify({'success': False, 'message': 'Missing data'}), 400

    if current_user.is_authenticated:
        sched = Schedule.query.filter_by(id=sched_id, user_id=current_user.id).first()
    else:
        sched = Schedule.query.filter_by(id=sched_id, session_id=session.get('guest_id')).first()
        
    if not sched:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403

    new_course = ScheduleCourse(schedule_id=sched_id, course_number=course_num)
    db.session.add(new_course)
    db.session.commit()

    return jsonify({'success': True})

@student_bp.route('/api/remove_course/<int:entry_id>', methods=['DELETE'])
def remove_course(entry_id):
    course_entry = ScheduleCourse.query.get(entry_id)
    if not course_entry:
        return jsonify({'success': False, 'message': 'Course not found'}), 404
        
    if current_user.is_authenticated:
        if course_entry.schedule.user_id != current_user.id:
            return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    else:
        if course_entry.schedule.session_id != session.get('guest_id'):
            return jsonify({'success': False, 'message': 'Unauthorized'}), 403

    db.session.delete(course_entry)
    db.session.commit()

    return jsonify({'success': True})

@student_bp.route('/api/update_grade', methods=['POST'])
@login_required
def update_grade():
    data = request.get_json()
    entry_id = data.get('entry_id')
    grade = data.get('grade')
    
    course_entry = ScheduleCourse.query.get(entry_id)
    if not course_entry or course_entry.schedule.user_id != current_user.id:
        return jsonify({'success': False, 'message': 'Unauthorized or not found'}), 403
        
    course_entry.grade = grade
    db.session.commit()
    
    return jsonify({'success': True})