# Routes/admin_routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
import os
import csv
import sqlite3
import re
from functools import wraps
from werkzeug.utils import secure_filename
from config import ADMIN_USERNAME, ADMIN_PASSWORD
from utils import get_db_connection, get_course_data_tables, get_app_setting, update_app_setting, calculate_gpa

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_logged_in' not in session:
            return redirect(url_for('admin.login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('username') == ADMIN_USERNAME and request.form.get('password') == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            flash('Login successful!', 'success')
            return redirect(url_for('admin.index'))
        else:
            flash('Invalid credentials.', 'error')
    return render_template('Admin/admin_login.html')

@admin_bp.route('/logout')
def logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin.login'))

@admin_bp.route('/')
@admin_required
def index():
    conn = get_db_connection()
    user_count = conn.execute('SELECT COUNT(id) FROM users').fetchone()[0]
    schedule_count = conn.execute('SELECT COUNT(id) FROM schedules').fetchone()[0]
    course_tables = get_course_data_tables()
    
    all_schedules = conn.execute('''
        SELECT s.schedule_name, s.year, s.semester, s.schedule_type, u.username
        FROM schedules s
        JOIN users u ON s.user_id = u.id
        ORDER BY s.id DESC
    ''').fetchall()
    
    conn.close()
    return render_template('Admin/admin_dashboard.html', 
                           user_count=user_count, 
                           schedule_count=schedule_count,
                           course_tables=course_tables,
                           all_schedules=[dict(row) for row in all_schedules],
                           current_year_setting=get_app_setting('current_year'), 
                           current_semester_setting=get_app_setting('current_semester'),
                           graduation_credits_setting=get_app_setting('graduation_credits'))

@admin_bp.route('/datasets')
@admin_required
def manage_datasets():
    tables = get_course_data_tables()
    return render_template('Admin/manage_datasets.html', tables=tables)

@admin_bp.route('/datasets/upload', methods=['POST'])
@admin_required
def upload_dataset():
    if 'file' not in request.files:
        flash('No file part', 'error')
        return redirect(url_for('admin.manage_datasets'))
    
    file = request.files['file']
    if file.filename == '' or not file.filename.endswith('.csv'):
        flash('Invalid file selection. Must be CSV.', 'error')
        return redirect(url_for('admin.manage_datasets'))

    filename = secure_filename(file.filename)
    table_name = filename.replace('.csv', '')
    upload_path = os.path.join('uploads', filename)
    
    if not os.path.exists('uploads'): os.makedirs('uploads')
    file.save(upload_path)

    conn = get_db_connection()
    try:
        # FIXED: Added Prerequisite to the schema!
        conn.execute(f'''
            CREATE TABLE IF NOT EXISTS "{table_name}" (
                "Course_Number" TEXT PRIMARY KEY, 
                "Course_Title" TEXT, 
                "Credits" INTEGER,
                "Instructor" TEXT, 
                "Day_and_Period" TEXT, 
                "Area_of_Study" TEXT,
                "Year_Available" TEXT,
                "Prerequisite" TEXT,
                "subject_area_id" INTEGER, 
                "Semester" TEXT
            )
        ''')
        
        with open(upload_path, mode='r', encoding='utf-8-sig', errors='replace') as f:
            reader = csv.reader(f)
            raw_headers = next(reader)
            
            def clean_header(h): return re.sub(r'[^a-z0-9]', '', str(h).lower())
            headers = [clean_header(h) for h in raw_headers]
            
            inserted_count = 0
            seen_course_nums = set()
            
            for row in reader:
                if not row or not any(row): continue 
                row_dict = dict(zip(headers, row))
                
                course_num = row_dict.get('coursenumber', row_dict.get('courseid', row_dict.get('code', ''))).strip()
                title = row_dict.get('coursetitle', row_dict.get('title', row_dict.get('name', ''))).strip()
                credits = row_dict.get('credits', row_dict.get('credit', '0'))
                instructor = row_dict.get('courseinstructor', row_dict.get('instructor', row_dict.get('teacher', ''))).strip()
                day_period = row_dict.get('dayandperiod', row_dict.get('schedule', row_dict.get('time', ''))).strip()
                semester = row_dict.get('semester', row_dict.get('term', '')).strip()
                area_of_study = row_dict.get('areaofstudy', row_dict.get('subjectarea', row_dict.get('faculty', row_dict.get('subject', '')))).strip()
                year_available = row_dict.get('yearavailable', row_dict.get('targetyear', row_dict.get('year', ''))).strip()
                
                # FIXED: Fuzzy match for Prerequisite
                prerequisite = row_dict.get('prerequisite', row_dict.get('prereq', row_dict.get('prerequisites', ''))).strip()
                
                if course_num:
                    sec_match = re.search(r'Sec\s*(\d+)', title, re.IGNORECASE)
                    sec_str = f"-S{sec_match.group(1)}" if sec_match else ""
                    sem_str = "-Sp" if "spring" in semester.lower() else "-Fa" if "fall" in semester.lower() else ""
                    
                    base_unique = f"{course_num}{sec_str}{sem_str}"
                    unique_course_num = base_unique
                    counter = 1
                    while unique_course_num in seen_course_nums:
                        unique_course_num = f"{base_unique}-V{counter}"
                        counter += 1
                    seen_course_nums.add(unique_course_num)

                    conn.execute(f'INSERT OR REPLACE INTO "{table_name}" VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', 
                        (unique_course_num, title, credits, instructor, day_period, area_of_study, year_available, prerequisite, None, semester))
                    inserted_count += 1
        conn.commit()
    except Exception as e:
        flash(f'Upload failed: {e}', 'error')
    finally:
        conn.close()
    return redirect(url_for('admin.manage_datasets'))

@admin_bp.route('/datasets/delete/<table_name>', methods=['POST'])
@admin_required
def delete_dataset(table_name):
    conn = get_db_connection()
    try:
        conn.execute(f'DROP TABLE "{table_name}"')
        conn.commit()
        flash(f'Dataset "{table_name}" deleted.', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'error')
    finally:
        conn.close()
    return redirect(url_for('admin.manage_datasets'))

@admin_bp.route('/update_app_settings', methods=['POST'])
@admin_required
def update_app_settings():
    update_app_setting('current_year', request.form.get('current_year_setting'))
    update_app_setting('current_semester', request.form.get('current_semester_setting'))
    update_app_setting('graduation_credits', request.form.get('graduation_credits_setting'))
    flash("System settings updated.", "success")
    return redirect(url_for('admin.index'))

@admin_bp.route('/periods')
@admin_required
def manage_periods():
    conn = get_db_connection()
    periods = conn.execute("SELECT * FROM periods ORDER BY name ASC").fetchall()
    conn.close()
    return render_template('Admin/periods.html', periods=periods)

@admin_bp.route('/users')
@admin_required
def manage_users():
    conn = get_db_connection()
    users = conn.execute('SELECT id, gender, year_of_study FROM users ORDER BY id ASC').fetchall()
    conn.close()
    return render_template('Admin/manage_users.html', users=users)

@admin_bp.route('/api/update_user_demographics', methods=['POST'])
@admin_required
def update_user_demographics():
    data = request.get_json()
    conn = get_db_connection()
    try:
        conn.execute('UPDATE users SET gender = ?, year_of_study = ? WHERE id = ?', 
                     (data.get('gender'), data.get('year_of_study'), data.get('id')))
        conn.commit()
        return jsonify({'success': True})
    except sqlite3.Error as e:
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        conn.close()

@admin_bp.route('/api/delete_user/<int:user_id>', methods=['POST'])
@admin_required
def delete_user(user_id):
    conn = get_db_connection()
    try:
        schedules = conn.execute('SELECT id FROM schedules WHERE user_id = ?', (user_id,)).fetchall()
        for s in schedules:
            conn.execute('DELETE FROM schedule_courses WHERE schedule_id = ?', (s['id'],))
        conn.execute('DELETE FROM schedules WHERE user_id = ?', (user_id,))
        conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
        conn.commit()
        return jsonify({'success': True})
    except sqlite3.Error as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        conn.close()

@admin_bp.route('/subjects', methods=['GET', 'POST'])
@admin_required
def manage_subjects():
    conn = get_db_connection()
    if request.method == 'POST':
        subject_id = request.form.get('subject_id')
        new_benchmark = request.form.get('min_depth_credits')
        conn.execute('UPDATE subject_areas SET min_depth_credits = ? WHERE id = ?', (new_benchmark, subject_id))
        conn.commit()
        flash('Subject benchmark updated.', 'success')
    subjects = conn.execute("SELECT id, name, is_major, min_depth_credits FROM subject_areas ORDER BY name").fetchall()
    conn.close()
    return render_template('Admin/subjects.html', subjects=subjects)

@admin_bp.route('/instructors')
@admin_required
def manage_instructors():
    conn = get_db_connection()
    instructors = conn.execute("SELECT id, name FROM instructors ORDER BY name").fetchall()
    conn.close()
    return render_template('Admin/instructors.html', instructors=instructors)

@admin_bp.route('/api/update_instructor/<int:instructor_id>', methods=['POST'])
@admin_required
def api_update_instructor(instructor_id):
    data = request.get_json()
    conn = get_db_connection()
    try:
        conn.execute("UPDATE instructors SET name = ? WHERE id = ?", (data.get('name'), instructor_id))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        conn.close()

@admin_bp.route('/courses')
@admin_required
def manage_courses():
    conn = get_db_connection()
    course_tables = get_course_data_tables()
    if not course_tables:
        conn.close()
        return render_template('Admin/courses.html', courses=[])
    union_parts = [f'SELECT \'{t}\' as source_table, rowid as _rowid, "Course_Number", "Course_Title", "Credits", "Instructor", "Day_and_Period", "Prerequisite" FROM "{t}"' for t in course_tables]
    full_query = " UNION ALL ".join(union_parts)
    courses = conn.execute(f"SELECT * FROM ({full_query})").fetchall()
    conn.close()
    return render_template('Admin/courses.html', courses=courses)

@admin_bp.route('/api/update_course', methods=['POST'])
@admin_required
def api_update_course():
    data = request.get_json()
    conn = get_db_connection()
    try:
        conn.execute(f'UPDATE "{data.get("source_table")}" SET Course_Title = ?, Credits = ? WHERE rowid = ?', 
                     (data.get('Course_Title'), data.get('Credits'), data.get('_rowid')))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        conn.close()

@admin_bp.route('/api/update_period', methods=['POST'])
@admin_required
def update_period():
    data = request.get_json()
    conn = get_db_connection()
    try:
        conn.execute('UPDATE periods SET start_time = ?, end_time = ? WHERE id = ?',
                     (data['start_time'], data['end_time'], data['id']))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        conn.close()

# ==========================================
# DEEP DATA ANALYTICS & COMPARISON TOOL
# ==========================================
@admin_bp.route('/analytics')
@admin_required
def analytics():
    conn = get_db_connection()
    tables = get_course_data_tables()
    
    total_users = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    total_schedules = conn.execute('SELECT COUNT(*) FROM schedules').fetchone()[0]

    user_data = conn.execute('SELECT id, gender, year_of_study FROM users').fetchall()
    gpa_by_gender = {"Male": [], "Female": [], "Other/Prefer not to say": []}
    gpa_by_year = {"1": [], "2": [], "3": [], "4": []}
    overall_gpas = []

    for user in user_data:
        try:
            courses = conn.execute('''
                SELECT sc.grade, c.Credits 
                FROM schedule_courses sc 
                JOIN (SELECT Course_Number, Credits FROM "2024_All_Courses" UNION SELECT Course_Number, Credits FROM "2025_All_Courses") c 
                ON sc.course_number = c.Course_Number 
                WHERE sc.grade IS NOT NULL AND sc.grade != "" 
                AND sc.schedule_id IN (SELECT id FROM schedules WHERE user_id = ?)
            ''', (user['id'],)).fetchall()
            
            if courses:
                gpa = calculate_gpa([dict(row) for row in courses])
                overall_gpas.append(gpa)
                
                gen = user['gender'] if user['gender'] in gpa_by_gender else "Other/Prefer not to say"
                gpa_by_gender[gen].append(gpa)
                
                yr = str(user['year_of_study'])
                if yr in gpa_by_year: gpa_by_year[yr].append(gpa)
        except Exception:
            pass 

    avg_gpa_overall = sum(overall_gpas)/len(overall_gpas) if overall_gpas else 0
    gender_gpa_stats = {k: (sum(v)/len(v) if v else 0) for k, v in gpa_by_gender.items()}
    year_gpa_stats = {k: (sum(v)/len(v) if v else 0) for k, v in gpa_by_year.items()}

    top_courses = []
    top_areas = []
    top_faculty = []
    day_counts = {"Mon": 0, "Tue": 0, "Wed": 0, "Thu": 0, "Fri": 0}
    period_counts = {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0, "6": 0}

    if tables:
        table_union = " UNION ".join([f'SELECT Course_Number, Course_Title, Area_of_Study, Instructor, Day_and_Period FROM "{t}"' for t in tables])
        
        top_courses = conn.execute(f'''
            SELECT m.Course_Title, COUNT(sc.id) as count 
            FROM schedule_courses sc
            JOIN ({table_union}) m ON sc.course_number = m.Course_Number
            GROUP BY m.Course_Title ORDER BY count DESC LIMIT 10
        ''').fetchall()

        top_areas = conn.execute(f'''
            SELECT m.Area_of_Study, COUNT(sc.id) as count 
            FROM schedule_courses sc
            JOIN ({table_union}) m ON sc.course_number = m.Course_Number
            WHERE m.Area_of_Study != "" AND m.Area_of_Study IS NOT NULL
            GROUP BY m.Area_of_Study ORDER BY count DESC LIMIT 10
        ''').fetchall()

        top_faculty = conn.execute(f'''
            SELECT m.Instructor, COUNT(sc.id) as count 
            FROM schedule_courses sc
            JOIN ({table_union}) m ON sc.course_number = m.Course_Number
            WHERE m.Instructor != "" AND m.Instructor IS NOT NULL
            GROUP BY m.Instructor ORDER BY count DESC LIMIT 10
        ''').fetchall()

        all_time_slots = conn.execute(f'''
            SELECT m.Day_and_Period 
            FROM schedule_courses sc
            JOIN ({table_union}) m ON sc.course_number = m.Course_Number
        ''').fetchall()

        for row in all_time_slots:
            ts = row['Day_and_Period']
            if ts:
                for day in day_counts.keys():
                    if day in ts: day_counts[day] += 1
                nums = re.findall(r'\d', ts)
                for n in nums:
                    if n in period_counts: period_counts[n] += 1

    conn.close()
    return render_template('Admin/admin_analytics.html', 
                           total_users=total_users, 
                           total_schedules=total_schedules,
                           avg_gpa=round(avg_gpa_overall, 2),
                           gender_gpa=gender_gpa_stats,
                           year_gpa=year_gpa_stats,
                           top_courses=[dict(row) for row in top_courses],
                           top_areas=[dict(row) for row in top_areas],
                           top_faculty=[dict(row) for row in top_faculty],
                           day_counts=day_counts,
                           period_counts=period_counts)