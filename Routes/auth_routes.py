# Routes/auth_routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import re
from sqlalchemy import or_
from models import db, User, SubjectArea, Setting
from utils import get_available_dataset_years

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
    majors = SubjectArea.query.filter(
        SubjectArea.is_active.is_(True),
        or_(SubjectArea.area_type == 'major', SubjectArea.area_type == 'both'),
    ).order_by(SubjectArea.name.asc()).all()
    minors = SubjectArea.query.filter(
        SubjectArea.is_active.is_(True),
        or_(SubjectArea.area_type == 'minor', SubjectArea.area_type == 'both'),
    ).order_by(SubjectArea.name.asc()).all()

    if request.method == 'POST':
        username = request.form.get('username')
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')

        gender = request.form.get('gender')
        expected_graduation_str = request.form.get('expected_graduation')
        major_subject_area_id = request.form.get('major_subject_area_id', type=int)
        minor_subject_area_id = request.form.get('minor_subject_area_id', type=int)
        
        expected_graduation = None
        if expected_graduation_str and expected_graduation_str.isdigit():
            expected_graduation = int(expected_graduation_str)

        if not username or not email or not password or not confirm_password:
            flash("Please fill out all required fields.", "error")
            return render_template('Student/register.html', majors=majors, minors=minors)

        email_regex = r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$'
        if not re.match(email_regex, email):
            flash("Please provide a valid email address.", "error")
            return render_template('Student/register.html', majors=majors, minors=minors)

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template('Student/register.html', majors=majors, minors=minors)

        if not major_subject_area_id or not minor_subject_area_id:
            flash("Please select both a major and a minor.", "error")
            return render_template('Student/register.html', majors=majors, minors=minors)

        major = SubjectArea.query.filter(
            SubjectArea.id == major_subject_area_id,
            SubjectArea.is_active.is_(True),
            or_(SubjectArea.area_type == 'major', SubjectArea.area_type == 'both'),
        ).first()
        minor = SubjectArea.query.filter(
            SubjectArea.id == minor_subject_area_id,
            SubjectArea.is_active.is_(True),
            or_(SubjectArea.area_type == 'minor', SubjectArea.area_type == 'both'),
        ).first()
        if not major or not minor:
            flash("Invalid major/minor selection. Please choose active options.", "error")
            return render_template('Student/register.html', majors=majors, minors=minors)

        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            flash("Username already exists. Please choose another one.", "error")
            return render_template('Student/register.html', majors=majors, minors=minors)

        existing_email = User.query.filter_by(email=email).first()
        if existing_email:
            flash("Email already registered. Please use another email.", "error")
            return render_template('Student/register.html', majors=majors, minors=minors)

        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')

        make_admin = False

        new_user = User(
            username=username, 
            email=email,
            password=hashed_password, 
            role='admin' if make_admin else 'student',
            is_admin=make_admin,
            gender=gender,
            expected_graduation=expected_graduation,
            major_subject_area_id=major_subject_area_id,
            minor_subject_area_id=minor_subject_area_id,
        )
        db.session.add(new_user)
        db.session.commit()

        flash("Registration successful! Please log in.", "success")
        return redirect(url_for('auth.login'))

    return render_template('Student/register.html', majors=majors, minors=minors)

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for('auth.login'))

@auth_bp.route('/choose_schedule_type')
@login_required
def choose_schedule_type():
    dataset_years = get_available_dataset_years()
    current_year_setting = Setting.query.get('current_year')
    current_year = int(current_year_setting.value) if current_year_setting and str(current_year_setting.value).isdigit() else 2024

    past_years = [y for y in dataset_years if y < current_year]
    future_years = [y for y in dataset_years if y >= current_year]

    return render_template(
        'Student/schedule_type.html',
        dataset_years=dataset_years,
        has_dataset_years=bool(dataset_years),
        has_past_years=bool(past_years),
        has_future_years=bool(future_years),
        past_years=past_years,
        future_years=future_years,
    )