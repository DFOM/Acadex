# Routes/student_routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import login_required, current_user
from sqlalchemy import text
import uuid
import re
from models import db, Schedule, ScheduleCourse, Period, Setting, Announcement
from utils import get_best_course_table, get_course_data_tables, calculate_gpa, is_valid_table_name
from services.eligibility import prereqs_met

student_bp = Blueprint('student', __name__)

def get_guest_id():
    if 'guest_id' not in session:
        session['guest_id'] = str(uuid.uuid4())
    return session['guest_id']

# --- Landing Page (Guest View & Homepage) ---
@student_bp.route('/')
def landing_page():
    about_us = Setting.query.get('about_us')
    creator_message = Setting.query.get('creator_message')
    current_year = Setting.query.get('current_year')
    current_semester = Setting.query.get('current_semester')
    
    announcements = Announcement.query.filter_by(is_active=True).order_by(Announcement.date_posted.desc()).all()
    
    guest_schedules = []
    if not current_user.is_authenticated:
        guest_schedules = Schedule.query.filter_by(
            session_id=get_guest_id(), 
            schedule_type='future'
        ).order_by(Schedule.year.desc()).all()
        
    # NEW: Fetch all available datasets to show on the landing page
    available_tables = get_course_data_tables()
        
    return render_template('Student/index.html',
                           about_us=about_us.value if about_us else "Welcome to our dynamic scheduling platform.",
                           creator_message=creator_message.value if creator_message else "",
                           current_year=current_year.value if current_year else "2025",
                           current_semester=current_semester.value if current_semester else "Spring",
                           announcements=announcements,
                           guest_schedules=guest_schedules,
                           available_tables=available_tables)

@student_bp.route('/courses/search')
def search_courses():
    query_str = request.args.get('q', '').strip()
    year = request.args.get('year')
    semester = request.args.get('semester')

    if not query_str or len(query_str) < 2:
        return jsonify([])

    table_name, _ = get_best_course_table(year, semester)
    if not table_name or not is_valid_table_name(table_name):
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

# --- Premium Dashboard Logic ---
@student_bp.route('/dashboard')
@login_required
def user_dashboard():
    schedules = Schedule.query.filter_by(user_id=current_user.id).order_by(Schedule.year.desc()).all()
    past_schedules = [s for s in schedules if s.schedule_type == 'past']
    
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

    favorite_faculty = max(top_faculty, key=top_faculty.get) if top_faculty else "N/A"
    favorite_day = max(top_periods, key=top_periods.get) if top_periods else "N/A"

    return render_template('Student/dashboard.html', 
                           schedules=schedules, 
                           has_past=len(past_schedules) > 0,
                           total_credits=total_credits,
                           area_credits=area_credits,
                           overall_gpa=overall_gpa,
                           predictive_gpa=predictive_gpa,
                           favorite_faculty=favorite_faculty,
                           favorite_day=favorite_day)

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
        
    current_year_setting = Setting.query.get('current_year')
    current_year = int(current_year_setting.value) if current_year_setting and current_year_setting.value.isdigit() else 2024
    
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
    if table_name and is_valid_table_name(table_name):
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
@student_bp.route('/student/api/add_course', methods=['POST'])
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

    # --- Prerequisite enforcement ---
    # Fetch the prerequisite string for the selected course from the schedule's dataset.
    table_name, _ = get_best_course_table(sched.year, sched.semester)
    if not table_name or not is_valid_table_name(table_name):
        print(f"Prereq check blocked: invalid/missing dataset table for schedule {sched.id}")
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
    try:
        prereq_row = db.session.execute(
            text(f'SELECT "Prerequisite" FROM "{table_name}" WHERE "Course_Number" = :cn LIMIT 1'),
            {'cn': course_num},
        ).fetchone()
        if not prereq_row:
            return (
                jsonify(
                    {
                        'success': False,
                        'error': f'Unknown course {course_num}',
                        'message': f'Unknown course {course_num}'
                    }
                ),
                400,
            )
        prereq_str = prereq_row[0]
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
            completed_rows = db.session.execute(
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
                {'uid': current_user.id},
            ).fetchall()
            completed_passing = [r[0] for r in completed_rows]
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

    met, friendly_error = prereqs_met(prereq_str, completed_passing)
    if friendly_error:
        print(f"Invalid prereq syntax for course {course_num}: {prereq_str}")
        return (
            jsonify(
                {
                    'success': False,
                    'error': f'Invalid prerequisite format for {course_num}',
                    'message': f'Invalid prerequisite format for {course_num}'
                }
            ),
            400,
        )
    if not met:
        return (
            jsonify(
                {
                    'success': False,
                    'error': f'Prerequisites not met for {course_num}',
                    'message': f'Prerequisites not met for {course_num}'
                }
            ),
            400,
        )

    new_course = ScheduleCourse(schedule_id=sched_id, course_number=course_num)
    db.session.add(new_course)
    db.session.commit()

    return jsonify({'success': True})

@student_bp.route('/api/remove_course/<int:entry_id>', methods=['DELETE', 'POST'])
@student_bp.route('/student/api/remove_course/<int:entry_id>', methods=['DELETE', 'POST'])
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