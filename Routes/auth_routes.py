# Routes/auth_routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import os
from models import db, User

auth_bp = Blueprint('auth', __name__)

# FIXED: Changed from '/' to '/login' so the new Landing Page can take the root URL
@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        # FIXED: Pointed to the new student dashboard route
        return redirect(url_for('student.user_dashboard'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password, password):
            login_user(user)
            # FIXED: Pointed to the new student dashboard route
            return redirect(url_for('student.user_dashboard'))
        else:
            flash("Invalid username or password.", "error")

    return render_template('Student/login.html')

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        gender = request.form.get('gender')
        expected_graduation_str = request.form.get('expected_graduation')
        secret_code = (request.form.get('secret_code') or '').strip()
        
        expected_graduation = None
        if expected_graduation_str and expected_graduation_str.isdigit():
            expected_graduation = int(expected_graduation_str)

        if not username or not password or not confirm_password:
            flash("Please fill out all required fields.", "error")
            return redirect(url_for('auth.register'))

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return redirect(url_for('auth.register'))

        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            flash("Username already exists. Please choose another one.", "error")
            return redirect(url_for('auth.register'))

        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')

        admin_secret = (os.environ.get('ADMIN_SECRET_CODE') or '').strip()
        make_admin = bool(admin_secret) and secret_code and secret_code == admin_secret

        new_user = User(
            username=username, 
            password=hashed_password, 
            role='admin' if make_admin else 'student',
            is_admin=make_admin,
            gender=gender,
            expected_graduation=expected_graduation
        )
        db.session.add(new_user)
        db.session.commit()

        flash("Registration successful! Please log in.", "success")
        return redirect(url_for('auth.login'))

    return render_template('Student/register.html')

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for('auth.login'))

@auth_bp.route('/choose_schedule_type')
@login_required
def choose_schedule_type():
    return render_template('Student/schedule_type.html')