# Routes/student_routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import login_required, current_user
from sqlalchemy import text
import uuid
import re
from models import db, Schedule, ScheduleCourse, Period, Setting, Announcement
from utils import get_best_course_table, get_course_data_tables, calculate_gpa, is_valid_table_name
from services.eligibility import prereqs_met
from services.conflicts import parse_day_period_blocks, has_conflict
from services.ai.recommendations import recommend_courses
from services.ai.generator import generate_schedule_courses

student_bp = Blueprint('student', __name__)

def get_guest_id():
    if 'guest_id' not in session:
        session['guest_id'] = str(uuid.uuid4())
    return session['guest_id']

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
            sched = Schedule.query.filter_by(id=schedule_id_int, session_id=session.get('guest_id')).first()

        if not sched:
            return jsonify([])

        resolved_year = sched.year
        resolved_semester = sched.semester

    table_name, _ = get_best_course_table(resolved_year, resolved_semester)
    if not table_name or not is_valid_table_name(table_name):
        return jsonify([])

    # Full catalog load for the schedule editor.
    if json_mode and schedule_id and query_str == '':
        try:
            results = db.session.execute(
                text(
                    f'''
                    SELECT "Course_Number", "Course_Title", "Credits", "Instructor", "Day_and_Period", "Area_of_Study", "Prerequisite"
                    FROM "{table_name}"
                    '''
                )
            ).fetchall()
            return jsonify([dict(r._mapping) for r in results])
        except Exception as e:
            db.session.rollback()
            print(f"Catalog load error: {e}")
            return jsonify([])

    # Search mode
    if not query_str or len(query_str) < 2:
        return jsonify([])

    try:
        search_sql = text(
            f'''
            SELECT "Course_Number", "Course_Title", "Credits", "Instructor", "Day_and_Period", "Area_of_Study", "Prerequisite"
            FROM "{table_name}"
            WHERE LOWER("Course_Number") LIKE LOWER(:q)
               OR LOWER("Course_Title") LIKE LOWER(:q)
            LIMIT 50
            '''
        )
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

@student_bp.route('/schedule/create', methods=['GET', 'POST'])
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

@student_bp.route('/schedule/edit/<int:schedule_id>')
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

    # Prevent duplicates
    existing = ScheduleCourse.query.filter_by(schedule_id=sched.id, course_number=course_num).first()
    if existing:
        return (
            jsonify(
                {
                    'success': False,
                    'error': f'Course already in schedule: {course_num}',
                    'message': f'Course already in schedule: {course_num}'
                }
            ),
            400,
        )

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
    candidate_day_period = ''
    try:
        prereq_row = db.session.execute(
            text(
                f'SELECT "Prerequisite", "Day_and_Period" FROM "{table_name}" '
                'WHERE "Course_Number" = :cn LIMIT 1'
            ),
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

    # --- Conflict enforcement ---
    try:
        existing_rows = db.session.execute(
            text(
                f'''
                SELECT c."Day_and_Period"
                FROM schedule_courses sc
                JOIN "{table_name}" c ON sc.course_number = c."Course_Number"
                WHERE sc.schedule_id = :sid
                '''
            ),
            {'sid': sched.id},
        ).fetchall()
        occupied_blocks = set()
        for r in existing_rows:
            occupied_blocks |= parse_day_period_blocks(r[0])

        candidate_blocks = parse_day_period_blocks(candidate_day_period)
        if has_conflict(occupied_blocks, candidate_blocks):
            return (
                jsonify(
                    {
                        'success': False,
                        'error': f'Schedule conflict for {course_num}',
                        'message': f'Schedule conflict for {course_num}'
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

    return jsonify({'success': True})

@student_bp.route('/api/remove_course/<int:entry_id>', methods=['DELETE', 'POST'])
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


@student_bp.route('/api/recommendations', methods=['GET'])
def api_recommendations():
    sched_id = request.args.get('schedule_id')
    try:
        limit = int(request.args.get('limit', 10))
    except Exception:
        limit = 10

    if not sched_id:
        return jsonify({'success': False, 'error': 'Missing schedule_id', 'message': 'Missing schedule_id'}), 400
    try:
        sched_id_int = int(sched_id)
    except Exception:
        return jsonify({'success': False, 'error': 'Invalid schedule_id', 'message': 'Invalid schedule_id'}), 400

    if current_user.is_authenticated:
        sched = Schedule.query.filter_by(id=sched_id_int, user_id=current_user.id).first()
    else:
        sched = Schedule.query.filter_by(id=sched_id_int, session_id=session.get('guest_id')).first()
    if not sched:
        return jsonify({'success': False, 'error': 'Unauthorized', 'message': 'Unauthorized'}), 403

    table_name, _ = get_best_course_table(sched.year, sched.semester)
    if not table_name or not is_valid_table_name(table_name):
        return jsonify({'success': False, 'error': 'Catalog unavailable', 'message': 'Catalog unavailable'}), 400

    try:
        rows = db.session.execute(
            text(
                f'''
                SELECT "Course_Number", "Course_Title", "Credits", "Instructor", "Day_and_Period", "Area_of_Study", "Prerequisite"
                FROM "{table_name}"
                '''
            )
        ).fetchall()
        catalog = [dict(r._mapping) for r in rows]
    except Exception as e:
        db.session.rollback()
        print(f"Error loading catalog for recommendations from {table_name}: {e}")
        return jsonify({'success': False, 'error': 'Failed to load catalog', 'message': 'Failed to load catalog'}), 400

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
            print(f"Error fetching completed courses for recommendations (user {current_user.id}): {e}")
            return jsonify({'success': False, 'error': 'Unable to validate prerequisites', 'message': 'Unable to validate prerequisites'}), 400

    # Avoid recommending courses already in the schedule.
    try:
        in_sched_rows = db.session.execute(
            text('SELECT course_number FROM schedule_courses WHERE schedule_id = :sid'),
            {'sid': sched.id},
        ).fetchall()
        in_sched = {r[0] for r in in_sched_rows}
        catalog = [c for c in catalog if c.get('Course_Number') not in in_sched]
    except Exception as e:
        db.session.rollback()
        print(f"Error checking existing schedule courses for recommendations: {e}")

    recs, errors = recommend_courses(catalog, completed_passing, limit=limit)
    for err in errors:
        print(f"Recommendation prereq parse error: {err}")
    return jsonify({'success': True, 'recommendations': recs})


@student_bp.route('/api/generate_schedule', methods=['POST'])
def api_generate_schedule():
    data = request.get_json(silent=True) or {}
    sched_id = data.get('schedule_id')
    target_count = data.get('target_count', 5)

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

    if current_user.is_authenticated:
        sched = Schedule.query.filter_by(id=sched_id_int, user_id=current_user.id).first()
    else:
        sched = Schedule.query.filter_by(id=sched_id_int, session_id=session.get('guest_id')).first()
    if not sched:
        return jsonify({'success': False, 'error': 'Unauthorized', 'message': 'Unauthorized'}), 403

    table_name, _ = get_best_course_table(sched.year, sched.semester)
    if not table_name or not is_valid_table_name(table_name):
        return jsonify({'success': False, 'error': 'Catalog unavailable', 'message': 'Catalog unavailable'}), 400

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
            print(f"Error fetching completed courses for generator (user {current_user.id}): {e}")
            return jsonify({'success': False, 'error': 'Unable to validate prerequisites', 'message': 'Unable to validate prerequisites'}), 400

    try:
        existing_dp_rows = db.session.execute(
            text(
                f'''
                SELECT c."Day_and_Period"
                FROM schedule_courses sc
                JOIN "{table_name}" c ON sc.course_number = c."Course_Number"
                WHERE sc.schedule_id = :sid
                '''
            ),
            {'sid': sched.id},
        ).fetchall()
        existing_day_period = [r[0] for r in existing_dp_rows]
    except Exception as e:
        db.session.rollback()
        print(f"Error loading existing day/period for generator: {e}")
        existing_day_period = []

    try:
        rows = db.session.execute(
            text(
                f'''
                SELECT "Course_Number", "Course_Title", "Credits", "Instructor", "Day_and_Period", "Area_of_Study", "Prerequisite"
                FROM "{table_name}"
                '''
            )
        ).fetchall()
        catalog = [dict(r._mapping) for r in rows]
    except Exception as e:
        db.session.rollback()
        print(f"Error loading catalog for generator from {table_name}: {e}")
        return jsonify({'success': False, 'error': 'Failed to load catalog', 'message': 'Failed to load catalog'}), 400

    generated, errors = generate_schedule_courses(
        catalog,
        completed_passing,
        target_count=target_count,
        existing_day_and_period=existing_day_period,
    )
    for err in errors:
        print(f"Generator prereq parse error: {err}")

    return jsonify({'success': True, 'courses': generated})