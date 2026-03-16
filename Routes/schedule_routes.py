# Routes/schedule_routes.py
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
import sqlite3
import re
from utils import get_db_connection, get_course_data_tables, calculate_gpa, get_app_setting

schedule_bp = Blueprint('schedule', __name__)

@schedule_bp.route('/dashboard')
@login_required
def user_dashboard():
    conn = get_db_connection()

    # 1. Fetch user's schedules
    schedules_raw = conn.execute('''
        SELECT * FROM schedules
        WHERE user_id = ?
        ORDER BY year DESC, semester DESC
    ''', (current_user.id,)).fetchall()

    tables = get_course_data_tables()

    schedules_data = []
    past_courses = []
    future_courses = []
    area_credits = {}

    if tables:
        table_union = " UNION ".join([f'SELECT "Course_Number", "Credits", "Area_of_Study" FROM "{t}"' for t in tables])
        
        # --- Python-based Deduplication to prevent overcounting sections and multiple years ---
        raw_catalog_courses = conn.execute(f'''
            SELECT "Course_Number", "Credits", "Area_of_Study"
            FROM ({table_union})
            WHERE "Area_of_Study" IS NOT NULL AND "Area_of_Study" != ''
        ''').fetchall()

        unique_courses_per_area = {}

        for row in raw_catalog_courses:
            area = row['Area_of_Study'].strip()
            raw_code = row['Course_Number']
            try:
                credits = float(row['Credits'] or 0)
            except (ValueError, TypeError):
                credits = 0.0

            # Strip section (-S1), semester (-Sp/-Fa), and version (-V1) tags
            base_code = re.sub(r'-(S\d+|Sp|Fa|V\d+).*', '', raw_code, flags=re.IGNORECASE).strip().upper()

            if area not in unique_courses_per_area:
                unique_courses_per_area[area] = {}
            
            # Store it in a dictionary to guarantee it is only counted exactly once
            unique_courses_per_area[area][base_code] = credits

        # Tally up the true unique available credits
        for area, unique_courses in unique_courses_per_area.items():
            total_available = sum(unique_courses.values())
            area_credits[area] = {'obtained': 0, 'required': 0, 'available': total_available}

        # --- Overlay Admin Requirements ---
        subject_areas = conn.execute('SELECT name, min_depth_credits FROM subject_areas').fetchall()
        for sa in subject_areas:
            name = sa['name'].strip()
            if name in area_credits:
                area_credits[name]['required'] = float(sa['min_depth_credits'] or 0)
            else:
                area_credits[name] = {'obtained': 0, 'required': float(sa['min_depth_credits'] or 0), 'available': 0}

        # --- Process Student Schedules ---
        for sched in schedules_raw:
            sched_dict = dict(sched)
            course_list = []

            try:
                courses = conn.execute(f'''
                    SELECT sc.grade, c."Credits", c."Area_of_Study", c."Course_Number"
                    FROM schedule_courses sc
                    JOIN ({table_union}) c ON sc.course_number = c."Course_Number"
                    WHERE sc.schedule_id = ?
                ''', (sched['id'],)).fetchall()

                course_list = [dict(c) for c in courses]
            except sqlite3.OperationalError as e:
                print(f"Database error while fetching courses for schedule {sched['id']}: {e}")

            sched_dict['gpa'] = calculate_gpa(course_list)
            sched_dict['credits'] = sum(float(c.get('Credits') or 0) for c in course_list)
            sched_dict['course_count'] = len(course_list)

            # Separate logic based on Past vs Future
            if sched['schedule_type'] == 'past':
                past_courses.extend(course_list)

                # Tally Area of Study Credits 
                for c in course_list:
                    grade = c.get('grade')
                    if grade and grade.upper() not in ['F', '']:
                        area = c.get('Area_of_Study')
                        if area:
                            area = area.strip()
                            if area not in area_credits:
                                area_credits[area] = {'obtained': 0, 'required': 0, 'available': 0}
                            area_credits[area]['obtained'] += float(c.get('Credits') or 0)
            else:
                future_courses.extend(course_list)

            schedules_data.append(sched_dict)

    # 3. Calculate Global Student Stats
    overall_gpa = calculate_gpa(past_courses)
    estimated_overall_gpa = calculate_gpa(past_courses + future_courses)

    total_obtained_credits = sum(float(c.get('Credits') or 0) for c in past_courses if c.get('grade') and c.get('grade').upper() not in ['F', ''])
    total_future_credits = sum(float(c.get('Credits') or 0) for c in future_courses)

    try:
        graduation_credits = float(get_app_setting('graduation_credits') or 124)
    except (ValueError, TypeError):
        graduation_credits = 124.0

    conn.close()

    # FIXED: Sort the areas by obtained credits (descending), then alphabetically by name!
    sorted_area_credits = dict(sorted(area_credits.items(), key=lambda item: (-item[1]['obtained'], item[0])))

    return render_template('Student/dashboard.html',
                           schedules=schedules_data,
                           overall_gpa=overall_gpa,
                           estimated_overall_gpa=estimated_overall_gpa,
                           total_obtained_credits=total_obtained_credits,
                           total_future_credits=total_future_credits,
                           graduation_credits=graduation_credits,
                           area_credits=sorted_area_credits)

@schedule_bp.route('/delete/<int:schedule_id>', methods=['POST'])
@login_required
def delete_schedule(schedule_id):
    conn = get_db_connection()
    try:
        sched = conn.execute('SELECT id FROM schedules WHERE id = ? AND user_id = ?', (schedule_id, current_user.id)).fetchone()
        if sched:
            conn.execute('DELETE FROM schedule_courses WHERE schedule_id = ?', (schedule_id,))
            conn.execute('DELETE FROM schedules WHERE id = ?', (schedule_id,))
            conn.commit()
            flash('Schedule deleted successfully.', 'success')
        else:
            flash('Unauthorized action.', 'error')
    except Exception as e:
        conn.rollback()
        flash(f'Error deleting schedule: {e}', 'error')
    finally:
        conn.close()
    return redirect(url_for('schedule.user_dashboard'))