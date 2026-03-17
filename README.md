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
- `DATABASE_URL` (default: `sqlite:///instance/app.db`)

Optional:
- `UPLOAD_FOLDER` (default: `uploads`)
- `FLASK_ENV=production`
- `STRICT_MIGRATIONS=true` (fail startup if migration upgrade fails)

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
