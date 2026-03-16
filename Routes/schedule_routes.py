# Routes/schedule_routes.py
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
import re
from sqlalchemy import text
from models import db, Schedule, SubjectArea, Setting
from utils import get_course_data_tables, calculate_gpa

schedule_bp = Blueprint('schedule', __name__)

def get_app_setting(key, default=""):
    setting = Setting.query.get(key)
    return setting.value if setting else default

@schedule_bp.route('/dashboard')
@login_required
def user_dashboard():
    # Fetch user's schedules using ORM
    schedules_raw = Schedule.query.filter_by(user_id=current_user.id).order_by(Schedule.year.desc(), Schedule.semester.desc()).all()

    tables = get_course_data_tables()

    # Initialize default structures
    schedules_data = []
    past_courses = []
    future_courses = []
    area_credits = {}

    table_union = ""
    
    # 1. Only process catalog data if datasets exist
    if tables:
        table_union = " UNION ".join([f'SELECT "Course_Number", "Credits", "Area_of_Study" FROM "{t}"' for t in tables])
        
        # Python-based Deduplication to prevent overcounting
        raw_catalog_courses = db.session.execute(text(f'''
            SELECT "Course_Number", "Credits", "Area_of_Study"
            FROM ({table_union})
            WHERE "Area_of_Study" IS NOT NULL AND "Area_of_Study" != ''
        ''')).fetchall()

        unique_courses_per_area = {}

        for row in raw_catalog_courses:
            area = row.Area_of_Study.strip() if getattr(row, 'Area_of_Study', None) else ''
            raw_code = row.Course_Number
            try:
                credits = float(row.Credits or 0)
            except (ValueError, TypeError):
                credits = 0.0

            # Strip section (-S1), semester (-Sp/-Fa), and version (-V1) tags
            base_code = re.sub(r'-(S\d+|Sp|Fa|V\d+).*', '', raw_code, flags=re.IGNORECASE).strip().upper()

            if area not in unique_courses_per_area:
                unique_courses_per_area[area] = {}
            
            unique_courses_per_area[area][base_code] = credits

        # Tally up the true unique available credits
        for area, unique_courses in unique_courses_per_area.items():
            total_available = sum(unique_courses.values())
            area_credits[area] = {'obtained': 0, 'required': 0, 'available': total_available}

    # 2. Overlay Admin Requirements (Runs even if datasets are empty)
    subject_areas = SubjectArea.query.all()
    for sa in subject_areas:
        name = sa.name.strip()
        if name in area_credits:
            area_credits[name]['required'] = float(sa.min_depth_credits or 0)
        else:
            area_credits[name] = {'obtained': 0, 'required': float(sa.min_depth_credits or 0), 'available': 0}

    # 3. Process Student Schedules (Runs even if datasets are empty)
    for sched in schedules_raw:
        sched_dict = {
            'id': sched.id,
            'schedule_name': sched.schedule_name,
            'schedule_type': sched.schedule_type,
            'semester': sched.semester,
            'year': sched.year
        }
        course_list = []

        # Only attempt to join with courses if the tables actually exist
        if tables:
            try:
                courses = db.session.execute(text(f'''
                    SELECT sc.grade, c."Credits", c."Area_of_Study", c."Course_Number"
                    FROM schedule_courses sc
                    JOIN ({table_union}) c ON sc.course_number = c."Course_Number"
                    WHERE sc.schedule_id = :sched_id
                '''), {'sched_id': sched.id}).fetchall()

                course_list = [dict(c._mapping) for c in courses]
            except Exception as e:
                print(f"Database error while fetching courses for schedule {sched.id}: {e}")

        sched_dict['gpa'] = calculate_gpa(course_list)
        sched_dict['credits'] = sum(float(c.get('Credits') or 0) for c in course_list)
        sched_dict['course_count'] = len(course_list)

        # Separate logic based on Past vs Future
        if sched.schedule_type == 'past':
            past_courses.extend(course_list)

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

    # Calculate Global Student Stats
    overall_gpa = calculate_gpa(past_courses)
    estimated_overall_gpa = calculate_gpa(past_courses + future_courses)

    total_obtained_credits = sum(float(c.get('Credits') or 0) for c in past_courses if c.get('grade') and c.get('grade').upper() not in ['F', ''])
    total_future_credits = sum(float(c.get('Credits') or 0) for c in future_courses)

    try:
        graduation_credits = float(get_app_setting('graduation_credits', '124'))
    except (ValueError, TypeError):
        graduation_credits = 124.0

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
    try:
        # Verify ownership via ORM
        sched = Schedule.query.filter_by(id=schedule_id, user_id=current_user.id).first()
        if sched:
            # SQLAlchemy automatically handles deleting the linked schedule_courses because of the cascade rule!
            db.session.delete(sched)
            db.session.commit()
            flash('Schedule deleted successfully.', 'success')
        else:
            flash('Unauthorized action.', 'error')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting schedule: {e}', 'error')
        
    return redirect(url_for('schedule.user_dashboard'))