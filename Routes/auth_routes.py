# Routes/auth_routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import re
import json
from datetime import datetime, timedelta
from sqlalchemy import or_
from models import db, User, SubjectArea, Setting, Role, RegistrationField, LoginAttempt
from utils import get_available_dataset_years

auth_bp = Blueprint('auth', __name__)


def _client_ip() -> str:
    forwarded = (request.headers.get('X-Forwarded-For') or '').strip()
    if forwarded:
        return forwarded.split(',')[0].strip()[:64]
    return (request.remote_addr or '')[:64]


def _login_rate_limit_values() -> tuple[int, int]:
    max_attempts = 5
    window_minutes = 15
    try:
        max_attempts = max(1, int((Setting.query.get('login_rate_limit_max') or Setting(value='5')).value or 5))
    except Exception:
        max_attempts = 5
    try:
        window_minutes = max(1, int((Setting.query.get('login_rate_limit_window_minutes') or Setting(value='15')).value or 15))
    except Exception:
        window_minutes = 15
    return max_attempts, window_minutes


def _too_many_login_attempts(identity: str, ip_address: str) -> bool:
    try:
        max_attempts, window_minutes = _login_rate_limit_values()
        since = datetime.utcnow() - timedelta(minutes=window_minutes)
        recent_failures = (
            LoginAttempt.query
            .filter(LoginAttempt.success.is_(False))
            .filter(LoginAttempt.attempted_at >= since)
            .filter(or_(LoginAttempt.identity == identity, LoginAttempt.ip_address == ip_address))
            .count()
        )
        return int(recent_failures) >= int(max_attempts)
    except Exception:
        db.session.rollback()
        return False


def _record_login_attempt(identity: str, success: bool) -> None:
    try:
        db.session.add(LoginAttempt(identity=identity, ip_address=_client_ip(), success=bool(success)))
        db.session.commit()
    except Exception:
        db.session.rollback()


def _registration_fields():
    rows = RegistrationField.query.filter_by(is_active=True).order_by(RegistrationField.display_order.asc()).all()
    result = []
    for field in rows:
        options = []
        try:
            parsed = json.loads(field.options_json or '[]')
            if isinstance(parsed, list):
                options = [str(x) for x in parsed]
        except Exception:
            options = []
        result.append({'field': field, 'options': options})
    return result

# FIXED: Changed from '/' to '/login' so the new Landing Page can take the root URL
@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        # FIXED: Pointed to the new student dashboard route
        return redirect(url_for('student.user_dashboard'))

    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password')
        identity = username.lower()
        ip_address = _client_ip()

        if _too_many_login_attempts(identity, ip_address):
            flash('Too many login attempts. Please wait and try again.', 'error')
            return render_template('Student/login.html')

        user = User.query.filter(or_(User.username == username, User.email == identity)).first()

        if user and check_password_hash(user.password, password):
            _record_login_attempt(identity, success=True)
            login_user(user)
            # FIXED: Pointed to the new student dashboard route
            return redirect(url_for('student.user_dashboard'))
        else:
            _record_login_attempt(identity, success=False)
            flash("Invalid username or password.", "error")

    return render_template('Student/login.html')

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    dynamic_fields = _registration_fields()
    majors = SubjectArea.query.filter(
        SubjectArea.is_active.is_(True),
        or_(SubjectArea.area_type == 'major', SubjectArea.area_type == 'both'),
    ).order_by(SubjectArea.name.asc()).all()
    minors = SubjectArea.query.filter(
        SubjectArea.is_active.is_(True),
        or_(SubjectArea.area_type == 'minor', SubjectArea.area_type == 'both'),
    ).order_by(SubjectArea.name.asc()).all()

    if request.method == 'POST':
        full_name = (request.form.get('full_name') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')

        gender = request.form.get('gender')
        career_interests = (request.form.get('career_interests') or '').strip() or None
        expected_graduation_str = request.form.get('expected_graduation')
        major_subject_area_id = request.form.get('major_subject_area_id', type=int)
        minor_subject_area_id = request.form.get('minor_subject_area_id', type=int)
        
        expected_graduation = None
        if expected_graduation_str and expected_graduation_str.isdigit():
            expected_graduation = int(expected_graduation_str)

        if not email or not password or not confirm_password:
            flash("Please fill out all required fields.", "error")
            return render_template('Student/register.html', majors=majors, minors=minors, dynamic_fields=dynamic_fields)

        for row in dynamic_fields:
            field = row['field']
            value = (request.form.get(field.field_key) or '').strip()
            if field.is_required and not value:
                flash(f"{field.label} is required.", "error")
                return render_template('Student/register.html', majors=majors, minors=minors, dynamic_fields=dynamic_fields)

        email_regex = r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$'
        if not re.match(email_regex, email):
            flash("Please provide a valid email address.", "error")
            return render_template('Student/register.html', majors=majors, minors=minors, dynamic_fields=dynamic_fields)

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template('Student/register.html', majors=majors, minors=minors, dynamic_fields=dynamic_fields)

        if not major_subject_area_id or not minor_subject_area_id:
            flash("Please select both a major and a minor.", "error")
            return render_template('Student/register.html', majors=majors, minors=minors, dynamic_fields=dynamic_fields)

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
            return render_template('Student/register.html', majors=majors, minors=minors, dynamic_fields=dynamic_fields)

        existing_email = User.query.filter_by(email=email).first()
        if existing_email:
            flash("Email already registered. Please use another email.", "error")
            return render_template('Student/register.html', majors=majors, minors=minors, dynamic_fields=dynamic_fields)

        username_seed = (email.split('@')[0] or 'user').strip()[:120] or 'user'
        candidate_username = username_seed
        suffix = 1
        while User.query.filter_by(username=candidate_username).first():
            suffix += 1
            candidate_username = f"{username_seed}_{suffix}"[:149]

        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')

        default_role = Role.query.filter_by(name='user').first()
        extra_profile = {}
        for row in dynamic_fields:
            field = row['field']
            key = str(field.field_key or '').strip()
            if not key:
                continue
            value = (request.form.get(key) or '').strip()
            if not value:
                continue
            if key not in {'gender', 'career_interests'}:
                extra_profile[key] = value

        new_user = User(
            username=candidate_username,
            full_name=full_name or None,
            email=email,
            password=hashed_password,
            role='user',
            role_id=default_role.id if default_role else None,
            is_admin=False,
            gender=gender,
            career_interests=career_interests,
            extra_profile_json=json.dumps(extra_profile) if extra_profile else None,
            expected_graduation=expected_graduation,
            major_subject_area_id=major_subject_area_id,
            minor_subject_area_id=minor_subject_area_id,
        )
        db.session.add(new_user)
        db.session.commit()

        flash("Registration successful! Please log in.", "success")
        return redirect(url_for('auth.login'))

    return render_template('Student/register.html', majors=majors, minors=minors, dynamic_fields=dynamic_fields)

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