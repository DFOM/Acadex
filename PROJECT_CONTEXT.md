# FOR AI AGENTS / COPILOT

## Mandatory startup protocol

Before making ANY code change in this repository:

1. Read this full file (`PROJECT_CONTEXT.md`).
2. Confirm current architecture rules and constraints.
3. Continue from the latest Change Log entry.
4. Apply updates that are consistent with dynamic dataset resolution and faculty-linking requirements.
5. Update this file before considering the task complete.

## Mandatory completion protocol

Every code change, fix, feature, or commit preparation must include:

- An updated Change Log entry.
- Updated `Current Features` if behavior changed.
- Updated `Known Issues / Pending Fixes` if issues were resolved/introduced.

No change is complete unless `PROJECT_CONTEXT.md` is updated.

---

# 1. Project Overview

- App name: Acadex (University Scheduler / Academic Planning Platform)
- Purpose: Dynamic academic planning + analytics platform for university students and admins.
- Core product intent:
  - Build/edit schedules (past + future)
  - Use contextual course catalogs by year/semester
  - Enforce prerequisites/conflict checks
  - Support administrative catalog management, analytics, and scraping ingestion

---

# 2. Core System Architecture

## Flask backend structure

- Entry point: `app.py`
- Blueprint registration:
  - `auth_bp` (`Routes/auth_routes.py`)
  - `main_bp` (`Routes/main_routes.py`)
  - `admin_bp` (`Routes/admin_routes.py`, `/admin` prefix)
  - `student_bp` (`Routes/student_routes.py`, `/student` prefix)
  - `schedule_bp` (`Routes/schedule_routes.py`, `/schedule` prefix)
- ORM and DB access: SQLAlchemy models in `models.py`
- Migrations: Alembic under `migrations/`
- Shared helpers: `utils.py`
- Domain services: `services/` (conflicts, prereqs, eligibility, scraper, AI)

## Database structure (high-level)

- Identity/RBAC: `User`, `Role`, `Permission`, `RegistrationField`, `LoginAttempt`
- Planning: `Schedule`, `ScheduleCourse`, `SubjectArea`, `Period`
- Catalog/courses: `Course`, `ScrapedDataset`, `ScrapedCourse`, `DatasetRegistry`, `CatalogStatus`
- Faculty: `Faculty`, join table `course_faculty`
- Notifications/events: `Notification`, `AIEvent`, `BehaviorEvent`
- App settings/content: `Setting`, `Announcement`, `LandingPageSection`

## Dataset system (dynamic, per year/semester)

- Registry table: `DatasetRegistry`
  - Key columns: `id`, `name`, `year`, `semester`, `dataset_type`, `table_name`, `source`, `created_at`
- Dynamic resolution function:
  - `utils.resolve_dataset(year, semester, source=None)`
  - Exact match (year+semester) -> same-year `full_year` -> latest available
- There is NO global active dataset dependency in current architecture.

## Schedule system (past/future)

- `Schedule` includes `year`, `semester`, `schedule_type` (`past` or `future`)
- Student flows resolve catalog by schedule context
- Future schedules drive recommendation/generation paths
- Past schedules drive historical GPA/progress signals

## Faculty system (many-to-many)

- `Course` <-> `Faculty` through `course_faculty`
- Canonical linking/normalization pipeline in `utils.py`:
  - `extract_faculty_names`
  - `normalize_faculty_name`
  - `sync_course_faculty`
- No-course-without-faculty is a required ingestion rule for CSV/scraped import paths (rows without faculty are skipped and warned)

## Period system (admin-controlled)

- `Period` is managed through admin APIs/UI
- Scheduling UI uses configured periods and lunch-window settings

---

# 3. Key Systems Explained

## Dataset Resolution Engine

- Primary resolver: `utils.resolve_dataset(year, semester, source=None)`
- Semantics:
  1. Exact match on normalized year+semester
  2. Fallback to same-year `dataset_type == full_year`
  3. Final fallback to latest registry row
- Student catalog flows call resolver to identify contextual dataset instead of global selection.

## CSV ingestion pipeline

- Endpoint: admin upload route (`Routes/admin_routes.py`)
- Workflow:
  - Parse CSV headers with alias matching (course/faculty/term fields)
  - Infer dataset context from filename/form input/name (`extract_dataset_context`)
  - Normalize semester (`normalize_dataset_semester`) and infer dataset type
  - Upsert or insert `Course` rows (`source='csv'`, `dataset_name`)
  - Parse faculty and enforce faculty presence
  - Link faculty through `sync_course_faculty`
  - Upsert corresponding `DatasetRegistry` row with metadata
  - Update `CatalogStatus` for inferred term

## Scraper integration

- Service: `services/scraper_service.py`
- Workflow:
  - Create `ScrapedDataset`
  - Persist row-level details in `ScrapedCourse`
  - On completion: upsert `DatasetRegistry` metadata for scraped dataset
  - Mirror canonical scraped rows into unified `Course` (`source='scraped'`) for consistent querying
  - Parse and link faculty via `sync_course_faculty`; log warnings when missing

## Schedule creation logic

- Route: `student.create_schedule`
- Requires `schedule_name`, `year`, `semester`
- Validates contextual dataset availability via `resolve_dataset`
- On missing dataset, user-facing message must be:
  - "No course data available for selected year/semester"

## Prerequisite logic

- Main logic in `Routes/student_routes.py` + `services/prereqs.py` + `services/eligibility.py`
- Uses normalized course IDs and title-aware presentation
- Add-course flow allows warning-based prerequisite status and conflict enforcement
- Conflict checks use parsed day/period blocks from `services/conflicts.py`

## Analytics system

- Admin analytics endpoints/UI in `Routes/admin_routes.py`
- Supports dynamic filtering (year/semester/major/minor/gender/year-of-study)
- Includes health/integrity checks and analytics export/override pathways

---

# 4. Current Features

## Student-facing

- Create/edit schedules (past/future)
- Search/browse courses by contextual dataset resolution
- Add/remove schedule courses with:
  - conflict detection
  - prerequisite status checks
- Course detail API with unlock analysis and prereq state
- Dashboard with GPA/progress and notifications
- AI recommendation preview and generation flows (feature-gated)

## Admin-facing

- Dataset management:
  - CSV upload and metadata registration
  - Registry listing with source/year/semester/type/table metadata
  - Refresh/delete registry entries
  - Orphan table scan/cleanup
- University scraper control:
  - start/check/probe/scrape/pause/resume/stop/status/diagnostics
- Course/faculty CRUD and linkage workflows
- Subject area/period/user/role/registration-field management
- Analytics + system health dashboards
- Landing page and announcements management

## Platform capabilities

- RBAC + per-route permission enforcement
- Notifications + behavior/AI events
- Alembic migrations and startup bootstrap protections
- Policy enforcement for `PROJECT_CONTEXT.md` in both local pre-commit and GitHub Actions CI

---

# 5. Known Issues / Pending Fixes

1. Repository version metadata drift:
   - `README.md` still shows earlier beta numbering (Beta 0.2) while active project label is Beta 0.9 in folder naming.
2. Test invocation environment sensitivity:
   - `pytest` may require `PYTHONPATH=.` for module imports in current local setup.
3. Legacy naming cleanup opportunity:
   - Some variable names still use historical “active_dataset_name” wording in old response payload expectations; behavior is contextual, but naming consistency can be improved further where non-breaking.
4. CI workflow maintenance:
  - Keep branch protection configured to require the `Project Context Guard` workflow so policy checks cannot be bypassed.

---

# 6. Change Log (Chronological)

## 2026-03-18 — Beta 0.1

- Added scraper-first dataset architecture with canonical scraped fields.
- Added scraper controls and diagnostics foundation.
- Added initial system-health administration surface.
- Files: `services/scraper_service.py`, `Routes/admin_routes.py`, templates, supporting services.

## 2026-03-18 — Beta 0.2

- Migrated remaining route flows from legacy CSV helper dependence to scraper-backed models.
- Refactored student schedule editor and admin analytics metadata paths.
- Removed deprecated legacy CSV table helpers.
- Files: `Routes/student_routes.py`, `Routes/admin_routes.py`, `utils.py`, templates.

## 2026-03-23 — Beta 0.9 (Core architecture dataset/faculty refactor)

- Removed global active-dataset dependency and activation endpoints.
- Enforced contextual dataset resolution across schedule/catalog flows.
- Introduced centralized resolver and dataset context parsing helpers.
- Refactored CSV ingestion to infer year/semester/type and enforce faculty linking.
- Updated scraper completion to register rich dataset metadata and mirror linked `Course` rows.
- Added dataset-aware faculty backfill script.
- Updated admin/student UI labels and messages to contextual dataset language.
- Files affected:
  - `utils.py`
  - `Routes/admin_routes.py`
  - `Routes/student_routes.py`
  - `services/scraper_service.py`
  - `scripts/backfill_faculty_links.py`
  - `templates/Admin/manage_datasets.html`
  - `templates/Admin/university_connection.html`
  - `templates/Admin/admin_health.html`
  - `templates/Student/courses.html`

## 2026-03-23 — Beta 0.9 (Project memory & tracking system)

- Added persistent repository memory file as single source of truth.
- Added agent startup/completion protocol requiring context updates.
- Added git pre-commit guard to block code commits without `PROJECT_CONTEXT.md` update.
- Added githook installer utility for team-wide adoption.
- Files affected:
  - `PROJECT_CONTEXT.md`
  - `.githooks/pre-commit`
  - `scripts/install-githooks.sh`

## 2026-03-23 — Beta 0.9 (CI enforcement for project memory policy)

- Added GitHub Actions workflow `Project Context Guard` to enforce `PROJECT_CONTEXT.md` updates on push/PR when code/config/docs change.
- Implemented diff-range handling for `pull_request`, `push`, and manual workflow runs.
- Matched policy scope to local pre-commit patterns for consistent local/remote behavior.
- Files affected:
  - `.github/workflows/project-context-guard.yml`
  - `PROJECT_CONTEXT.md`

---

# 7. Developer Notes (AI Self-Guide)

## Coding conventions and style

- Keep changes surgical and architecture-consistent.
- Reuse shared helpers in `utils.py` for normalization/resolution logic.
- Prefer service/helper composition over route-local duplication.
- Avoid unrelated refactors when solving focused tasks.

## Dataset handling rules (mandatory)

- NEVER use a global active dataset model.
- ALWAYS resolve datasets by user context (`year`, `semester`) via `resolve_dataset`.
- Keep dataset metadata complete in `DatasetRegistry`.
- Avoid hardcoded table assumptions in application logic.

## Faculty ingestion rules (mandatory)

- Accept `Instructor` or `Faculty` source values.
- Split names by comma and conjunctions (`and`), normalize/dedupe.
- Reuse existing `Faculty` records when possible.
- Link through `course_faculty` relation via `sync_course_faculty`.
- If faculty missing on ingestion paths, skip and log warning.

## No-hardcoding rules

- No hardcoded dataset selection.
- No hardcoded term assumptions when contextual metadata is available.
- Avoid embedding business rules in templates/JS when backend can enforce.

## Safe extension checklist

Before extending features:

1. Read `PROJECT_CONTEXT.md` and latest Change Log entries.
2. Confirm whether change impacts dataset resolution, ingestion, or faculty links.
3. Update/extend shared utils first if logic is reusable.
4. Update route/service call sites.
5. Verify errors/tests.
6. Update this file’s Features, Known Issues, and Change Log.

---

# 8. Commit & Tracking Policy

## Commit policy

- `PROJECT_CONTEXT.md` must be included in commits that stage code changes.
- Use the pre-commit guard in `.githooks/pre-commit`.

## Hook setup

Run once per clone:

```bash
bash scripts/install-githooks.sh
```

This sets `core.hooksPath` to `.githooks` for this repository.

---

# 9. Future Resume Protocol

When resuming work after a break:

1. Open and read `PROJECT_CONTEXT.md`.
2. Start from latest Change Log and Known Issues.
3. Continue using architecture rules in Sections 2, 3, and 7.
4. Add a new dated Change Log entry for every completed implementation batch.
