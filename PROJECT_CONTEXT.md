# FOR AI AGENTS / COPILOT

## Mandatory startup protocol

Before making ANY code change in this repository:

1. Read this full file (`PROJECT_CONTEXT.md`).
2. Confirm current architecture rules and constraints.
3. Continue from the latest Change Log entry.
4. Apply updates that are consistent with CSV-driven ingestion and faculty-linking requirements.
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
  - Support administrative catalog management and CSV ingestion

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
- Domain services: `services/` (conflicts, prereqs, eligibility, AI)

## Database structure (high-level)

- Identity/RBAC: `User`, `Role`, `Permission`, `RegistrationField`, `LoginAttempt`
- Planning: `Schedule`, `ScheduleCourse`, `SubjectArea`, `Period`
- Catalog/courses: `Course`, `DatasetRegistry`, `CatalogStatus`
- Faculty: `Faculty`, join table `course_faculty`
- Notifications/events: `Notification`, `AIEvent`, `BehaviorEvent`
- App settings/content: `Setting`, `Announcement`, `LandingPageSection`
- Legacy storage retained for cleanup support: `ScrapedDataset`, `ScrapedCourse`

## Dataset system (dynamic, per year/semester)

- Registry table: `DatasetRegistry`
  - Key columns: `id`, `name`, `year`, `semester`, `dataset_type`, `table_name`, `source`, `created_at`
- Dynamic resolution function:
  - `utils.resolve_dataset(year, semester, source=None)`
  - Exact match (year+semester) -> same-year `full_year` -> latest available
- There is NO global active dataset dependency in current architecture.

## CSV mapping system (centralized)

- Mapping source of truth:
  - `config/data_mapping.py` (`DATA_MAPPING`)
  - Optional runtime overrides in `Setting(key='data_mapping_json')`
- Shared mapping helpers in `utils.py`:
  - `get_data_mapping`, `get_data_mapping_normalized`
  - `mapped_course_payload_from_row`, `detected_mapping_fields`
- Rule:
  - CSV ingestion must use centralized mapping helpers (no route-local hardcoded header aliases).

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
- No-course-without-faculty is a required ingestion rule for CSV import paths (rows without faculty are skipped and warned)

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
  - Parse CSV headers dynamically
  - Resolve canonical fields through centralized mapping helpers
  - Infer dataset context from filename/form input/name (`extract_dataset_context`)
  - Normalize semester (`normalize_dataset_semester`) and infer dataset type
  - Upsert or insert `Course` rows (`source='csv'`, `dataset_name`)
  - Parse faculty and enforce faculty presence
  - Link faculty through `sync_course_faculty`
  - Upsert corresponding `DatasetRegistry` row with metadata
  - Update `CatalogStatus` for inferred term

## Dataset cleanup and governance

- API: `DELETE/POST /admin/api/datasets/<id>/force-delete`
  - Drops table via `DROP TABLE IF EXISTS ... CASCADE`
  - Removes matching registry and source rows
- API: `POST /admin/api/datasets/clean-invalid`
  - Bulk removes invalid datasets (broken table metadata, empty csv datasets, legacy scraped-source rows)
- UI: dataset tab includes force delete and clean-invalid actions.

## Mapping management

- API: `GET/POST /admin/api/datasets/mapping`
  - Read effective mapping / store alias overrides
- API: `GET /admin/api/datasets/<id>/columns`
  - Inspect detected columns and current mapped field matches

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
  - Refresh/delete/force-delete registry entries
  - Bulk clean invalid datasets
  - Orphan table scan/cleanup
  - Dataset column inspection
  - Mapping edit API/UI
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
3. Legacy data model retention:
   - `ScrapedDataset` and `ScrapedCourse` models remain for backward compatibility cleanup, but ingestion no longer uses them.
4. CI workflow maintenance:
   - Keep branch protection configured to require the `Project Context Guard` workflow so policy checks cannot be bypassed.

---

# 6. Change Log (Chronological)

## 2026-03-18 — Beta 0.1

- Added initial dataset ingestion architecture and admin data management foundations.

## 2026-03-18 — Beta 0.2

- Migrated route flows from legacy CSV helper dependence to normalized models.
- Refactored student schedule editor and admin analytics metadata paths.

## 2026-03-23 — Beta 0.9 (Core architecture dataset/faculty refactor)

- Removed global active-dataset dependency and activation endpoints.
- Enforced contextual dataset resolution across schedule/catalog flows.
- Introduced centralized resolver and dataset context parsing helpers.
- Refactored CSV ingestion to infer year/semester/type and enforce faculty linking.
- Added dataset-aware faculty backfill script.
- Updated admin/student UI labels and messages to contextual dataset language.

## 2026-03-23 — Beta 0.9 (Project memory & tracking system)

- Added persistent repository memory file as single source of truth.
- Added agent startup/completion protocol requiring context updates.
- Added git pre-commit guard to block code commits without `PROJECT_CONTEXT.md` update.
- Added githook installer utility for team-wide adoption.

## 2026-03-23 — Beta 0.9 (CI enforcement for project memory policy)

- Added GitHub Actions workflow `Project Context Guard` to enforce `PROJECT_CONTEXT.md` updates on push/PR when code/config/docs change.

## 2026-03-23 — Beta 0.9 (CSV ingestion reset and scraper purge)

- Hard-removed old browser-driven scraping service and all related admin routes/templates.
- Rebuilt dataset management UI around CSV ingestion and dataset governance actions.
- Added centralized mapping config in `config/data_mapping.py` and runtime mapping overrides.
- Refactored CSV upload ingestion path to use centralized mapping helpers only.
- Added force-delete endpoint and bulk invalid dataset cleanup endpoint.
- Added dataset column inspection and mapping-edit APIs.
- Removed selenium dependency from `requirements.txt`.
- Files affected:
  - `Routes/admin_routes.py`
  - `utils.py`
  - `config/data_mapping.py`
  - `config/__init__.py`
  - `templates/Admin/manage_datasets.html`
  - `templates/Admin/admin_health.html`
  - `templates/Admin/admin_dashboard.html`
  - `templates/Layouts/admin_base.html`
  - `requirements.txt`
  - removed: `services/scraper_service.py`
  - removed: `templates/Admin/university_connection.html`

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

## CSV mapping rules (mandatory)

- All CSV field extraction must go through centralized mapping helpers.
- Do not hardcode CSV header names in routes/services.
- Mapping edits must flow through `/admin/api/datasets/mapping`.

## Faculty ingestion rules (mandatory)

- Accept mapped faculty source values.
- Split names by comma and conjunctions (`and`), normalize/dedupe.
- Reuse existing `Faculty` records when possible.
- Link through `course_faculty` relation via `sync_course_faculty`.
- If faculty missing on ingestion paths, skip and log warning.

## Safe extension checklist

Before extending features:

1. Read `PROJECT_CONTEXT.md` and latest Change Log entries.
2. Confirm whether change impacts dataset resolution, ingestion, mapping, or faculty links.
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
