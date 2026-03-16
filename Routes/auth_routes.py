# Routes/auth_routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        if current_user.role == 'admin':
            return redirect(url_for('admin.dashboard'))
        return redirect(url_for('schedule.user_dashboard'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        # NEW: SQLAlchemy ORM Query
        user = User.query.filter_by(username=username).first()

        # NEW: Using check_password_hash for secure logins
        if user and check_password_hash(user.password, password):
            login_user(user)
            if user.role == 'admin':
                return redirect(url_for('admin.dashboard'))
            return redirect(url_for('schedule.user_dashboard'))
        else:
            flash("Invalid username or password.", "error")

    return render_template('Auth/login.html')

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        # Secret code logic to create an admin
        secret_code = request.form.get('secret_code')
        role = 'admin' if secret_code == 'admin123' else 'student'

        if not username or not password or not confirm_password:
            flash("Please fill out all fields.", "error")
            return redirect(url_for('auth.register'))

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return redirect(url_for('auth.register'))

        # NEW: SQLAlchemy ORM Query to check if user exists
        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            flash("Username already exists. Please choose another one.", "error")
            return redirect(url_for('auth.register'))

        # NEW: Encrypt the password before saving!
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')

        # NEW: Create and save the new user using SQLAlchemy
        new_user = User(username=username, password=hashed_password, role=role)
        db.session.add(new_user)
        db.session.commit()

        flash("Registration successful! Please log in.", "success")
        return redirect(url_for('auth.login'))

    return render_template('Auth/register.html')

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for('auth.login'))

@auth_bp.route('/choose_schedule_type')
@login_required
def choose_schedule_type():
    return render_template('Auth/choose_schedule_type.html')