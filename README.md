# CoursesScraper / Acadex

Acadex is a Flask-based academic planning platform for students and admins.

It supports:
- Student schedule planning (past + future)
- Prerequisite-aware course selection
- Admin analytics dashboards with drill-downs and export
- AI recommendation + schedule-generation foundations
- Landing page + announcement management

## Tech Stack

- Python + Flask
- SQLAlchemy + Flask-Migrate (Alembic)
- Flask-Login
- Jinja templates + Bootstrap
- Vanilla JS on front-end

## Core Capabilities

### Student
- Create and edit past/future schedules
- Add/remove courses with conflict and prerequisite checks
- Title-first prerequisite messaging (no raw backend codes in UI)
- AI recommendation preview for eligible future plans
- Degree progress and dashboard views

### Admin
- Manage users, subjects/areas, instructors, courses, periods, and datasets
- Dynamic analytics with filters (year/semester/major/minor/gender/year-of-study)
- Analytics export + override API/UI (`/admin/api/analytics/overrides`)
- Landing-page section control + announcement management
- AI dashboard and placeholder university integration settings

## Deploy-Ready Behavior

On startup the app can:
- Ensure required directories exist (`instance`, uploads)
- Create tables if needed (`db.create_all()` fallback)
- Attempt migration upgrade to latest revision

This makes first-run bootstrapping easier on fresh hosts.

## Environment Variables

Required:
- `SECRET_KEY`
- `DATABASE_URL` (recommended on Render: PostgreSQL connection string)

Optional:
- `UPLOAD_FOLDER` (default: `uploads`)
- `FLASK_ENV=production`
- `STRICT_MIGRATIONS=true` (fail startup if migration upgrade fails)
- `ADMIN_EMAIL` and `ADMIN_PASSWORD` (used to auto-bootstrap first admin if none exists)
- `ADMIN_USERNAME` (default: `admin`, used with admin bootstrap)

Database URL behavior:
- If `DATABASE_URL` starts with `postgres://`, the app rewrites it to `postgresql://` for SQLAlchemy compatibility.
- If `DATABASE_URL` is not set:
	- On Render (`RENDER` env var present), fallback is `sqlite:////tmp/acadex.db`.
	- Otherwise fallback is an app-local SQLite file under `instance/app.db`.

## Security & RBAC

- Database-backed roles: `admin`, `moderator`, `user`.
- Database-backed permissions:
	- `view_analytics`
	- `edit_courses`
	- `manage_datasets`
	- `manage_users`
	- `control_ai`
	- `edit_landing_page`
- All `/admin/*` routes are protected with per-request permission checks.
- Login rate limiting is enabled via `login_attempts` table (default: 5 failed attempts per 15 minutes).
- Profile and registration passwords are always stored as salted hashes.
- Global CSRF protection is enabled for all mutating requests (`POST/PUT/PATCH/DELETE`).
- Session cookies are hardened (`HttpOnly`, `SameSite=Lax`, and `Secure` in production).
- Global request throttling is enabled for login/registration/admin/student API mutation paths.
- Structured audit logs are emitted for critical mutating operations without sensitive payloads.

## Local Development

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

export SECRET_KEY=dev
export DATABASE_URL=sqlite:///local_dev.db

flask --app app db upgrade
flask --app app run --no-debugger --no-reload -p 5055
```

Health check:

```bash
curl http://127.0.0.1:5055/health
```

Expected: `200` and `{ "status": "ok" }`.

## Testing

From project root:

```bash
PYTHONPATH=. pytest -q
```

Current baseline in this branch: all tests passing.

## Project Layout

- `app.py` – bootstrap + blueprint registration
- `models.py` – ORM models
- `Routes/` – route modules (`auth`, `admin`, `student`, etc.)
- `services/` – business logic (AI, prerequisites, conflicts)
- `templates/` – Jinja templates
- `static/` – CSS/JS/assets
- `migrations/` – Alembic versions
- `utils.py` – shared helpers

## Recent Implementation Notes

- Added robust fallback loading for schedule editor/API when catalog joins are empty/missing.
- Added analytics metric override backend + admin UI editor.
- Strengthened future-only guards for AI generation paths.
- Improved prerequisite status display to prefer course titles.
- Expanded design-system based UI consistency (theme + icon harmonization).

## Tomorrow Handoff Notes

Recommended continuation checklist:
1. Run app locally on `5055` and spot-check student + admin dashboards.
2. Verify analytics override workflows with real dataset filters.
3. Continue refining AI ranking and recommendation explainability.
4. Optionally add focused tests around admin override payload paths.
