# Acadex Functionality Guide

**Version covered:** Beta 1.8.0  
**Audience:** students, administrators, maintainers, and deployment operators  
**Purpose:** describe every major capability in Acadex, what each capability does, and how the application implements it.

## 1. Product overview

Acadex is a Flask-based academic planning platform for students and administrators. It combines schedule planning, prerequisite-aware course discovery, graduation requirement tracking, faculty/course ratings, dataset administration, analytics, announcements, and operational health tooling in one web application.

The application is organized around Flask blueprints:

- `main`: public landing page and public announcement APIs.
- `auth`: student authentication, registration, logout, and schedule-type selection.
- `student`: student catalog, schedule planning, dashboard, ratings, notifications, AI recommendations, and profile features.
- `schedule`: compatibility routes for schedule dashboard and deletion.
- `admin`: administrative dashboard, dataset management, user/role management, catalog/faculty management, analytics, AI settings, activity logs, and system health.

The data layer uses SQLAlchemy models for users, roles, schedules, courses, faculty, subject areas, periods, settings, announcements, datasets, metadata fields, ratings, AI events, behavior events, and notifications. The runtime can use a configured database URL, derive PostgreSQL connection strings from separate `PG*` environment variables, or fall back to SQLite for local development.

## 2. User roles and permissions

### What it does

Acadex supports normal student users plus administrators. Administrators can be granted permissions through roles, while legacy admin flags are still honored for compatibility.

### How it works

- `User` records store identity, authentication details, demographics, selected schedule type, optional avatar, AI consent, and role/admin fields.
- `Role` and `Permission` records define named access bundles.
- `User.has_permission()` checks assigned role permissions and falls back to admin status.
- Admin routes use access-control helpers to deny users who are neither admins nor holders of the required permission.
- Registration fields are data-driven, allowing admins to control additional profile fields collected at sign-up.

## 3. Authentication and onboarding

### Login

**What it does:** lets existing users sign in and redirects them into the appropriate student or admin experience.

**How it works:** the login route validates username/email and password, records login attempts, applies configurable rate limiting, supports legacy password upgrade behavior, stores the authenticated session through Flask-Login, and routes admins to the admin dashboard or students to schedule-type selection/dashboard flows.

### Registration

**What it does:** lets new students create accounts with required and configurable demographic fields.

**How it works:** registration reads active `RegistrationField` definitions, validates required fields and uniqueness, hashes passwords, creates a `User`, persists demographic values, and logs the user in when complete.

### Logout and schedule-type selection

**What it does:** ends sessions and lets students declare whether they are planning a four-year or transfer-style path.

**How it works:** logout clears the Flask-Login session; schedule-type selection stores the chosen path on the user/session so eligibility and recommendation logic can use it later.

## 4. Public landing page and announcements

### What it does

Acadex includes a public landing page with configurable content sections and announcements. Visitors can see active announcements, and the app can track which announcement IDs have been seen in the current session.

### How it works

- Landing sections and announcements are stored in database models.
- Public routes fetch active sections and announcements in display order.
- Announcement APIs return currently active announcements and accept a mark-seen payload for session-level dismissal.
- Admin landing-management routes create, edit, toggle, and delete announcements and update landing-page content.

## 5. Student course catalog and search

### What it does

Students can browse courses, search by text, filter by academic level, inspect catalog details, and view prerequisite/dependency relationships.

### How it works

- Course data is read from uploaded datasets plus local course records.
- Dataset context helpers resolve the most relevant dataset for a schedule year/semester/source.
- Metadata lookup prefers schedule-course identity (course number, day/period, semester, year, dataset/source) before falling back to normalized course codes.
- Course search normalizes titles, numbers, instructors, semesters, and day/period values for tolerant matching.
- Level filtering can use explicit year-level metadata or parse course-number patterns when explicit metadata is missing.
- Course detail APIs return catalog metadata, prerequisites, dependency information, schedule identity, rating summaries, faculty information, and dynamic visible metadata fields.

## 6. Prerequisite and dependency intelligence

### What it does

Acadex helps students understand whether a course depends on prior coursework and shows courses that depend on a selected course.

### How it works

- Prerequisite text and metadata are scanned across available course rows.
- The dependency graph endpoint builds prerequisite and dependent-course nodes for the selected course.
- Historical/course metadata fallback logic improves matches when datasets contain duplicate course numbers or section-specific rows.
- Requirement and eligibility helpers compare completed/planned coursework and grades against prerequisite-like course references.

## 7. Student schedule planning

### Schedule creation

**What it does:** students create schedules for a chosen year and semester.

**How it works:** create-schedule routes validate the requested term, apply schedule-type/year eligibility, resolve the best dataset name for that context, and persist a `Schedule` row owned either by a signed-in user or guest/session identity.

### Schedule editing

**What it does:** students add and remove section-level courses in a visual schedule grid.

**How it works:** the editor loads schedule courses through an API. The browser script parses day/period strings, understands compact tokens and ranges, normalizes day and period labels, and finds matching grid cells by data attributes or fallback text comparisons. Adding a course validates ownership, resolves course metadata, detects duplicate section identities, checks time conflicts, and saves a `ScheduleCourse` row with section identity fields such as day/period, semester, year, dataset name, and source.

### Course removal, schedule deletion, and grade updates

**What it does:** students can remove planned entries, delete entire schedules, and record grades for completed courses.

**How it works:** API routes validate ownership, modify `ScheduleCourse`/`Schedule` rows, and recompute dashboard progress. Grade updates persist grades on schedule-course records and affect completed-credit calculations.

## 8. Conflict detection and timetable parsing

### What it does

Acadex prevents or flags schedule conflicts when two courses meet at overlapping times.

### How it works

- Period definitions store start/end times.
- Day/period parsing converts course meeting strings into structured day/period blocks.
- Time-window helpers convert those blocks into minute ranges.
- Overlap helpers compare windows on the same day.
- The front-end schedule grid uses normalized day/period tokens and data attributes to place courses into the correct timetable cells.

## 9. Student dashboard and graduation progress

### What it does

The dashboard summarizes total credits, major/minor progress, area-of-study progress, graduation requirement checklist status, GPA-like completion signals, upcoming/planned work, and recommendation entry points.

### How it works

- The dashboard aggregates schedule courses by grade status, semester/year, subject area, and schedule identity.
- Graduation requirement helpers compute completed units, planned units, required units, metric type, and labels.
- Area progress includes selected major/minor requirement areas, earned credits, in-progress credits, total available credits, requirement remaining, and capacity remaining.
- Dashboard templates render progress cards and bars for acquired, planned, and available remaining credits.
- Missing requirement information is surfaced as remaining work or checklist items.

## 10. Recommendations and schedule generation

### What it does

Acadex can preview recommendation inputs, list recommended courses, and generate suggested schedules.

### How it works

- Recommendation APIs combine completed/planned courses, prerequisites, current schedule type, selected year/semester, available datasets, time conflicts, and course metadata.
- Preview endpoints expose recommendation context for the UI.
- Generation endpoints select candidate courses, filter by eligibility and conflicts, and return proposed course sets for student review.
- AI and behavior-event logging can capture recommendation usage when collection is enabled and the user has consented where required.

## 11. Student faculty browser

### What it does

Students can browse faculty, filter by department, view faculty profiles, see current/historical courses, and rate faculty.

### How it works

- Faculty records store names, titles, departments, bios, and linked courses.
- Detail APIs serialize current courses, transferred courses, discontinued courses, advisor areas, and rating summaries.
- Faculty course status logic distinguishes currently offered, transferred, and discontinued courses by comparing historical rows to active datasets.

## 12. Ratings system

### What it does

Students can rate courses and faculty across multiple dimensions, then review their own submitted ratings. Administrators can inspect aggregate rating analytics.

### How it works

- Course ratings store overall score, content quality, workload, usefulness, materials quality, difficulty, take-again flag, and comments.
- Faculty ratings store overall score, teaching quality, communication, availability, grading fairness, engagement, difficulty, recommend flag, and comments.
- Rating APIs enforce one rating per user per target through uniqueness rules and update-or-create behavior.
- Student ratings pages query the current user’s submissions.
- Admin analytics aggregate averages, percentages, and correlation-style chart inputs.

## 13. Notifications

### What it does

Acadex shows student notifications, unread counts, and mark-read controls. Notifications are used for system messages such as dataset deletion impacts.

### How it works

- Notification records store user, title, message, type, read timestamp, and creation timestamp.
- Student routes return paginated/list data, unread counts, single mark-read operations, and mark-all-read operations.
- Guest/session helpers can track notification visibility for non-authenticated contexts where applicable.

## 14. Student profile and preferences

### What it does

Students can maintain profile information, upload/view profile images, review preferences, and manage AI consent.

### How it works

- Profile routes update user fields and demographic values.
- Profile-image handling validates allowed image extensions and serves the stored avatar path or fallback.
- Preferences and AI-consent routes store user-level choices used by recommendation and telemetry logic.

## 15. Admin dashboard

### What it does

The admin dashboard gives operators a high-level view of platform state, including users, courses, schedules, datasets, and health indicators.

### How it works

Admin index routes collect counts and summary metrics from the database, then render management links and status cards. Access is guarded by admin/permission checks.

## 16. Dataset management

### What it does

Administrators can upload course datasets, preview mappings, list datasets, refresh dataset registry state, delete datasets safely, force-delete problematic datasets, inspect dataset columns, and clean orphan/invalid dataset artifacts.

### How it works

- Upload routes accept CSV-like datasets, detect/match columns, normalize rows, and store data in local course tables and registry records.
- Dataset registry rows track dataset names, source, year, semester/type, activity, and table metadata.
- Preview and mapping APIs help admins align source columns with canonical course fields.
- Impact APIs count linked schedules and users before destructive deletion.
- Delete flows remove course rows, linked schedule entries, registry rows, and notify affected users.
- Force-delete and cleanup endpoints handle stale registry rows, orphan tables, and invalid dataset records.

## 17. Dynamic course metadata schema

### What it does

Acadex can ingest and display arbitrary course metadata fields without requiring a database migration for each new source column.

### How it works

- Dataset ingestion maps known columns to canonical course fields and stores additional fields as metadata extras.
- `CourseMetadataField` records track discovered metadata keys, labels, visibility, source, and active state.
- Admin APIs list/create metadata fields and change visibility.
- Student course-detail serializers include only fields marked visible for students, while admin flows retain full metadata.

## 18. Course and faculty administration

### What it does

Admins can create, edit, duplicate, delete, and inspect courses and faculty. They can reassign placeholder/TBD faculty to real faculty members and manage instructor records.

### How it works

- Course APIs merge local and scraped/dataset courses into serializable rows.
- Course refs encode whether an item came from local or scraped data.
- Course mutations update canonical fields, metadata extras, subject areas, faculty links, and section identity values.
- Faculty APIs serialize active, transferred, and discontinued course states.
- Faculty reassignment moves selected courses from placeholder faculty to target faculty and syncs course instructor text.

## 19. Subject areas, periods, and settings

### What it does

Admins configure academic subject areas, minimum major/minor credits, timetable periods, lunch settings, registration fields, and app-wide settings that influence recommendation, eligibility, analytics, security, and display behavior.

### How it works

- Subject-area routes update names, descriptions, active flags, and credit requirements.
- Period routes create/update/delete named timetable periods with start/end times.
- Settings routes persist key/value settings used by student and admin helpers.
- Lunch settings and period times feed schedule conflict detection and grid display.

## 20. User, role, and registration administration

### What it does

Admins can review users, delete users, assign roles, manage roles/permissions, and control registration fields.

### How it works

- User management routes query users with roles and demographic fields.
- Role routes create/update permission bundles and assign them to users.
- Registration-field routes control label, field type, required status, option values, and display order.
- Delete-user APIs remove or deactivate user-owned records according to route logic.

## 21. Analytics and exports

### What it does

Acadex provides admin analytics dashboards, chart data APIs, override settings, CSV-style exports, and ratings overview analytics.

### How it works

- Analytics routes aggregate schedules, courses, users, datasets, subject areas, ratings, and enrollment patterns.
- Data APIs return chart-ready JSON.
- Override APIs allow administrators to tune analytics display values without code changes.
- Export routes generate downloadable analytics data.
- Ratings overview aggregates course and faculty dimensions and recommendation/take-again percentages.

## 22. AI settings and telemetry

### What it does

Admins can configure AI/recommendation behavior and export AI or behavior event logs. Students can consent to AI-related data collection.

### How it works

- AI settings are stored in application settings.
- Student AI consent is stored on the user profile.
- Event logging helpers check collection settings and consent before inserting `AIEvent` or `BehaviorEvent` records.
- Admin export endpoints stream collected events as CSV for audit or analysis.

## 23. Activity log and system health

### What it does

Operators can inspect system activity, runtime health, schema status, process metrics, action logs, and run selected health-fix actions.

### How it works

- Request hooks record mutating admin/student/schedule requests into bounded action logs.
- Health APIs expose database connectivity, table/schema checks, audit data, version information, active session counts, and process metrics.
- Health-fix actions can perform targeted cleanup or repair operations.
- Activity endpoints expose recent activity/action-log data for admin review.

## 24. Runtime, deployment, and database bootstrap

### What it does

Acadex can start reliably in local, Render/Railway-style, and PostgreSQL-backed environments.

### How it works

- Runtime config resolves `DATABASE_URL`, translates legacy `postgres://` URLs to SQLAlchemy-compatible `postgresql://`, or builds PostgreSQL URLs from `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, and `PGDATABASE`.
- Runtime directory helpers create required instance/upload directories and SQLite parent directories.
- Database bootstrap checks existing tables, creates missing schema in development-style contexts, stamps Alembic when tables exist but migration state is missing, and runs migrations when possible.
- Security hooks set CSRF tokens, security headers, basic rate limits, and error handlers.

## 25. Data model summary

Acadex persists the following main object types:

- **Access and identity:** `User`, `Role`, `Permission`, `RegistrationField`, `LoginAttempt`.
- **Planning:** `Schedule`, `ScheduleCourse`, `SubjectArea`, `Course`, `Period`.
- **Content and catalog:** `Faculty`, `Announcement`, `LandingPageSection`, `CatalogStatus`, `DatasetRegistry`, `CourseMetadataField`, `ScrapedDataset`, `ScrapedCourse`.
- **Feedback and telemetry:** `CourseRating`, `FacultyRating`, `AIEvent`, `BehaviorEvent`, `Notification`.
- **Configuration:** `Setting`.

These models are connected through ownership fields, foreign keys, uniqueness constraints, and helper methods. Schedule-course identity fields preserve section-level provenance so dashboards, recommendations, and detail screens can resolve the correct catalog metadata.

## 26. API surface summary

### Public and authentication

- `GET /`: landing page.
- `GET /api/landing/announcements`: active landing announcements.
- `POST /api/landing/announcements/mark_seen`: mark announcements as seen in session.
- `GET|POST /login`: authenticate.
- `GET|POST /register`: create account.
- `GET /logout`: sign out.
- `GET /choose_schedule_type`: choose planning path.

### Student

- `GET /student/courses`, `/student/courses/search`: browse/search catalog.
- `GET /student/api/course_detail`, `/student/api/catalog_course_detail`, `/student/api/course-details/<id>`: course detail and dependency graph.
- `GET /student/faculty`, `/student/api/faculty/<id>`: faculty browser and detail.
- `GET /student/ratings`, `/student/api/ratings/my`: ratings pages and current-user history.
- `POST /student/api/rate/course`, `/student/api/rate/faculty`: submit/update ratings.
- `GET /student/dashboard`: progress dashboard.
- `GET|POST /student/profile`, `GET /student/preferences`, `GET /student/profile/image`: profile and preferences.
- Notification APIs under `/student/api/notifications...`: list, counts, mark one/all read.
- `POST /student/ai/consent`: update AI consent.
- `GET|POST /student/schedule/create`: create schedules.
- `GET /student/schedule/edit/<id>`: edit a schedule.
- Schedule APIs under `/student/api/...`: list schedule courses, add/remove courses, delete schedule, update grades, preview/get recommendations, and generate schedules.

### Admin

- `GET /admin/`: admin dashboard.
- Landing and announcement management under `/admin/landing_management` and `/admin/announcement/...`.
- Dataset management under `/admin/datasets...` and `/admin/api/datasets...`.
- Metadata-field APIs under `/admin/api/course-metadata-fields...`.
- Settings, periods, users, roles, registration fields, subjects, instructors, courses, and faculty management under their respective `/admin/...` routes.
- Health and repair under `/admin/health`, `/admin/api/health-metrics`, `/admin/api/health-fix`, `/admin/api/system-reset`, and `/admin/api/system/restore`.
- Analytics under `/admin/analytics`, `/admin/api/analytics/data`, `/admin/api/analytics/overrides`, `/admin/analytics/export`, and `/admin/api/ratings/overview`.
- AI administration under `/admin/ai`, `/admin/ai/settings`, `/admin/ai/export_events.csv`, and `/admin/ai/export_behavior_events.csv`.
- Activity logs under `/admin/activity` and `/admin/api/activity`.

## 27. Operational notes

- Run the app through the configured Procfile/gunicorn command in production-like hosting.
- Use `pytest -q` for regression tests.
- Use migrations for schema changes, and rely on bootstrap helpers only for compatibility and startup safety.
- Keep dataset identity fields intact when modifying schedule-course logic because metadata, conflict detection, and recommendations rely on section-level provenance.
- Treat AI and behavior telemetry as configurable and consent-aware data flows.
