# app.py
from flask import Flask, jsonify
from flask_login import LoginManager
import os
from pathlib import Path
from models import db, User
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


_bootstrap_database()

if __name__ == '__main__':
    app.run(debug=True)