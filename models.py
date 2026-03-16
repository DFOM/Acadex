# models.py
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin

# Initialize the SQLAlchemy object
db = SQLAlchemy()

class User(db.Model, UserMixin):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    role = db.Column(db.String(50), default='student')
    
    # Demographics
    gender = db.Column(db.String(50), nullable=True)
    # FIXED: Replaced year_of_study with a concrete Expected Graduation Year
    expected_graduation = db.Column(db.Integer, nullable=True)
    
    # Relationship: If a user is deleted, all their schedules are automatically deleted
    schedules = db.relationship('Schedule', backref='student', lazy=True, cascade="all, delete-orphan")

class Schedule(db.Model):
    __tablename__ = 'schedules'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    schedule_name = db.Column(db.String(150), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    semester = db.Column(db.String(50), nullable=False)
    schedule_type = db.Column(db.String(50), default='future') # past or future
    
    # Relationship: If a schedule is deleted, all its courses are automatically deleted
    courses = db.relationship('ScheduleCourse', backref='schedule', lazy=True, cascade="all, delete-orphan")

class ScheduleCourse(db.Model):
    __tablename__ = 'schedule_courses'
    id = db.Column(db.Integer, primary_key=True)
    schedule_id = db.Column(db.Integer, db.ForeignKey('schedules.id'), nullable=False)
    course_number = db.Column(db.String(100), nullable=False)
    grade = db.Column(db.String(5))

class SubjectArea(db.Model):
    __tablename__ = 'subject_areas'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), unique=True, nullable=False)
    min_depth_credits = db.Column(db.Float, default=0.0)

class Period(db.Model):
    __tablename__ = 'periods'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    start_time = db.Column(db.String(50))
    end_time = db.Column(db.String(50))

class Setting(db.Model):
    __tablename__ = 'settings'
    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.String(255))