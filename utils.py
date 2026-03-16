# utils.py
import sqlite3

def get_db_connection():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

def get_course_data_tables():
    conn = get_db_connection()
    # Pulls all tables except the core system tables
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('users', 'schedules', 'schedule_courses', 'subject_areas', 'instructors', 'periods', 'settings')").fetchall()
    conn.close()
    return [t['name'] for t in tables if not t['name'].startswith('sqlite_')]

def get_app_setting(key):
    conn = get_db_connection()
    conn.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
    setting = conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    conn.close()
    return setting['value'] if setting else None

def update_app_setting(key, value):
    conn = get_db_connection()
    conn.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
    conn.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, value))
    conn.commit()
    conn.close()

def get_best_course_table(year, semester):
    tables = get_course_data_tables()
    if not tables:
        return None, False
    
    # Try to find a table that matches the year string
    for t in tables:
        if str(year) in t:
            return t, True
            
    # Fallback to the latest uploaded table
    return tables[-1], True

def populate_master_tables():
    # Placeholder for syncing distinct instructors/subjects if needed
    pass

def calculate_gpa(courses):
    if not courses:
        return 0.0

    # Standard 4.0 Scale restricted strictly to A, B, C, D, F
    grade_points = {
        'A': 4.0,
        'B': 3.0,
        'C': 2.0,
        'D': 1.0,
        'F': 0.0
    }

    total_points = 0.0
    total_credits = 0.0

    for course in courses:
        # The 'or ""' perfectly fixes the NoneType crash you experienced earlier!
        grade = (course.get('grade') or '').strip().upper()
        
        try:
            credits = float(course.get('Credits') or 0)
        except (ValueError, TypeError):
            credits = 0.0

        # Only calculate if the grade is one of the valid 5 letters, and credits are valid
        if grade in grade_points and credits > 0:
            total_points += grade_points[grade] * credits
            total_credits += credits

    if total_credits == 0:
        return 0.0

    return total_points / total_credits