# app.py
from flask import Flask, jsonify
from flask_login import LoginManager
import os
from pathlib import Path
from werkzeug.security import generate_password_hash
from models import db, User, Role, Permission, RegistrationField, RBAC_PERMISSION_KEYS
from flask_migrate import Migrate, upgrade

from Routes.auth_routes import auth_bp
from Routes.admin_routes import admin_bp
from Routes.main_routes import main_bp
from Routes.student_routes import student_bp
from Routes.schedule_routes import schedule_bp

app = Flask(__name__)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-me')
if os.environ.get('FLASK_ENV') == 'production' and app.secret_key == 'dev-secret-change-me':
    raise RuntimeError('SECRET_KEY must be set in production.')

def _resolve_database_url() -> str:
    database_url = os.environ.get('DATABASE_URL')

    if database_url and database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    if not database_url:
        if os.environ.get('RENDER'):
            return 'sqlite:////tmp/acadex.db'
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

db.init_app(app)
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


@app.get('/health')
def health_check():
    return jsonify({'status': 'ok'}), 200


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
            if app.config.get('STRICT_MIGRATIONS', False):
                raise
            app.logger.warning("Automatic migration upgrade skipped: %s", exc)

        try:
            _seed_security_defaults()
        except Exception as exc:
            if app.config.get('STRICT_MIGRATIONS', False):
                raise
            app.logger.warning('Security defaults bootstrap skipped until schema is updated: %s', exc)


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