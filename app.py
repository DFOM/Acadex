# app.py
from flask import Flask
from flask_login import LoginManager
import os
from models import db, User
from flask_migrate import Migrate

from Routes.auth_routes import auth_bp
from Routes.admin_routes import admin_bp
from Routes.main_routes import main_bp
from Routes.student_routes import student_bp
from Routes.schedule_routes import schedule_bp

app = Flask(__name__)

app.secret_key = os.environ.get('SECRET_KEY')
if not app.secret_key:
    raise RuntimeError("SECRET_KEY must be set as an environment variable.")

database_url = os.environ.get('DATABASE_URL')
if not database_url:
    raise RuntimeError("DATABASE_URL must be set as an environment variable.")

if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

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

if __name__ == '__main__':
    app.run(debug=True)