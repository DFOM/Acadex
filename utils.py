# utils.py
from sqlalchemy import inspect
from models import db, Setting
import re

def get_course_data_tables():
    """Dynamically finds all uploaded CSV tables, ignoring the core system tables."""
    try:
        inspector = inspect(db.engine)
        all_tables = inspector.get_table_names()
        # Core system tables to ignore when looking for CSV datasets
        core_tables = [
            'users', 'schedules', 'schedule_courses', 'subject_areas', 
            'instructors', 'periods', 'settings', 'alembic_version'
        ]
        
        return [t for t in all_tables if t not in core_tables and not t.startswith('sqlite_')]
    except Exception as e:
        print(f"Error inspecting tables: {e}")
        return []

def is_valid_table_name(name: str) -> bool:
    """Basic validation for SQL table identifiers used by the app.

    Only allow ASCII letters, digits and underscores to avoid SQL injection
    risks when identifiers are interpolated into SQL statements.
    """
    if not name or not isinstance(name, str):
        return False
    return bool(re.match(r'^[A-Za-z0-9_]+$', name))

def get_app_setting(key, default=""):
    """Fetches a setting from the database using SQLAlchemy ORM."""
    try:
        setting = Setting.query.get(key)
        return setting.value if setting else default
    except Exception:
        return default

def update_app_setting(key, value):
    """Updates or creates a setting using SQLAlchemy ORM."""
    setting = Setting.query.get(key)
    if not setting:
        setting = Setting(key=key, value=value)
        db.session.add(setting)
    else:
        setting.value = value
    db.session.commit()

def get_best_course_table(year, semester):
    """Finds the most relevant dataset for a given year/semester."""
    tables = get_course_data_tables()
    if not tables:
        return None, False
    
    year_str = str(year)
    sem_str = str(semester).lower()

    # 1. Look for a perfect match (Year AND Semester)
    for t in tables:
        if year_str in t and sem_str in t.lower():
            return t, False # Return False for needs_filter because it's already specific

    # 2. Fallback: Look for just the Year
    for t in tables:
        if year_str in t:
            return t, True # Return True to tell the route to filter by semester
            
    # 3. Last Resort: Latest uploaded table
    return tables[-1], True

def calculate_gpa(courses):
    """Calculates GPA based on the standard 4.0 scale."""
    if not courses:
        return 0.0

    grade_points = {'A': 4.0, 'B': 3.0, 'C': 2.0, 'D': 1.0, 'F': 0.0}
    total_points = 0.0
    total_credits = 0.0

    for course in courses:
        grade = (course.get('grade') or '').strip().upper()
        try:
            credits = float(course.get('Credits') or 0)
        except (ValueError, TypeError):
            credits = 0.0

        if grade in grade_points and credits > 0:
            total_points += grade_points[grade] * credits
            total_credits += credits

    return total_points / total_credits if total_credits > 0 else 0.0