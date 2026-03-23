# CoursesScraper / Acadex

Acadex is a Flask-based academic planning platform for students and admins.

It supports:
- Student schedule planning (past + future)
- Prerequisite-aware course selection
- Admin analytics dashboards with drill-downs and export
- AI recommendation + schedule-generation foundations
- Landing page + announcement management

## Version

- Current version: **Beta 1.1**

## Changelog

### Beta 1.1 — 2026-03-23

- Upgraded CSV ingestion to preserve course history by term-scoping updates (`course_number + day_period + year + semester + dataset_name`) instead of cross-term overwrites.
- Added automatic split behavior for mixed Fall/Spring CSV uploads so local CSV course rows and registry entries are stored under semester-specific dataset names.
- Enforced schedule creation options from dataset registry availability only, with strict past/future term windows.
- Added future-planning fallback logic (up to `current_year + 5`) so unavailable requested terms resolve to the nearest available registry term with a warning.
- Updated create-schedule UI to dynamically load semester options per selected year from registry-backed availability.
- Added odd/even offering filtering support by excluding rows tagged in course text as odd-only/even-only when year parity does not match.
- Improved global theme visibility by introducing generic color aliases (`--text-primary`, `--text-secondary`, `--background`, `--surface`) and applying contrast-safe defaults to cards/tables/forms.

### Beta 1 — 2026-03-23

- Fixed CSV mapping engine application by enforcing centralized mapped payload extraction per row.
- Added normalized mapping aliases for `subject_area`, `day_period`, and `year_level` in `config/data_mapping.py`.
- Fixed semester/year parsing for labels like `Fall Semester 2025` -> `semester=Fall`, `year=2025`.
- Added `year_level` extraction from `Year Available` (e.g., `1Year` -> `1`) and persisted it to `Course.year_level`.
- Added automatic dataset naming from detected CSV term coverage:
	- `YYYY Full Course List`
	- `YYYY Fall Course List`
	- `YYYY Spring Course List`
- Added mandatory row-level parser debug logs including extracted subject area/day period and structured row payload summaries.
- Simplified faculty table course display to title-only labels in admin faculty APIs/UI payloads.

### Beta 0.2 — 2026-03-18

- Completed migration of remaining route flows from legacy CSV table helpers to scraper-backed models.
- Refactored student schedule editor, add-course validation, schedule course APIs, and AI generation to use `ScrapedDataset`/`ScrapedCourse` metadata.
- Refactored admin instructors/courses management and analytics metadata paths to use scraper datasets.
- Refactored landing and dashboard catalog visibility to derive from completed scraped datasets.
- Removed deprecated legacy CSV helper functions from `utils.py` (`get_course_data_tables`, `get_best_course_table`, semester/table column helpers).

### Beta 0.1 — 2026-03-18

- Introduced scraper-first dataset architecture with canonical `scraped_courses` columns and dynamic attributes.
- Added robust scraper popup lifecycle handling, configurable row transition delay, and pause/resume controls.
- Added async-friendly scraper controls in admin integration UI and expanded scraper state reporting.
- Added a new Admin System Health tab with real-time app/scraper metrics and version-control visibility.
- Initialized versioning protocol for future structural changes.

## Versioning Protocol

- All structural changes must increment `VERSION` in `app.py`.
- Each structural release must add a dated changelog entry in this `README.md`.
- Commit messages for structural changes should include the target version label (for example: `Beta 0.2`).

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
- `SESSION_SECRET` (preferred; stable across deployments)
- `DATABASE_URL` (recommended on Render: PostgreSQL connection string)

Optional:
- `SECRET_KEY` (legacy fallback if `SESSION_SECRET` is unset)
- `JWT_SECRET` (used when JWT-based features are enabled)
- `UPLOAD_FOLDER` (default: `uploads`)
- `FLASK_ENV=production`
- `STRICT_MIGRATIONS=true` (fail startup if migration upgrade fails)
- `ADMIN_EMAIL` and `ADMIN_PASSWORD` (used to auto-bootstrap first admin if none exists)
- `ADMIN_USERNAME` (default: `admin`, used with admin bootstrap)
- `SESSION_COOKIE_SECURE=true|false` (default true in production)
- `SESSION_COOKIE_SAMESITE=Lax|Strict|None` (default `Lax`)
- `SESSION_COOKIE_DOMAIN` (optional; for multi-subdomain setups)
- `SESSION_COOKIE_PATH` (default `/`)
- `SESSION_COOKIE_NAME` (default `acadex_session`)

Database URL behavior:
- If `DATABASE_URL` starts with `postgres://`, the app rewrites it to `postgresql://` for SQLAlchemy compatibility.
- If `DATABASE_URL` is not set:
	- On Render (`RENDER` env var present), startup fails intentionally so you cannot run on ephemeral DB by mistake.
	- Otherwise fallback is an app-local SQLite file under `instance/app.db`.

Session persistence:
- Server-side sessions are stored in the database (`user_sessions` table) via Flask-Session.
- Keep `SESSION_SECRET` stable across deployments to avoid invalidating existing session cookies.

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
