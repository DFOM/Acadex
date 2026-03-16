# Routes/admin_routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
import os
import csv
import re
import pandas as pd
from functools import wraps
from werkzeug.utils import secure_filename
from sqlalchemy import inspect, text
from models import db, User, Schedule, SubjectArea, Period, Setting
from utils import get_course_data_tables, calculate_gpa

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

# --- SECURITY WRAPPER ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash("Unauthorized access. Admin privileges required.", "error")
            return redirect(url_for('auth.login', next=request.url))
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
    course_tables = get_course_data_tables()
    
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
                           course_tables=course_tables,
                           all_schedules=[dict(row._mapping) for row in all_schedules],
                           current_year_setting=get_app_setting('current_year', '2025'), 
                           current_semester_setting=get_app_setting('current_semester', 'Spring'),
                           graduation_credits_setting=get_app_setting('graduation_credits', '124'))

@admin_bp.route('/datasets')
@login_required
@admin_required
def manage_datasets():
    tables = get_course_data_tables()
    return render_template('Admin/manage_datasets.html', tables=tables)

@admin_bp.route('/datasets/upload', methods=['POST'])
@login_required
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

    try:
        # 1. Read the CSV purely in Python to apply your custom logic
        processed_rows = []
        seen_course_nums = set()

        with open(upload_path, mode='r', encoding='utf-8-sig', errors='replace') as f:
            reader = csv.reader(f)
            raw_headers = next(reader)
            
            def clean_header(h): return re.sub(r'[^a-z0-9]', '', str(h).lower())
            headers = [clean_header(h) for h in raw_headers]
            
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

                    processed_rows.append({
                        "Course_Number": unique_course_num,
                        "Course_Title": title,
                        "Credits": credits,
                        "Instructor": instructor,
                        "Day_and_Period": day_period,
                        "Area_of_Study": area_of_study,
                        "Year_Available": year_available,
                        "Prerequisite": prerequisite,
                        "subject_area_id": None,
                        "Semester": semester
                    })

        # 2. Convert to DataFrame and push to PostgreSQL/SQLite safely using SQLAlchemy
        df = pd.DataFrame(processed_rows)
        df.to_sql(table_name, con=db.engine, if_exists='replace', index=False)
        
        flash(f'Dataset "{table_name}" uploaded and parsed successfully!', 'success')
    except Exception as e:
        flash(f'Upload failed: {e}', 'error')

    return redirect(url_for('admin.manage_datasets'))

@admin_bp.route('/datasets/delete/<table_name>', methods=['POST'])
@login_required
@admin_required
def delete_dataset(table_name):
    try:
        db.session.execute(text(f'DROP TABLE "{table_name}"'))
        db.session.commit()
        flash(f'Dataset "{table_name}" deleted.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {e}', 'error')
    return redirect(url_for('admin.manage_datasets'))

@admin_bp.route('/update_app_settings', methods=['POST'])
@login_required
@admin_required
def update_app_settings_route():
    update_app_setting('current_year', request.form.get('current_year_setting'))
    update_app_setting('current_semester', request.form.get('current_semester_setting'))
    update_app_setting('graduation_credits', request.form.get('graduation_credits_setting'))
    flash("System settings updated.", "success")
    return redirect(url_for('admin.index'))

@admin_bp.route('/periods')
@login_required
@admin_required
def manage_periods():
    periods = Period.query.order_by(Period.name.asc()).all()
    return render_template('Admin/periods.html', periods=periods)

@admin_bp.route('/users')
@login_required
@admin_required
def manage_users():
    users = db.session.execute(text('SELECT id, gender, year_of_study, username FROM users ORDER BY id ASC')).fetchall()
    return render_template('Admin/manage_users.html', users=[dict(row._mapping) for row in users])

@admin_bp.route('/api/update_user_demographics', methods=['POST'])
@login_required
@admin_required
def update_user_demographics():
    data = request.get_json()
    try:
        db.session.execute(text('UPDATE users SET gender = :g, year_of_study = :y WHERE id = :id'), 
                     {'g': data.get('gender'), 'y': data.get('year_of_study'), 'id': data.get('id')})
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@admin_bp.route('/api/delete_user/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    try:
        user = User.query.get(user_id)
        if user:
            db.session.delete(user)
            db.session.commit()
            return jsonify({'success': True})
        return jsonify({'success': False, 'message': 'User not found'}), 404
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@admin_bp.route('/subjects', methods=['GET', 'POST'])
@login_required
@admin_required
def manage_subjects():
    if request.method == 'POST':
        subject_id = request.form.get('subject_id')
        new_benchmark = request.form.get('min_depth_credits')
        try:
            db.session.execute(text('UPDATE subject_areas SET min_depth_credits = :val WHERE id = :id'), 
                               {'val': new_benchmark, 'id': subject_id})
            db.session.commit()
            flash('Subject benchmark updated.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating benchmark: {e}', 'error')
            
    subjects = SubjectArea.query.order_by(SubjectArea.name).all()
    return render_template('Admin/subjects.html', subjects=subjects)

@admin_bp.route('/instructors')
@login_required
@admin_required
def manage_instructors():
    try:
        instructors = db.session.execute(text("SELECT id, name FROM instructors ORDER BY name")).fetchall()
        return render_template('Admin/instructors.html', instructors=[dict(row._mapping) for row in instructors])
    except:
        return render_template('Admin/instructors.html', instructors=[])

@admin_bp.route('/api/update_instructor/<int:instructor_id>', methods=['POST'])
@login_required
@admin_required
def api_update_instructor(instructor_id):
    data = request.get_json()
    try:
        db.session.execute(text("UPDATE instructors SET name = :name WHERE id = :id"), 
                           {'name': data.get('name'), 'id': instructor_id})
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@admin_bp.route('/courses')
@login_required
@admin_required
def manage_courses():
    course_tables = get_course_data_tables()
    if not course_tables:
        return render_template('Admin/courses.html', courses=[])
    
    # FIXED: Replaced SQLite-only 'rowid' with universally unique 'Course_Number' as _rowid
    union_parts = [f'SELECT \'{t}\' as source_table, "Course_Number" as _rowid, "Course_Number", "Course_Title", "Credits", "Instructor", "Day_and_Period", "Prerequisite" FROM "{t}"' for t in course_tables]
    full_query = " UNION ALL ".join(union_parts)
    
    courses = db.session.execute(text(f"SELECT * FROM ({full_query})")).fetchall()
    return render_template('Admin/courses.html', courses=[dict(row._mapping) for row in courses])

@admin_bp.route('/api/update_course', methods=['POST'])
@login_required
@admin_required
def api_update_course():
    data = request.get_json()
    try:
        table = data.get("source_table")
        if table in get_course_data_tables():
            # FIXED: Updating based on Course_Number instead of rowid
            db.session.execute(text(f'UPDATE "{table}" SET "Course_Title" = :title, "Credits" = :creds WHERE "Course_Number" = :rid'), 
                        {'title': data.get('Course_Title'), 'creds': data.get('Credits'), 'rid': data.get('_rowid')})
            db.session.commit()
            return jsonify({'success': True})
        return jsonify({'success': False, 'message': 'Invalid table'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@admin_bp.route('/api/update_period', methods=['POST'])
@login_required
@admin_required
def update_period():
    data = request.get_json()
    try:
        period = Period.query.get(data['id'])
        if period:
            period.start_time = data['start_time']
            period.end_time = data['end_time']
            db.session.commit()
            return jsonify({'success': True})
        return jsonify({'success': False, 'message': 'Period not found'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

# ==========================================
# DEEP DATA ANALYTICS & COMPARISON TOOL
# ==========================================
@admin_bp.route('/analytics')
@login_required
@admin_required
def analytics():
    tables = get_course_data_tables()
    
    total_users = User.query.count()
    total_schedules = Schedule.query.count()

    user_data = db.session.execute(text('SELECT id, gender, year_of_study FROM users')).fetchall()
    gpa_by_gender = {"Male": [], "Female": [], "Other/Prefer not to say": []}
    gpa_by_year = {"1": [], "2": [], "3": [], "4": []}
    overall_gpas = []

    for user in user_data:
        try:
            if tables:
                c_union = " UNION ".join([f'SELECT Course_Number, Credits FROM "{t}"' for t in tables])
                courses = db.session.execute(text(f'''
                    SELECT sc.grade, c.Credits 
                    FROM schedule_courses sc 
                    JOIN ({c_union}) c 
                    ON sc.course_number = c.Course_Number 
                    WHERE sc.grade IS NOT NULL AND sc.grade != "" 
                    AND sc.schedule_id IN (SELECT id FROM schedules WHERE user_id = :uid)
                '''), {'uid': user.id}).fetchall()
                
                if courses:
                    gpa = calculate_gpa([dict(row._mapping) for row in courses])
                    overall_gpas.append(gpa)
                    
                    gen = user.gender if getattr(user, 'gender', None) in gpa_by_gender else "Other/Prefer not to say"
                    gpa_by_gender[gen].append(gpa)
                    
                    yr = str(getattr(user, 'year_of_study', ''))
                    if yr in gpa_by_year: gpa_by_year[yr].append(gpa)
        except Exception as e:
            print(f"Analytics error for user {user.id}: {e}")
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
        
        top_courses = db.session.execute(text(f'''
            SELECT m.Course_Title, COUNT(sc.id) as count 
            FROM schedule_courses sc
            JOIN ({table_union}) m ON sc.course_number = m.Course_Number
            GROUP BY m.Course_Title ORDER BY count DESC LIMIT 10
        ''')).fetchall()

        top_areas = db.session.execute(text(f'''
            SELECT m.Area_of_Study, COUNT(sc.id) as count 
            FROM schedule_courses sc
            JOIN ({table_union}) m ON sc.course_number = m.Course_Number
            WHERE m.Area_of_Study != "" AND m.Area_of_Study IS NOT NULL
            GROUP BY m.Area_of_Study ORDER BY count DESC LIMIT 10
        ''')).fetchall()

        top_faculty = db.session.execute(text(f'''
            SELECT m.Instructor, COUNT(sc.id) as count 
            FROM schedule_courses sc
            JOIN ({table_union}) m ON sc.course_number = m.Course_Number
            WHERE m.Instructor != "" AND m.Instructor IS NOT NULL
            GROUP BY m.Instructor ORDER BY count DESC LIMIT 10
        ''')).fetchall()

        all_time_slots = db.session.execute(text(f'''
            SELECT m.Day_and_Period 
            FROM schedule_courses sc
            JOIN ({table_union}) m ON sc.course_number = m.Course_Number
        ''')).fetchall()

        for row in all_time_slots:
            ts = row.Day_and_Period
            if ts:
                for day in day_counts.keys():
                    if day in ts: day_counts[day] += 1
                nums = re.findall(r'\d', ts)
                for n in nums:
                    if n in period_counts: period_counts[n] += 1

    return render_template('Admin/admin_analytics.html', 
                           total_users=total_users, 
                           total_schedules=total_schedules,
                           avg_gpa=round(avg_gpa_overall, 2),
                           gender_gpa=gender_gpa_stats,
                           year_gpa=year_gpa_stats,
                           top_courses=[dict(row._mapping) for row in top_courses],
                           top_areas=[dict(row._mapping) for row in top_areas],
                           top_faculty=[dict(row._mapping) for row in top_faculty],
                           day_counts=day_counts,
                           period_counts=period_counts)