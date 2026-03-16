# auth_routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from models import User
from utils import get_db_connection

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/')
def home():
    if current_user.is_authenticated:
        return redirect(url_for('schedule.user_dashboard'))
    return render_template('Student/index.html')

@auth_bp.route('/schedule-type')
@login_required
def choose_schedule_type():
    return render_template('Student/schedule_type.html')

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('schedule.user_dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        gender = request.form.get('gender', 'Prefer not to say')
        year_of_study = request.form.get('year_of_study', 'Prefer not to say')

        if not username or not password:
            flash('Username and password are required.', 'error')
            return redirect(url_for('auth.register'))

        conn = get_db_connection()
        existing_user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        
        if existing_user:
            flash('Username already exists.', 'error')
            conn.close()
            return redirect(url_for('auth.register'))

        new_user = User(id=None, username=username, password_hash=None)
        new_user.set_password(password)
        
        try:
            conn.execute(
                'INSERT INTO users (username, password_hash, gender, year_of_study) VALUES (?, ?, ?, ?)',
                (username, new_user.password_hash, gender, year_of_study)
            )
            conn.commit()
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('auth.login'))
        except Exception as e:
            conn.rollback()
            flash(f'Error: {e}', 'error')
        finally:
            conn.close()
            
    return render_template('Student/register.html')

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('schedule.user_dashboard'))
        
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.get_by_username(username)
        
        if user and user.check_password(password):
            login_user(user, remember=True)
            return redirect(url_for('schedule.user_dashboard'))
        else:
            flash('Invalid credentials.', 'error')
            
    return render_template('Student/login.html')

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.home'))