# models.py
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

# Initialize the SQLAlchemy object
db = SQLAlchemy()

class User(db.Model, UserMixin):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=True)
    password = db.Column(db.String(150), nullable=False)
    role = db.Column(db.String(50), default='student')
    is_admin = db.Column(db.Boolean, default=False, nullable=False)

    # AI consent (opt-in)
    ai_opt_in = db.Column(db.Boolean, default=False, nullable=False)
    
    # Demographics
    gender = db.Column(db.String(50), nullable=True)
    expected_graduation = db.Column(db.Integer, nullable=True)
    major_subject_area_id = db.Column(db.Integer, db.ForeignKey('subject_areas.id'), nullable=True)
    minor_subject_area_id = db.Column(db.Integer, db.ForeignKey('subject_areas.id'), nullable=True)
    
    # Relationship: If a user is deleted, all their schedules are automatically deleted
    schedules = db.relationship('Schedule', backref='student', lazy=True, cascade="all, delete-orphan")

class Schedule(db.Model):
    __tablename__ = 'schedules'
    id = db.Column(db.Integer, primary_key=True)
    
    # MODIFIED FOR GUEST MODE: 
    # user_id is now nullable so anonymous users can save schedules
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    
    # NEW FOR GUEST MODE: Tracks anonymous browser sessions
    session_id = db.Column(db.String(255), nullable=True)
    
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
    area_type = db.Column(db.String(20), default='generic', nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    is_required = db.Column(db.Boolean, default=False, nullable=False)
    required_for_paths = db.Column(db.Text, nullable=True)
    min_depth_credits = db.Column(db.Float, default=0.0)
    min_major_credits = db.Column(db.Float, default=0.0, nullable=False)
    min_minor_credits = db.Column(db.Float, default=0.0, nullable=False)

class Period(db.Model):
    __tablename__ = 'periods'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    start_time = db.Column(db.String(50))
    end_time = db.Column(db.String(50))

class Setting(db.Model):
    __tablename__ = 'settings'
    key = db.Column(db.String(100), primary_key=True)
    # Changed from String(255) to db.Text to accommodate long paragraphs for the Landing Page
    value = db.Column(db.Text)

class Announcement(db.Model):
    __tablename__ = 'announcements'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    content = db.Column(db.Text, nullable=False)
    date_posted = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True) # Allows Admins to hide announcements without deleting them

    # New: announcement classification (Info / Update / Warning)
    ann_type = db.Column(db.String(20), default='info', nullable=False)

    # New: update timestamp for edits
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class LandingPageSection(db.Model):
    __tablename__ = 'landing_page_sections'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)  # e.g. hero, features, demo
    enabled = db.Column(db.Boolean, default=True, nullable=False)
    display_order = db.Column(db.Integer, default=0, nullable=False)

    # Flexible section configuration (hero text, features list, etc.)
    content_json = db.Column(db.Text, nullable=True)

    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class CatalogStatus(db.Model):
    __tablename__ = 'catalog_statuses'

    id = db.Column(db.Integer, primary_key=True)
    term = db.Column(db.String(100), unique=True, nullable=False)  # e.g. "Spring 2026"
    status = db.Column(db.String(20), default='available', nullable=False)  # processing | available
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class AIEvent(db.Model):
    __tablename__ = 'ai_events'

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    schedule_id = db.Column(db.Integer, db.ForeignKey('schedules.id'), nullable=True, index=True)

    event_type = db.Column(db.String(50), nullable=False, index=True)
    route = db.Column(db.String(200), nullable=True)
    metadata_json = db.Column(db.Text, nullable=True)


class BehaviorEvent(db.Model):
    __tablename__ = 'behavior_events'

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Either user_id (authenticated) or session_id (guest) may be present.
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    session_id = db.Column(db.String(255), nullable=True, index=True)
    schedule_id = db.Column(db.Integer, db.ForeignKey('schedules.id'), nullable=True, index=True)

    event_type = db.Column(db.String(50), nullable=False, index=True)
    route = db.Column(db.String(200), nullable=True)
    metadata_json = db.Column(db.Text, nullable=True)


class Notification(db.Model):
    __tablename__ = 'notifications'

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Notifications can be scoped to either a user (authenticated) or a guest session,
    # or left unset for broadcast/system notifications.
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    session_id = db.Column(db.String(255), nullable=True, index=True)

    notif_type = db.Column(db.String(30), default='info', nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False)
    url = db.Column(db.String(300), nullable=True)

    is_read = db.Column(db.Boolean, default=False, nullable=False, index=True)
    read_at = db.Column(db.DateTime, nullable=True)

    metadata_json = db.Column(db.Text, nullable=True)
