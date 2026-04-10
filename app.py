# app.py
from flask import Flask, jsonify, request, session, g
from flask_login import LoginManager, current_user
from flask_session import Session
import os
import secrets
import logging
from collections import defaultdict, deque
from pathlib import Path
from datetime import timedelta, datetime
from werkzeug.security import generate_password_hash
from sqlalchemy import inspect as sa_inspect, text as sa_text
from models import db, User, Role, Permission, RegistrationField, Setting, RBAC_PERMISSION_KEYS
from flask_migrate import Migrate, upgrade, stamp
import json

from Routes.auth_routes import auth_bp
from Routes.admin_routes import admin_bp
from Routes.main_routes import main_bp
from Routes.student_routes import student_bp
from Routes.schedule_routes import schedule_bp
from utils import clean_display_name as _clean_display_name

VERSION = 'Beta 1.8.0'
APP_STARTED_AT = datetime.utcnow().replace(tzinfo=None).isoformat() + 'Z'

app = Flask(__name__)
session_manager = Session()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def _env_str(name: str, default: str = '') -> str:
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip()


app.secret_key = _env_str('SESSION_SECRET') or _env_str('SECRET_KEY') or 'dev-secret-change-me'
if os.environ.get('FLASK_ENV') == 'production' and app.secret_key == 'dev-secret-change-me':
    raise RuntimeError('SESSION_SECRET (or SECRET_KEY) must be set in production.')

app.config['JWT_SECRET_KEY'] = _env_str('JWT_SECRET') or app.secret_key

def _resolve_database_url() -> str:
    database_url = os.environ.get('DATABASE_URL')

    if database_url and database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    if not database_url:
        if os.environ.get('RENDER'):
            raise RuntimeError('DATABASE_URL must be configured on Render for persistent user/session data.')
        return f"sqlite:///{Path(app.instance_path) / 'app.db'}"

    if database_url.startswith('sqlite:///'):
        sqlite_path = database_url.replace('sqlite:///', '', 1)
        if sqlite_path != ':memory:' and not os.path.isabs(sqlite_path):
            database_url = f"sqlite:///{Path(app.root_path) / sqlite_path}"

    return database_url


database_url = _resolve_database_url()

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.environ.get('UPLOAD_FOLDER', 'uploads')
app.config['PROFILE_UPLOAD_FOLDER'] = os.path.join(app.config['UPLOAD_FOLDER'], 'profile_images')
app.config['STRICT_MIGRATIONS'] = _env_bool('STRICT_MIGRATIONS', default=False)
app.config['ALEMBIC_AUTO_STAMP_ON_MISSING_REV'] = _env_bool('ALEMBIC_AUTO_STAMP_ON_MISSING_REV', default=True)
app.config['MAX_CONTENT_LENGTH'] = int(os.environ.get('MAX_CONTENT_LENGTH_BYTES', 2 * 1024 * 1024))

cookie_samesite = (_env_str('SESSION_COOKIE_SAMESITE', 'Lax') or 'Lax').strip().capitalize()
if cookie_samesite not in {'Lax', 'Strict', 'None'}:
    cookie_samesite = 'Lax'

app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = cookie_samesite
app.config['SESSION_COOKIE_SECURE'] = _env_bool('SESSION_COOKIE_SECURE', default=os.environ.get('FLASK_ENV') == 'production')
app.config['SESSION_COOKIE_PATH'] = _env_str('SESSION_COOKIE_PATH', '/') or '/'
app.config['SESSION_COOKIE_DOMAIN'] = _env_str('SESSION_COOKIE_DOMAIN', '') or None
app.config['SESSION_COOKIE_NAME'] = _env_str('SESSION_COOKIE_NAME', 'acadex_session') or 'acadex_session'
app.config['APP_VERSION'] = VERSION
app.config['APP_STARTED_AT'] = APP_STARTED_AT

app.config['SESSION_TYPE'] = 'sqlalchemy'
app.config['SESSION_SQLALCHEMY'] = db
app.config['SESSION_SQLALCHEMY_TABLE'] = 'user_sessions'
app.config['SESSION_USE_SIGNER'] = True
app.config['SESSION_KEY_PREFIX'] = 'acadex:session:'
app.config['SESSION_PERMANENT'] = True

app.config['REMEMBER_COOKIE_HTTPONLY'] = True
app.config['REMEMBER_COOKIE_SAMESITE'] = cookie_samesite
app.config['REMEMBER_COOKIE_SECURE'] = app.config['SESSION_COOKIE_SECURE']
app.config['REMEMBER_COOKIE_PATH'] = app.config['SESSION_COOKIE_PATH']
app.config['REMEMBER_COOKIE_DOMAIN'] = app.config['SESSION_COOKIE_DOMAIN']
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=12)

if not app.logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s event=%(message)s'))
    app.logger.addHandler(handler)
app.logger.setLevel(logging.INFO)

_rate_limit_buckets: dict[str, deque] = defaultdict(deque)
_SYSTEM_HEALTH_ACTION_LOGS_KEY = 'system_health_action_logs_json'


def _request_ip() -> str:
    forwarded = (request.headers.get('X-Forwarded-For') or '').strip()
    if forwarded:
        return forwarded.split(',')[0].strip()[:64]
    return (request.remote_addr or 'unknown')[:64]


def _append_system_health_action_log(entry: dict, limit: int = 400) -> None:
    if not isinstance(entry, dict):
        return

    try:
        row = Setting.query.get(_SYSTEM_HEALTH_ACTION_LOGS_KEY)
        existing: list[dict] = []
        if row and row.value:
            parsed = json.loads(str(row.value))
            if isinstance(parsed, list):
                existing = [item for item in parsed if isinstance(item, dict)]

        existing.append(entry)
        if len(existing) > int(limit):
            existing = existing[-int(limit):]

        encoded = json.dumps(existing, separators=(',', ':'), ensure_ascii=False)
        if not row:
            row = Setting(key=_SYSTEM_HEALTH_ACTION_LOGS_KEY, value=encoded)
            db.session.add(row)
        else:
            row.value = encoded
        db.session.commit()
    except Exception:
        db.session.rollback()


def _safe_log_detail(value, max_len: int = 1400) -> str:
    if value is None:
        return ''
    try:
        if isinstance(value, str):
            text = value
        else:
            text = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
        text = text.strip()
        if len(text) > max_len:
            return text[:max_len]
        return text
    except Exception:
        return str(value)[:max_len]


def _apply_rate_limit(bucket: str, max_events: int, window_seconds: int) -> bool:
    now = __import__('time').time()
    q = _rate_limit_buckets[bucket]
    cutoff = now - float(window_seconds)
    while q and q[0] < cutoff:
        q.popleft()
    if len(q) >= int(max_events):
        return False
    q.append(now)
    return True


def _csrf_token() -> str:
    token = session.get('_csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['_csrf_token'] = token
    return token


app.jinja_env.filters['clean_name'] = _clean_display_name


@app.context_processor
def _inject_csrf_token():
    return {'csrf_token': _csrf_token()}


@app.before_request
def _security_before_request():
    if not session.get('_session_started_at'):
        session['_session_started_at'] = datetime.utcnow().isoformat()
        app.logger.info('session_created path=%s ip=%s auth=%s session_sid=%s', request.path, _request_ip(), bool(getattr(current_user, 'is_authenticated', False)), getattr(session, 'sid', None))

    _csrf_token()

    if request.endpoint == 'static':
        return None

    if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        path = request.path or ''
        ip = _request_ip()

        if path.endswith('/login') and not _apply_rate_limit(f'login:{ip}', 20, 300):
            return jsonify({'success': False, 'message': 'Too many login attempts.'}), 429
        if path.endswith('/register') and not _apply_rate_limit(f'register:{ip}', 10, 600):
            return jsonify({'success': False, 'message': 'Too many registration attempts.'}), 429
        if path.startswith('/admin') and not _apply_rate_limit(f'admin:{ip}', 180, 60):
            return jsonify({'success': False, 'message': 'Too many admin requests.'}), 429
        if path.startswith('/student/api/') and not _apply_rate_limit(f'student-api:{ip}', 240, 60):
            return jsonify({'success': False, 'message': 'Too many requests.'}), 429

        provided = (request.headers.get('X-CSRF-Token') or request.form.get('csrf_token') or '').strip()
        expected = str(session.get('_csrf_token') or '')
        if not expected or provided != expected:
            app.logger.warning(
                'csrf_mismatch path=%s method=%s ip=%s auth=%s session_sid=%s',
                request.path,
                request.method,
                _request_ip(),
                bool(getattr(current_user, 'is_authenticated', False)),
                getattr(session, 'sid', None),
            )
            wants_json = request.is_json or path.startswith('/admin/api/') or path.startswith('/student/api/')
            if wants_json:
                return jsonify({'success': False, 'message': 'Invalid CSRF token.'}), 400
            return 'Invalid request token.', 400
    return None


@app.after_request
def _security_after_request(response):
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    if app.config.get('SESSION_COOKIE_SECURE'):
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'

    if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'} and (
        request.path.startswith('/admin')
        or request.path.startswith('/student/api/')
        or request.path.startswith('/schedule/')
    ):
        user_id = current_user.id if getattr(current_user, 'is_authenticated', False) else None
        app.logger.info('audit method=%s path=%s status=%s ip=%s user=%s', request.method, request.path, response.status_code, _request_ip(), user_id)
        _append_system_health_action_log(
            {
                'ts': datetime.utcnow().replace(tzinfo=None).isoformat() + 'Z',
                'method': str(request.method or ''),
                'path': str(request.path or ''),
                'status': int(response.status_code or 0),
                'ok': bool(int(response.status_code or 0) < 400),
                'ip': _request_ip(),
                'user_id': int(user_id) if user_id is not None else None,
                'session_sid': str(getattr(session, 'sid', '') or ''),
                'detail': _safe_log_detail(getattr(g, '_system_action_detail', '')),
            }
        )
    return response


@app.errorhandler(400)
def _bad_request(_err):
    if request.path.startswith('/admin/api/') or request.path.startswith('/student/api/'):
        return jsonify({'success': False, 'message': 'Bad request.'}), 400
    return 'Bad request.', 400


@app.errorhandler(403)
def _forbidden(_err):
    if request.path.startswith('/admin/api/') or request.path.startswith('/student/api/'):
        return jsonify({'success': False, 'message': 'Forbidden.'}), 403
    return 'Forbidden.', 403


@app.errorhandler(404)
def _not_found(_err):
    if request.path.startswith('/admin/api/') or request.path.startswith('/student/api/'):
        return jsonify({'success': False, 'message': 'Not found.'}), 404
    return 'Not found.', 404


@app.errorhandler(429)
def _too_many(_err):
    if request.path.startswith('/admin/api/') or request.path.startswith('/student/api/'):
        return jsonify({'success': False, 'message': 'Too many requests.'}), 429
    return 'Too many requests. Please retry shortly.', 429


@app.errorhandler(500)
def _server_error(err):
    app.logger.exception('server_error path=%s method=%s ip=%s error=%s', request.path, request.method, _request_ip(), err)
    if request.path.startswith('/admin/api/') or request.path.startswith('/student/api/'):
        return jsonify({'success': False, 'message': 'Internal server error.'}), 500
    return 'Internal server error.', 500

db.init_app(app)
session_manager.init_app(app)
migrate = Migrate(app, db)

login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.init_app(app)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


app.register_blueprint(auth_bp)
app.register_blueprint(main_bp)
app.register_blueprint(admin_bp, url_prefix='/admin')
app.register_blueprint(student_bp, url_prefix='/student')
app.register_blueprint(schedule_bp, url_prefix='/schedule')

app.add_url_rule(
    '/api/student/course-details/<int:course_id>',
    endpoint='student_course_details_graph_alias',
    view_func=app.view_functions['student.api_course_details_graph'],
    methods=['GET'],
)


@app.get('/health')
def health_check():
    return jsonify({'status': 'ok'}), 200


@app.get('/health/schema')
def health_schema_check():
    required_columns = {
        'users': {
            'id', 'username', 'email', 'password', 'role', 'role_id', 'is_admin',
            'full_name', 'major_subject_area_id', 'minor_subject_area_id',
            'ai_opt_in', 'gender', 'career_interests', 'extra_profile_json',
            'profile_image_path', 'expected_graduation', 'two_factor_enabled', 'two_factor_secret',
        },
        'roles': {'id', 'name'},
        'permissions': {'id', 'key'},
        'role_permissions': {'role_id', 'permission_id'},
        'registration_fields': {'id', 'field_key', 'is_active'},
        'login_attempts': {'id', 'identity', 'ip_address', 'success', 'attempted_at'},
        'settings': {'key', 'value'},
        'catalog_statuses': {'id', 'term', 'status', 'dataset', 'updated_at'},
        'scraped_datasets': {'id', 'name', 'status', 'selected_fields_json'},
        'scraped_courses': {'id', 'dataset_id', 'data_json'},
        'courses': {
            'id', 'course_number', 'title', 'credits', 'day_period', 'semester', 'year',
            'prerequisite', 'subject_area', 'instructor', 'source', 'dataset_name',
            'created_at', 'updated_at', 'last_updated',
        },
        'subject_areas': {
            'id', 'name', 'area_type', 'is_active', 'is_required',
            'required_for_paths', 'min_depth_credits', 'min_major_credits', 'min_minor_credits',
        },
        'announcements': {'id', 'title', 'content', 'date_posted', 'is_active', 'ann_type', 'updated_at'},
        'landing_page_sections': {'id', 'name', 'enabled', 'display_order', 'content_json', 'updated_at'},
        'schedules': {'id', 'user_id', 'session_id', 'schedule_name', 'year', 'semester', 'schedule_type'},
        app.config.get('SESSION_SQLALCHEMY_TABLE', 'user_sessions'): {'id', 'data', 'expiry'},
    }

    try:
        inspector = sa_inspect(db.engine)
        existing_tables = set(inspector.get_table_names())
    except Exception as exc:
        app.logger.exception('health_schema_inspect_error error=%s', exc)
        return jsonify({'status': 'error', 'ok': False, 'message': f'Unable to inspect schema: {exc}'}), 500

    missing_tables = []
    missing_columns: dict[str, list[str]] = {}
    for table_name, expected_cols in required_columns.items():
        if table_name not in existing_tables:
            missing_tables.append(table_name)
            continue
        try:
            actual_cols = {c['name'] for c in inspector.get_columns(table_name)}
            missing = sorted(expected_cols - actual_cols)
            if missing:
                missing_columns[table_name] = missing
        except Exception as exc:
            missing_columns[table_name] = [f'!inspect_error: {exc}']

    ok = (len(missing_tables) == 0 and len(missing_columns) == 0)
    status_code = 200 if ok else 503

    payload = {
        'status': 'ok' if ok else 'schema_mismatch',
        'ok': ok,
        'required_tables': sorted(required_columns.keys()),
        'missing_tables': sorted(missing_tables),
        'missing_columns': missing_columns,
    }
    return jsonify(payload), status_code


def _bootstrap_database() -> None:
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['PROFILE_UPLOAD_FOLDER'], exist_ok=True)

    if app.config['SQLALCHEMY_DATABASE_URI'].startswith('sqlite:///'):
        sqlite_path = app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '', 1)
        if sqlite_path and sqlite_path != ':memory:':
            os.makedirs(os.path.dirname(sqlite_path), exist_ok=True)

    with app.app_context():
        db.create_all()

        try:
            upgrade()
        except Exception as exc:
            message = str(exc)
            if "Can't locate revision identified by" in message and app.config.get('ALEMBIC_AUTO_STAMP_ON_MISSING_REV', True):
                app.logger.warning('Alembic revision mismatch detected. Attempting recovery by stamping to head. error=%s', message)
                try:
                    stamp(revision='head')
                    app.logger.warning('Alembic recovery succeeded: stamped database to head revision.')
                except Exception as stamp_exc:
                    if app.config.get('STRICT_MIGRATIONS', False):
                        raise
                    app.logger.warning('Alembic stamp recovery failed: %s', stamp_exc)
            elif app.config.get('STRICT_MIGRATIONS', False):
                raise
            else:
                app.logger.warning("Automatic migration upgrade skipped: %s", exc)

        try:
            _seed_security_defaults()
        except Exception as exc:
            if app.config.get('STRICT_MIGRATIONS', False):
                raise
            app.logger.warning('Security defaults bootstrap skipped until schema is updated: %s', exc)

        try:
            _ensure_runtime_schema_compatibility()
        except Exception as exc:
            if app.config.get('STRICT_MIGRATIONS', False):
                raise
            app.logger.warning('Runtime schema compatibility heal skipped: %s', exc)


def _ensure_runtime_schema_compatibility() -> None:
    """Best-effort healer for drifted production schemas.

    This is intentionally idempotent and targets only columns that are
    required by request-time code paths (landing/login/register/admin checks).
    """
    insp = sa_inspect(db.engine)
    tables = set(insp.get_table_names())

    if 'users' in tables:
        user_cols = {c['name'] for c in insp.get_columns('users')}
        with db.engine.begin() as conn:
            if 'email' not in user_cols:
                conn.execute(sa_text('ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(255)'))
            if 'is_admin' not in user_cols:
                conn.execute(sa_text('ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN'))
                conn.execute(sa_text('UPDATE users SET is_admin = FALSE WHERE is_admin IS NULL'))
                conn.execute(sa_text('ALTER TABLE users ALTER COLUMN is_admin SET DEFAULT FALSE'))
                try:
                    conn.execute(sa_text('ALTER TABLE users ALTER COLUMN is_admin SET NOT NULL'))
                except Exception:
                    pass
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
                try:
                    conn.execute(sa_text('ALTER TABLE users ALTER COLUMN ai_opt_in SET NOT NULL'))
                except Exception:
                    pass
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
                try:
                    conn.execute(sa_text('ALTER TABLE users ALTER COLUMN two_factor_enabled SET NOT NULL'))
                except Exception:
                    pass
            if 'two_factor_secret' not in user_cols:
                conn.execute(sa_text('ALTER TABLE users ADD COLUMN IF NOT EXISTS two_factor_secret VARCHAR(255)'))

    if 'catalog_statuses' in tables:
        cat_cols = {c['name'] for c in insp.get_columns('catalog_statuses')}
        if 'dataset' not in cat_cols:
            with db.engine.begin() as conn:
                conn.execute(sa_text('ALTER TABLE catalog_statuses ADD COLUMN IF NOT EXISTS dataset VARCHAR(150)'))

    if 'subject_areas' in tables:
        subject_cols = {c['name'] for c in insp.get_columns('subject_areas')}
        with db.engine.begin() as conn:
            if 'area_type' not in subject_cols:
                conn.execute(sa_text('ALTER TABLE subject_areas ADD COLUMN IF NOT EXISTS area_type VARCHAR(20)'))
                conn.execute(sa_text("UPDATE subject_areas SET area_type = 'generic' WHERE area_type IS NULL"))
            if 'is_active' not in subject_cols:
                conn.execute(sa_text('ALTER TABLE subject_areas ADD COLUMN IF NOT EXISTS is_active BOOLEAN'))
                conn.execute(sa_text('UPDATE subject_areas SET is_active = TRUE WHERE is_active IS NULL'))
            if 'is_required' not in subject_cols:
                conn.execute(sa_text('ALTER TABLE subject_areas ADD COLUMN IF NOT EXISTS is_required BOOLEAN'))
                conn.execute(sa_text('UPDATE subject_areas SET is_required = FALSE WHERE is_required IS NULL'))
            if 'required_for_paths' not in subject_cols:
                conn.execute(sa_text('ALTER TABLE subject_areas ADD COLUMN IF NOT EXISTS required_for_paths TEXT'))
            if 'min_depth_credits' not in subject_cols:
                conn.execute(sa_text('ALTER TABLE subject_areas ADD COLUMN IF NOT EXISTS min_depth_credits DOUBLE PRECISION'))
                conn.execute(sa_text('UPDATE subject_areas SET min_depth_credits = 0 WHERE min_depth_credits IS NULL'))
            if 'min_major_credits' not in subject_cols:
                conn.execute(sa_text('ALTER TABLE subject_areas ADD COLUMN IF NOT EXISTS min_major_credits DOUBLE PRECISION'))
                conn.execute(sa_text('UPDATE subject_areas SET min_major_credits = 0 WHERE min_major_credits IS NULL'))
            if 'min_minor_credits' not in subject_cols:
                conn.execute(sa_text('ALTER TABLE subject_areas ADD COLUMN IF NOT EXISTS min_minor_credits DOUBLE PRECISION'))
                conn.execute(sa_text('UPDATE subject_areas SET min_minor_credits = 0 WHERE min_minor_credits IS NULL'))

    if 'courses' in tables:
        course_cols = {c['name'] for c in insp.get_columns('courses')}
        with db.engine.begin() as conn:
            if 'dataset_name' not in course_cols:
                conn.execute(sa_text('ALTER TABLE courses ADD COLUMN IF NOT EXISTS dataset_name VARCHAR(150)'))
            if 'last_updated' not in course_cols:
                conn.execute(sa_text('ALTER TABLE courses ADD COLUMN IF NOT EXISTS last_updated TIMESTAMP'))
            conn.execute(sa_text('UPDATE courses SET last_updated = COALESCE(last_updated, updated_at, created_at, CURRENT_TIMESTAMP)'))

    if 'announcements' in tables:
        ann_cols = {c['name'] for c in insp.get_columns('announcements')}
        with db.engine.begin() as conn:
            if 'ann_type' not in ann_cols:
                conn.execute(sa_text('ALTER TABLE announcements ADD COLUMN IF NOT EXISTS ann_type VARCHAR(20)'))
                conn.execute(sa_text("UPDATE announcements SET ann_type = 'info' WHERE ann_type IS NULL"))
            if 'updated_at' not in ann_cols:
                conn.execute(sa_text('ALTER TABLE announcements ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP'))
                conn.execute(sa_text('UPDATE announcements SET updated_at = COALESCE(updated_at, date_posted)'))

    if 'landing_page_sections' in tables:
        section_cols = {c['name'] for c in insp.get_columns('landing_page_sections')}
        with db.engine.begin() as conn:
            if 'content_json' not in section_cols:
                conn.execute(sa_text('ALTER TABLE landing_page_sections ADD COLUMN IF NOT EXISTS content_json TEXT'))
            if 'updated_at' not in section_cols:
                conn.execute(sa_text('ALTER TABLE landing_page_sections ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP'))

    if 'schedules' in tables:
        sched_cols = {c['name'] for c in insp.get_columns('schedules')}
        with db.engine.begin() as conn:
            if 'session_id' not in sched_cols:
                conn.execute(sa_text('ALTER TABLE schedules ADD COLUMN IF NOT EXISTS session_id VARCHAR(255)'))
            if 'schedule_type' not in sched_cols:
                conn.execute(sa_text('ALTER TABLE schedules ADD COLUMN IF NOT EXISTS schedule_type VARCHAR(50)'))
                conn.execute(sa_text("UPDATE schedules SET schedule_type = 'future' WHERE schedule_type IS NULL"))


def _seed_security_defaults() -> None:
    perm_defs = {
        'view_analytics': ('View analytics', 'View analytics dashboards and exports'),
        'edit_courses': ('Edit courses', 'Edit courses, instructors, and periods'),
        'manage_datasets': ('Manage datasets', 'Upload/delete course datasets'),
        'manage_users': ('Manage users', 'Manage users, roles, and registration fields'),
        'control_ai': ('Control AI features', 'Manage AI dashboards and settings'),
        'edit_landing_page': ('Edit landing page', 'Manage announcements and landing content'),
    }
    role_defs = {
        'admin': {'description': 'Full administrative access', 'is_system': True, 'permissions': set(RBAC_PERMISSION_KEYS)},
        'moderator': {'description': 'Limited admin access', 'is_system': True, 'permissions': {'view_analytics', 'edit_landing_page'}},
        'user': {'description': 'Default user role', 'is_system': True, 'permissions': set()},
    }

    permissions_by_key = {}
    for key, (label, description) in perm_defs.items():
        perm = Permission.query.filter_by(key=key).first()
        if not perm:
            perm = Permission(key=key, label=label, description=description)
            db.session.add(perm)
        permissions_by_key[key] = perm

    roles_by_name = {}
    for role_name, cfg in role_defs.items():
        role = Role.query.filter_by(name=role_name).first()
        if not role:
            role = Role(name=role_name, description=cfg['description'], is_system=cfg['is_system'])
            db.session.add(role)
        roles_by_name[role_name] = role

    db.session.flush()

    for role_name, cfg in role_defs.items():
        role = roles_by_name[role_name]
        wanted = {permissions_by_key[k] for k in cfg['permissions'] if k in permissions_by_key}
        role.permissions = list(wanted)

    reg_defaults = [
        {'field_key': 'career_interests', 'label': 'Career Interests', 'input_type': 'textarea', 'options_json': None, 'is_required': False, 'is_active': True, 'display_order': 10},
        {'field_key': 'gender', 'label': 'Gender', 'input_type': 'select', 'options_json': '["Male", "Female", "Prefer not to say"]', 'is_required': False, 'is_active': True, 'display_order': 20},
    ]
    for row in reg_defaults:
        exists = RegistrationField.query.filter_by(field_key=row['field_key']).first()
        if not exists:
            db.session.add(RegistrationField(**row))

    db.session.flush()

    admin_role = roles_by_name.get('admin')
    user_role = roles_by_name.get('user')

    for user in User.query.all():
        if user.role_id is None:
            if user.is_admin or str(user.role or '').strip().lower() == 'admin':
                user.role_id = admin_role.id if admin_role else None
            else:
                user.role_id = user_role.id if user_role else None

    admin_count = User.query.filter((User.is_admin.is_(True)) | (User.role_id == (admin_role.id if admin_role else -1))).count()
    if admin_count == 0:
        admin_email = (os.environ.get('ADMIN_EMAIL') or '').strip().lower()
        admin_password = os.environ.get('ADMIN_PASSWORD') or ''
        admin_username = (os.environ.get('ADMIN_USERNAME') or 'admin').strip()
        if admin_email and admin_password:
            existing = User.query.filter_by(email=admin_email).first()
            if not existing:
                existing = User(
                    username=admin_username,
                    full_name='System Administrator',
                    email=admin_email,
                    password=generate_password_hash(admin_password, method='pbkdf2:sha256'),
                )
                db.session.add(existing)
                db.session.flush()
            existing.is_admin = True
            existing.role = 'admin'
            existing.role_id = admin_role.id if admin_role else existing.role_id
        else:
            first_user = User.query.order_by(User.id.asc()).first()
            if first_user:
                first_user.is_admin = True
                first_user.role = 'admin'
                first_user.role_id = admin_role.id if admin_role else first_user.role_id
                app.logger.warning('No admin account existed; promoted earliest user id=%s to admin.', first_user.id)

    db.session.commit()


_bootstrap_database()

if __name__ == '__main__':
    app.run(debug=True)