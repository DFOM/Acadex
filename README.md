# CoursesScraper / Acadex

Acadex is a Flask-based academic planning platform for students and admins.

It supports:
- Student schedule planning (past + future)
- Prerequisite-aware course selection
- Admin analytics dashboards with drill-downs and export
- AI recommendation + schedule-generation foundations
- Landing page + announcement management

## Version

- Current version: **Beta 1.5.0-planning**

## Changelog

### Beta 1.5.0-planning — 2026-04-09

- Added a planning-only, stepwise 4-phase architecture roadmap (no implementation started yet in this entry).
- Phase 1 planned: dynamic course metadata schema, dynamic CSV/API ingestion mapping, and admin visibility controls with resolver compatibility maintained.
- Phase 2 planned: deterministic prerequisite reverse mapping and new student dependency-graph course details endpoint.
- Phase 3 planned: data-driven student UI rendering from admin-controlled visible metadata and dependency explorer interactions.
- Phase 4 planned: automated external sync service, runtime smoke checks, and machine-readable sync failure alerts.
- Delivery protocol documented for upcoming work: complete one phase at a time, then validate, bump version, update docs, commit, and push.

### Beta 1.4.5 — 2026-03-24

- Enriched System Health Action Logs with per-route diagnostic `detail` payloads so dataset impact/delete/force-delete operations record structured outcomes and errors.
- Wired dataset endpoints to emit explicit diagnostic details (`dataset_id`, dataset name/source, cleanup counts, partial-cleanup/failure metadata) into persistent action logs.
- Updated Action Logs rendering to include `detail=...` in copy output for faster production debugging.
- Expanded regression contracts for action-log detail persistence and dataset-route diagnostic hooks.

### Beta 1.4.4 — 2026-03-24

- Added persistent System Health action logging for mutating admin/student/schedule requests (method, path, status, user, IP, session id), stored in app settings.
- Extended `/admin/api/health-metrics` with `audit.action_logs` and `audit.action_log_count` so health diagnostics include recent action history.
- Added new copyable Action Logs panel in System Health UI, including one-click clipboard copy for sharing/debugging.
- Added regression contract tests for action-log persistence contract, health metrics payload exposure, and UI copy affordance.

### Beta 1.4.3 — 2026-03-24

- Added dataset dependency impact analysis endpoint (`GET /admin/api/datasets/<id>/impact`) so admin delete flow can show how many schedules/users are affected before confirmation.
- Updated admin dataset delete modal to display linked-schedule impact counts and explicit warning text prior to deletion.
- Deleting a dataset now automatically removes linked schedules (including guest schedules) and creates in-app notifications for affected signed-in users.
- Added regression contract coverage for impact endpoint presence and schedule cleanup + notification behavior.

### Beta 1.4.2 — 2026-03-24

- Fixed dataset deletion reliability for legacy/dirty CSV rows by using normalized dataset-name matching (trim + case-insensitive) and legacy-source-compatible cleanup filters.
- Applied the same robust dataset-row matching to normal delete, force delete, and invalid-dataset cleanup paths so registry rows do not reappear after sync.
- Added regression contract coverage for the new dataset cleanup filter behavior.

### Beta 1.4 — 2026-03-24

- Implemented ScheduleCourse course-instance identity model fields (`day_period`, `semester`, `year`, `dataset_name`, optional `source`) and supporting migration scaffolding.
- Updated student schedule read/write flows to persist and resolve section-level identity instead of course-number-only matching.
- Added legacy-safe identity fallback behavior so older rows remain loadable with deterministic defaults.
- Fixed duplicate-section detection in schedule editor UI to compare full instance identity instead of only course number.
- Hardened student courses view rendering with fail-safe fallback handling to avoid internal server errors on edge data states.
- Improved dataset deletion reliability by syncing registry state before delete and returning explicit partial-cleanup success when registry removal succeeds.
- Updated CSV ingestion + preview paths to support missing day/period values by normalizing them to `TBA`.
- Improved PNG export readability by forcing high-contrast full-opacity text styling in export clone context.
- Added regression contract tests for schema identity, write-path identity, dataset-delete/TBA behavior, and student view/export safeguards.

### Beta 1.3 — 2026-03-24

- Added schedule level support (`level` on `Schedule`) with creation-time level selection (`First` through `Fourth` year).
- Added level-aware course catalog filtering in schedule editor APIs/UI with a toggle: `Show only my level` / `Show all levels`.
- Upgraded unlock dependency inference to prioritize the latest active dataset so reverse-prerequisite results stay current.
- Added compact dashboard mini schedule overviews with day/period/title/faculty rows and quick links to full schedule editor.
- Added schedule grid PNG export from editor UI using client-side capture.
- Hardened admin dataset deletion with explicit transaction handling, retry-safe registry cleanup fallback, and structured success/failure logging.
- Simplified user-facing course detail surfaces to focus on title, area, credits, faculty, prerequisites, and unlocks.

### Beta 1.2 — 2026-03-24

- Replaced token-based clash checks with real time-overlap checks (same day + overlapping start/end minutes) in both backend add-course validation and schedule editor selection UI.
- Added resilient period-time parsing and overlap comparison helpers so conflicts are validated using `Period.start_time`/`Period.end_time` instead of raw day-period strings.
- Hardened dataset deletion flow with explicit `DELETE /admin/api/datasets/<id>` support and `DROP TABLE IF EXISTS ... CASCADE` behavior while still deleting registry rows if physical tables are missing.
- Added dataset delete confirmation modal in admin dataset management UI and wired delete action to the new DELETE route.
- Normalized faculty/instructor formatting across student-facing flows to clean list-like values (e.g., `["Dr. X"]` -> `Dr. X`).
- Stabilized course detail endpoint with stronger null-safe fallbacks and defensive error handling; unlocks payload remains dataset-context aware.
- Removed schedule editor list mode and standardized on grid-only editing.
- Added in-grid interactive grade chips (`A/B/C/D/F`) with toggle select/deselect behavior and themed tooltip guidance for GPA estimation.

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
