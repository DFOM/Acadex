# Routes/student_routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from utils import get_db_connection, get_course_data_tables, get_best_course_table, get_app_setting
import re

student_bp = Blueprint('student', __name__, url_prefix='/student')

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

        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO schedules (user_id, schedule_name, year, semester, schedule_type)
                VALUES (?, ?, ?, ?, ?)
            ''', (current_user.id, schedule_name, year, semester, schedule_type))
            conn.commit()
            new_schedule_id = cursor.lastrowid
            flash(f"Schedule '{schedule_name}' created successfully!", "success")
            return redirect(url_for('student.edit_schedule', schedule_id=new_schedule_id))
        except Exception as e:
            conn.rollback()
            flash(f"Database error: {e}", "error")
            return redirect(url_for('auth.choose_schedule_type'))
        finally:
            conn.close()

    schedule_type = request.args.get('type', 'future')
    
    # 1. Fetch current year from settings
    current_year = int(get_app_setting('current_year') or 2025)
    
    # 2. Extract available years from dataset names
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
    conn = get_db_connection()
    schedule = conn.execute('SELECT * FROM schedules WHERE id = ? AND user_id = ?',
                            (schedule_id, current_user.id)).fetchone()

    if not schedule:
        conn.close()
        flash("Plan not found or you do not have permission to view it.", "error")
        return redirect(url_for('schedule.user_dashboard'))

    target_table, needs_filter = get_best_course_table(schedule['year'], schedule['semester'])

    current_courses = []
    if target_table:
        sql = f'''
            SELECT sc.id as entry_id, sc.grade, c.*
            FROM schedule_courses sc
            JOIN "{target_table}" c ON sc.course_number = c.Course_Number
            WHERE sc.schedule_id = ?
        '''
        params = [schedule_id]

        if needs_filter:
            sql += " AND c.Semester LIKE ?"
            params.append(f"%{schedule['semester']}%")

        try:
            rows = conn.execute(sql, params).fetchall()
            current_courses = [dict(row) for row in rows]
        except Exception as e:
            print(f"Error fetching current courses: {e}")

    obtained_courses = []
    try:
        past_courses = conn.execute('''
            SELECT sc.course_number
            FROM schedule_courses sc
            JOIN schedules s ON sc.schedule_id = s.id
            WHERE s.user_id = ? AND s.schedule_type = 'past'
              AND sc.grade IS NOT NULL AND sc.grade != '' AND sc.grade != 'F'
        ''', (current_user.id,)).fetchall()

        for row in past_courses:
            base_code = re.sub(r'-S\d+-.*', '', row['course_number']).strip()
            obtained_courses.append(base_code)
    except Exception as e:
        print(f"Error fetching obtained courses: {e}")

    periods = conn.execute("SELECT * FROM periods ORDER BY name ASC").fetchall()
    conn.close()

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

    conn = get_db_connection()
    schedule = conn.execute('SELECT year, semester FROM schedules WHERE id = ? AND user_id = ?',
                            (schedule_id, current_user.id)).fetchone()

    if not schedule:
        conn.close()
        return jsonify([])

    target_table, needs_filter = get_best_course_table(schedule['year'], schedule['semester'])

    courses = []
    if target_table:
        sql = f'''
            SELECT c.*
            FROM "{target_table}" c
            WHERE 1=1
        '''
        params = []

        if query:
            sql += " AND (c.Course_Title LIKE ? OR c.Course_Number LIKE ?)"
            params.extend([f'%{query}%', f'%{query}%'])

        if needs_filter:
            sql += " AND c.Semester LIKE ?"
            params.append(f"%{schedule['semester']}%")

        sql += " LIMIT 250"

        try:
            rows = conn.execute(sql, params).fetchall()
            courses = [dict(row) for row in rows]
        except Exception as e:
            print(f"Search Error: {e}")

    conn.close()
    return jsonify(courses)

@student_bp.route('/api/add_course', methods=['POST'])
@login_required
def api_add_course():
    data = request.get_json()
    schedule_id = data.get('schedule_id')
    course_number = data.get('course_number')

    if not schedule_id or not course_number:
        return jsonify({'success': False, 'message': 'Missing data.'}), 400

    conn = get_db_connection()
    try:
        schedule = conn.execute('SELECT id FROM schedules WHERE id = ? AND user_id = ?',
                                (schedule_id, current_user.id)).fetchone()
        if not schedule:
            return jsonify({'success': False, 'message': 'Unauthorized access.'}), 403

        existing = conn.execute('SELECT id FROM schedule_courses WHERE schedule_id = ? AND course_number = ?',
                                (schedule_id, course_number)).fetchone()
        if existing:
            return jsonify({'success': False, 'message': 'Course is already in your plan.'}), 400

        conn.execute('INSERT INTO schedule_courses (schedule_id, course_number) VALUES (?, ?)',
                     (schedule_id, course_number))
        conn.commit()
        return jsonify({'success': True})

    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        conn.close()

@student_bp.route('/api/remove_course/<int:entry_id>', methods=['POST'])
@login_required
def api_remove_course(entry_id):
    conn = get_db_connection()
    try:
        entry = conn.execute('''
            SELECT sc.id
            FROM schedule_courses sc
            JOIN schedules s ON sc.schedule_id = s.id
            WHERE sc.id = ? AND s.user_id = ?
        ''', (entry_id, current_user.id)).fetchone()

        if not entry:
            return jsonify({'success': False, 'message': 'Unauthorized or course not found.'}), 403

        conn.execute('DELETE FROM schedule_courses WHERE id = ?', (entry_id,))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        conn.close()

# NEW: API to save grades when the student selects them from the dropdown!
@student_bp.route('/api/update_grade', methods=['POST'])
@login_required
def api_update_grade():
    data = request.get_json()
    entry_id = data.get('entry_id')
    grade = data.get('grade')

    conn = get_db_connection()
    try:
        entry = conn.execute('''
            SELECT sc.id FROM schedule_courses sc
            JOIN schedules s ON sc.schedule_id = s.id
            WHERE sc.id = ? AND s.user_id = ?
        ''', (entry_id, current_user.id)).fetchone()

        if not entry:
            return jsonify({'success': False, 'message': 'Unauthorized access.'}), 403

        conn.execute('UPDATE schedule_courses SET grade = ? WHERE id = ?', (grade, entry_id))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        conn.close()