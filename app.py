# app.py
from flask import Flask
from flask_login import LoginManager
import os
from models import db, User
from flask_migrate import Migrate

# Import Blueprints
from Routes.auth_routes import auth_bp
from Routes.admin_routes import admin_bp
from Routes.student_routes import student_bp
from Routes.schedule_routes import schedule_bp

app = Flask(__name__)

# --- NEW: Environment Variables for Security ---
# This looks for a live SECRET_KEY, but falls back to a default on your laptop
app.secret_key = os.environ.get('SECRET_KEY', 'jcxa%rUdH+Z+{WDaHc.w+5h-qB&zxw!K')

# --- NEW: Dynamic Database Selection ---
# Look for a live PostgreSQL URL. If none exists, use the local SQLite file.
database_url = os.environ.get('DATABASE_URL', 'postgresql://university_db_jt42_user:JpymFt7bc734Y07JPiFUrt78dNzz6rsW@dpg-d6s1c9a4d50c73bevgpg-a/university_db_jt42')

# SQLAlchemy requires 'postgresql://' but Render gives us 'postgres://'. This fixes that!
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize the database and migration tools with the app
db.init_app(app)
migrate = Migrate(app, db)

# --- Login Manager Setup ---
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- Register Blueprints ---
app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp, url_prefix='/admin')
app.register_blueprint(student_bp)
app.register_blueprint(schedule_bp)

if __name__ == '__main__':
    app.run(debug=True)