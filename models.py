# models.py
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

# Initialize the SQLAlchemy object
db = SQLAlchemy()


role_permissions = db.Table(
    'role_permissions',
    db.Column('role_id', db.Integer, db.ForeignKey('roles.id'), primary_key=True),
    db.Column('permission_id', db.Integer, db.ForeignKey('permissions.id'), primary_key=True),
)


course_faculty = db.Table(
    'course_faculty',
    db.Column('course_id', db.Integer, db.ForeignKey('courses.id'), primary_key=True),
    db.Column('faculty_id', db.Integer, db.ForeignKey('faculty.id'), primary_key=True),
)


faculty_advisor_area = db.Table(
    'faculty_advisor_area',
    db.Column('faculty_id', db.Integer, db.ForeignKey('faculty.id'), primary_key=True),
    db.Column('subject_area_id', db.Integer, db.ForeignKey('subject_areas.id'), primary_key=True),
)


class Role(db.Model):
    __tablename__ = 'roles'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    description = db.Column(db.String(255), nullable=True)
    is_system = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    permissions = db.relationship('Permission', secondary=role_permissions, lazy='subquery', backref='roles')


class Permission(db.Model):
    __tablename__ = 'permissions'

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(80), unique=True, nullable=False)
    label = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class RegistrationField(db.Model):
    __tablename__ = 'registration_fields'

    id = db.Column(db.Integer, primary_key=True)
    field_key = db.Column(db.String(80), unique=True, nullable=False)
    label = db.Column(db.String(120), nullable=False)
    input_type = db.Column(db.String(30), default='text', nullable=False)
    options_json = db.Column(db.Text, nullable=True)
    is_required = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    display_order = db.Column(db.Integer, default=0, nullable=False)


class LoginAttempt(db.Model):
    __tablename__ = 'login_attempts'

    id = db.Column(db.Integer, primary_key=True)
    identity = db.Column(db.String(255), nullable=False, index=True)
    ip_address = db.Column(db.String(64), nullable=True, index=True)
    success = db.Column(db.Boolean, default=False, nullable=False, index=True)
    attempted_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)


RBAC_PERMISSION_KEYS = (
    'view_analytics',
    'edit_courses',
    'manage_datasets',
    'manage_users',
    'control_ai',
    'edit_landing_page',
)

class User(db.Model, UserMixin):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    full_name = db.Column(db.String(150), nullable=True)
    email = db.Column(db.String(255), unique=True, nullable=True)
    password = db.Column(db.String(150), nullable=False)
    role = db.Column(db.String(50), default='student')
    role_id = db.Column(db.Integer, db.ForeignKey('roles.id'), nullable=True, index=True)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)

    # AI consent (opt-in)
    ai_opt_in = db.Column(db.Boolean, default=False, nullable=False)
    
    # Demographics
    gender = db.Column(db.String(50), nullable=True)
    career_interests = db.Column(db.Text, nullable=True)
    extra_profile_json = db.Column(db.Text, nullable=True)
    profile_image_path = db.Column(db.String(255), nullable=True)
    expected_graduation = db.Column(db.Integer, nullable=True)
    major_subject_area_id = db.Column(db.Integer, db.ForeignKey('subject_areas.id'), nullable=True)
    minor_subject_area_id = db.Column(db.Integer, db.ForeignKey('subject_areas.id'), nullable=True)
    two_factor_enabled = db.Column(db.Boolean, default=False, nullable=False)
    two_factor_secret = db.Column(db.String(255), nullable=True)
    
    # Relationship: If a user is deleted, all their schedules are automatically deleted
    schedules = db.relationship('Schedule', backref='student', lazy=True, cascade="all, delete-orphan")
    assigned_role = db.relationship('Role', backref='users', lazy=True)

    @property
    def display_name(self) -> str:
        return (self.full_name or self.username or '').strip()

    def effective_role_name(self) -> str:
        if self.assigned_role and self.assigned_role.name:
            return str(self.assigned_role.name).strip().lower()
        return str(self.role or 'user').strip().lower()

    def has_permission(self, permission_key: str) -> bool:
        role_name = self.effective_role_name()
        if self.is_admin or role_name == 'admin':
            return True
        if not self.assigned_role:
            return False
        wanted = str(permission_key or '').strip().lower()
        return any(str(p.key or '').strip().lower() == wanted for p in self.assigned_role.permissions)

    def can_access_admin_panel(self) -> bool:
        if self.is_admin or self.effective_role_name() in {'admin', 'moderator'}:
            return True
        return any(self.has_permission(k) for k in RBAC_PERMISSION_KEYS)

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
    level = db.Column(db.Integer, nullable=True)
    
    # Relationship: If a schedule is deleted, all its courses are automatically deleted
    courses = db.relationship('ScheduleCourse', backref='schedule', lazy=True, cascade="all, delete-orphan")

class ScheduleCourse(db.Model):
    __tablename__ = 'schedule_courses'

    __table_args__ = (
        db.Index(
            'ix_schedule_courses_course_term',
            'course_number',
            'semester',
            'year',
        ),
        db.Index(
            'ix_schedule_courses_identity',
            'course_number',
            'day_period',
            'semester',
            'year',
            'dataset_name',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    schedule_id = db.Column(db.Integer, db.ForeignKey('schedules.id'), nullable=False)

    # Base catalog code used as the primary human-readable course reference.
    # This anchors identity matching before section/term disambiguation is applied.
    course_number = db.Column(db.String(100), nullable=False)

    # Captures section timing identity (for example, "Mon 3 Wed 3" or "TBA").
    # This prevents different offerings of the same course number from collapsing into one entry.
    day_period = db.Column(db.String(255), nullable=False)

    # Term dimension used to distinguish offerings across academic cycles.
    # Required so identity remains stable even when course numbers repeat every year.
    semester = db.Column(db.String(50), nullable=False)

    # Year dimension paired with semester for complete term-level identity.
    # Stored as text to match existing catalog conventions and preserve source fidelity.
    year = db.Column(db.String(20), nullable=False)

    # Dataset context for resolving course instances when multiple datasets contain the same course/term tuple.
    # Nullable to support transitional/legacy rows and manual catalog entries with no dataset label.
    dataset_name = db.Column(db.String(150), nullable=True)

    # Optional provenance marker (for example: csv, scraped, manual).
    # Helps tie an instance back to its source pipeline when identity conflicts are investigated.
    source = db.Column(db.String(20), nullable=True)

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


class Course(db.Model):
    __tablename__ = 'courses'

    id = db.Column(db.Integer, primary_key=True)
    course_number = db.Column(db.String(120), nullable=False, index=True)
    title = db.Column(db.String(300), nullable=False)
    credits = db.Column(db.Float, nullable=True)
    day_period = db.Column(db.String(255), nullable=False, index=True)
    semester = db.Column(db.String(50), nullable=True, index=True)
    year = db.Column(db.String(20), nullable=True, index=True)
    year_level = db.Column(db.Integer, nullable=True, index=True)
    prerequisite = db.Column(db.Text, nullable=True)
    subject_area = db.Column(db.String(255), nullable=True, index=True)
    instructor = db.Column(db.String(255), nullable=True, index=True)
    source = db.Column(db.String(20), default='scraped', nullable=False, index=True)
    dataset_name = db.Column(db.String(150), nullable=True, index=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    faculty = db.relationship('Faculty', secondary=course_faculty, backref='courses')


class Faculty(db.Model):
    __tablename__ = 'faculty'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    advisor_areas = db.relationship('SubjectArea', secondary=faculty_advisor_area, backref='advisors')

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
    dataset = db.Column(db.String(150), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class DatasetRegistry(db.Model):
    __tablename__ = 'dataset_registry'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False, index=True)
    source = db.Column(db.String(20), nullable=False, index=True)  # csv | scraped
    year = db.Column(db.String(20), nullable=True, index=True)
    semester = db.Column(db.String(50), nullable=True, index=True)
    dataset_type = db.Column(db.String(20), nullable=False, default='term', index=True)  # term | full_year
    table_name = db.Column(db.String(150), nullable=False, unique=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    is_active = db.Column(db.Boolean, default=False, nullable=False, index=True)


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


# ──────────────────────────────────────────────────────────────────────────────
#  Legacy dataset capture models
# ──────────────────────────────────────────────────────────────────────────────

class ScrapedDataset(db.Model):
    """Tracks a single scraping run — one record per admin-initiated scrape."""
    __tablename__ = 'scraped_datasets'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)          # e.g. Scraped_2026_Spring
    year = db.Column(db.String(20), nullable=False)
    semester = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(20), default='idle', nullable=False)
    # idle | running | completed | failed

    # Fields the admin chose to scrape (JSON list)
    selected_fields_json = db.Column(db.Text, nullable=True)

    # Live progress counters
    total_courses = db.Column(db.Integer, default=0, nullable=False)
    current_index = db.Column(db.Integer, default=0, nullable=False)
    last_course = db.Column(db.String(255), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow,
        onupdate=datetime.utcnow, nullable=False,
    )

    courses = db.relationship(
        'ScrapedCourse', backref='dataset', lazy=True,
        cascade='all, delete-orphan',
    )


class ScrapedCourse(db.Model):
    """One scraped course row belonging to a ScrapedDataset."""
    __tablename__ = 'scraped_courses'

    id = db.Column(db.Integer, primary_key=True)
    dataset_id = db.Column(
        db.Integer, db.ForeignKey('scraped_datasets.id'),
        nullable=False, index=True,
    )
    # JSON-encoded dict of field → value pairs chosen during the scraping run
    data_json = db.Column(db.Text, nullable=False, default='{}')

    # Canonical searchable columns sourced from scrape output
    course_number = db.Column(db.String(120), nullable=True, index=True)
    course_title = db.Column(db.String(300), nullable=True, index=True)
    credits = db.Column(db.Float, nullable=True)
    instructor = db.Column(db.String(255), nullable=True, index=True)
    day_and_period = db.Column(db.String(255), nullable=True, index=True)
    area_of_study = db.Column(db.String(255), nullable=True, index=True)
    prerequisite = db.Column(db.Text, nullable=True)

    # Any dynamic fields not mapped to canonical columns
    additional_attributes_json = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
