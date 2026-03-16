# app.py
from flask import Flask
from flask_login import LoginManager
import os

from config import SECRET_KEY, UPLOAD_FOLDER
from Routes.admin_routes import admin_bp
from Routes.student_routes import student_bp
from Routes.auth_routes import auth_bp
from Routes.schedule_routes import schedule_bp
from models import User
from utils import populate_master_tables

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'

@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

def init_db():
    from utils import get_db_connection
    conn = get_db_connection()
    # Create tables if they don't exist (FIXED: Added missing tables)
    conn.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password_hash TEXT, gender TEXT, year_of_study TEXT)')
    conn.execute('CREATE TABLE IF NOT EXISTS schedules (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, schedule_name TEXT, year INTEGER, semester TEXT, schedule_type TEXT)')
    conn.execute('CREATE TABLE IF NOT EXISTS schedule_courses (id INTEGER PRIMARY KEY AUTOINCREMENT, schedule_id INTEGER, course_number TEXT, grade TEXT)')
    conn.execute('CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT)')
    conn.execute('CREATE TABLE IF NOT EXISTS subject_areas (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, is_major BOOLEAN DEFAULT 0, min_depth_credits INTEGER DEFAULT 0)')
    conn.execute('CREATE TABLE IF NOT EXISTS instructors (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE)')
    conn.execute('CREATE TABLE IF NOT EXISTS periods (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, start_time TEXT, end_time TEXT)')
    conn.commit()
    conn.close()
    
    # Sync unique data from uploaded datasets
    populate_master_tables()

app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(student_bp)
app.register_blueprint(schedule_bp)

if __name__ == '__main__':
    init_db()
    app.run(debug=True)