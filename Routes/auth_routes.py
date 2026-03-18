# Routes/auth_routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, session
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import re
import json
from datetime import datetime, timedelta
from sqlalchemy import or_
from sqlalchemy import text as sa_text
from sqlalchemy import inspect as sa_inspect
from flask_migrate import upgrade
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


def _safe_lookup_user(username: str, identity: str):
    return User.query.filter(or_(User.username == username, User.email == identity)).first()


def _attempt_login_schema_recovery(exc: Exception) -> bool:
    current_app.logger.warning('auth_login_schema_recovery_attempt error=%s', exc)
    try:
        db.session.rollback()
    except Exception:
        pass

    try:
        upgrade()
        db.session.remove()
        current_app.logger.warning('auth_login_schema_recovery_succeeded')
        return True
    except Exception as heal_exc:
        try:
            db.session.rollback()
        except Exception:
            pass
        current_app.logger.exception('auth_login_schema_recovery_failed error=%s', heal_exc)
        return False


def _heal_auth_user_schema() -> bool:
    """Best-effort local schema heal for auth-critical user columns."""
    try:
        insp = sa_inspect(db.engine)
        tables = set(insp.get_table_names())
        if 'users' not in tables:
            return False
        user_cols = {c['name'] for c in insp.get_columns('users')}
        with db.engine.begin() as conn:
            if 'email' not in user_cols:
                conn.execute(sa_text('ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(255)'))
            if 'is_admin' not in user_cols:
                conn.execute(sa_text('ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN'))
                conn.execute(sa_text('UPDATE users SET is_admin = FALSE WHERE is_admin IS NULL'))
                conn.execute(sa_text('ALTER TABLE users ALTER COLUMN is_admin SET DEFAULT FALSE'))
            if 'full_name' not in user_cols:
                conn.execute(sa_text('ALTER TABLE users ADD COLUMN IF NOT EXISTS full_name VARCHAR(150)'))
            if 'role_id' not in user_cols:
                conn.execute(sa_text('ALTER TABLE users ADD COLUMN IF NOT EXISTS role_id INTEGER'))
            if 'major_subject_area_id' not in user_cols:
                conn.execute(sa_text('ALTER TABLE users ADD COLUMN IF NOT EXISTS major_subject_area_id INTEGER'))
            if 'minor_subject_area_id' not in user_cols:
                conn.execute(sa_text('ALTER TABLE users ADD COLUMN IF NOT EXISTS minor_subject_area_id INTEGER'))
            if 'ai_opt_in' not in user_cols:
                conn.execute(sa_text('ALTER TABLE users ADD COLUMN IF NOT EXISTS ai_opt_in BOOLEAN'))
                conn.execute(sa_text('UPDATE users SET ai_opt_in = FALSE WHERE ai_opt_in IS NULL'))
                conn.execute(sa_text('ALTER TABLE users ALTER COLUMN ai_opt_in SET DEFAULT FALSE'))
            if 'gender' not in user_cols:
                conn.execute(sa_text('ALTER TABLE users ADD COLUMN IF NOT EXISTS gender VARCHAR(50)'))
            if 'career_interests' not in user_cols:
                conn.execute(sa_text('ALTER TABLE users ADD COLUMN IF NOT EXISTS career_interests TEXT'))
            if 'extra_profile_json' not in user_cols:
                conn.execute(sa_text('ALTER TABLE users ADD COLUMN IF NOT EXISTS extra_profile_json TEXT'))
            if 'profile_image_path' not in user_cols:
                conn.execute(sa_text('ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_image_path VARCHAR(255)'))
            if 'expected_graduation' not in user_cols:
                conn.execute(sa_text('ALTER TABLE users ADD COLUMN IF NOT EXISTS expected_graduation INTEGER'))
            if 'two_factor_enabled' not in user_cols:
                conn.execute(sa_text('ALTER TABLE users ADD COLUMN IF NOT EXISTS two_factor_enabled BOOLEAN'))
                conn.execute(sa_text('UPDATE users SET two_factor_enabled = FALSE WHERE two_factor_enabled IS NULL'))
                conn.execute(sa_text('ALTER TABLE users ALTER COLUMN two_factor_enabled SET DEFAULT FALSE'))
            if 'two_factor_secret' not in user_cols:
                conn.execute(sa_text('ALTER TABLE users ADD COLUMN IF NOT EXISTS two_factor_secret VARCHAR(255)'))
        return True
    except Exception as exc:
        current_app.logger.warning('auth_user_schema_heal_failed error=%s', exc)
        try:
            db.session.rollback()
        except Exception:
            pass
        return False


def _verify_password_with_legacy_support(user: User, password: str) -> bool:
    stored = str(getattr(user, 'password', '') or '')
    raw = str(password or '')
    try:
        if check_password_hash(stored, raw):
            return True
    except Exception:
        pass

    if stored and raw and stored == raw:
        try:
            user.password = generate_password_hash(raw, method='pbkdf2:sha256')
            db.session.commit()
            current_app.logger.warning('auth_password_upgraded_from_legacy_plaintext user_id=%s', getattr(user, 'id', None))
        except Exception:
            db.session.rollback()
        return True
    return False

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
        current_app.logger.info('auth_login_attempt identity=%s ip=%s', identity, ip_address)

        if _too_many_login_attempts(identity, ip_address):
            current_app.logger.warning('auth_login_rate_limited identity=%s ip=%s', identity, ip_address)
            flash('Too many login attempts. Please wait and try again.', 'error')
            return render_template('Student/login.html')

        try:
            user = _safe_lookup_user(username, identity)
            valid_credentials = bool(user and _verify_password_with_legacy_support(user, password))
        except Exception as exc:
            recovered = _attempt_login_schema_recovery(exc)
            if not recovered:
                current_app.logger.exception('auth_login_query_error identity=%s ip=%s error=%s', identity, ip_address, exc)
                flash('Sign in is temporarily unavailable due to a server/session issue. Please try again.', 'error')
                return render_template('Student/login.html')

            try:
                user = _safe_lookup_user(username, identity)
                valid_credentials = bool(user and _verify_password_with_legacy_support(user, password))
            except Exception as retry_exc:
                db.session.rollback()
                current_app.logger.exception('auth_login_query_retry_error identity=%s ip=%s error=%s', identity, ip_address, retry_exc)
                flash('Sign in is temporarily unavailable due to a server/session issue. Please try again.', 'error')
                return render_template('Student/login.html')

        if valid_credentials:
            try:
                _record_login_attempt(identity, success=True)
                login_user(user)
                session.permanent = True
                current_app.logger.info('auth_login_success user_id=%s identity=%s ip=%s session_sid=%s', user.id, identity, ip_address, getattr(session, 'sid', None))
                # FIXED: Pointed to the new student dashboard route
                return redirect(url_for('student.user_dashboard'))
            except Exception as exc:
                db.session.rollback()
                current_app.logger.exception('auth_login_session_error user_id=%s identity=%s ip=%s error=%s', getattr(user, 'id', None), identity, ip_address, exc)
                flash('Sign in failed due to a server/session error. Please try again.', 'error')
                return render_template('Student/login.html')

        _record_login_attempt(identity, success=False)
        current_app.logger.warning('auth_login_invalid_credentials identity=%s ip=%s', identity, ip_address)
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
        username = (request.form.get('username') or '').strip()[:150]
        full_name = (request.form.get('full_name') or '').strip()[:150]
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password')

        gender = request.form.get('gender')
        career_interests = (request.form.get('career_interests') or '').strip() or None
        expected_graduation_str = request.form.get('expected_graduation')
        major_raw = (request.form.get('major_subject_area_id') or '').strip().lower()
        minor_raw = (request.form.get('minor_subject_area_id') or '').strip().lower()
        major_subject_area_id = None if major_raw in {'', 'undeclared', 'prefer_not_to_say'} else request.form.get('major_subject_area_id', type=int)
        minor_subject_area_id = None if minor_raw in {'', 'undeclared', 'prefer_not_to_say'} else request.form.get('minor_subject_area_id', type=int)
        
        expected_graduation = None
        if expected_graduation_str and expected_graduation_str.isdigit():
            expected_graduation = int(expected_graduation_str)

        if not username or not email or not password:
            flash("Please fill out all required fields.", "error")
            return render_template('Student/register.html', majors=majors, minors=minors, dynamic_fields=dynamic_fields)

        email_regex = r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$'
        if not re.match(email_regex, email):
            flash("Please provide a valid email address.", "error")
            return render_template('Student/register.html', majors=majors, minors=minors, dynamic_fields=dynamic_fields)

        if len(password or '') < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template('Student/register.html', majors=majors, minors=minors, dynamic_fields=dynamic_fields)

        if major_subject_area_id is not None:
            major = SubjectArea.query.filter(
                SubjectArea.id == major_subject_area_id,
                SubjectArea.is_active.is_(True),
                or_(SubjectArea.area_type == 'major', SubjectArea.area_type == 'both'),
            ).first()
            if not major:
                flash("Invalid major selection. Please choose an active option or Undeclared.", "error")
                return render_template('Student/register.html', majors=majors, minors=minors, dynamic_fields=dynamic_fields)

        if minor_subject_area_id is not None:
            minor = SubjectArea.query.filter(
                SubjectArea.id == minor_subject_area_id,
                SubjectArea.is_active.is_(True),
                or_(SubjectArea.area_type == 'minor', SubjectArea.area_type == 'both'),
            ).first()
            if not minor:
                flash("Invalid minor selection. Please choose an active option or Undeclared.", "error")
                return render_template('Student/register.html', majors=majors, minors=minors, dynamic_fields=dynamic_fields)

        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            flash("Username already exists. Please choose another one.", "error")
            return render_template('Student/register.html', majors=majors, minors=minors, dynamic_fields=dynamic_fields)

        existing_email = User.query.filter_by(email=email).first()
        if existing_email:
            flash("Email already registered. Please use another email.", "error")
            return render_template('Student/register.html', majors=majors, minors=minors, dynamic_fields=dynamic_fields)

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
            if key not in {'gender', 'career_interests', 'major_subject_area_id', 'minor_subject_area_id'}:
                extra_profile[key] = value

        if major_raw in {'undeclared', 'prefer_not_to_say'}:
            extra_profile['major_optional_choice'] = major_raw
        if minor_raw in {'undeclared', 'prefer_not_to_say'}:
            extra_profile['minor_optional_choice'] = minor_raw

        new_user = User(
            username=username,
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
        register_saved = False
        for attempt in range(2):
            try:
                db.session.add(new_user)
                db.session.commit()
                register_saved = True
                break
            except Exception as exc:
                db.session.rollback()
                current_app.logger.exception('auth_register_commit_error attempt=%s username=%s email=%s error=%s', attempt + 1, username, email, exc)
                if attempt == 0 and _heal_auth_user_schema():
                    continue
                break

        if not register_saved:
            flash('Registration is temporarily unavailable due to a server configuration issue. Please try again in a moment.', 'error')
            return render_template('Student/register.html', majors=majors, minors=minors, dynamic_fields=dynamic_fields)

        flash("Registration successful! Please log in.", "success")
        return redirect(url_for('auth.login'))

    return render_template('Student/register.html', majors=majors, minors=minors, dynamic_fields=dynamic_fields)

@auth_bp.route('/logout')
@login_required
def logout():
    current_app.logger.info('auth_logout user_id=%s session_sid=%s', current_user.id, getattr(session, 'sid', None))
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