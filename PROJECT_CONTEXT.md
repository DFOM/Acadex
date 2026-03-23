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
- Schedule creation now captures student level (`1-4`) for level-adaptive planning.
- Create-schedule year/semester options are now driven strictly by `DatasetRegistry` availability (no free-form term selection).
- Future planning supports fallback to the nearest available registry term within `current_year + 5` and surfaces warning messages.
- Search/browse courses by contextual dataset resolution with optional future-plan level scope toggle (`my level` vs `all levels`).
- Add/remove schedule courses with:
  - real time-overlap conflict detection using Period start/end windows
  - prerequisite status checks
- Course detail API now returns a cleaner student payload (title/area/credits/faculty/prerequisites/unlocks) and unlock analysis uses latest active dataset when available.
- Schedule editor is grid-only and includes inline grade selection chips (A/B/C/D/F) with tooltip guidance, plus PNG export.
- Dashboard with GPA/progress, notifications, and mini schedule overview cards.
- AI recommendation preview and generation flows (feature-gated)

## Admin-facing

- Dataset management:
  - CSV upload and metadata registration
  - Mixed-term CSV uploads (Fall+Spring) are split into semester-specific dataset names during ingestion.
  - Registry listing with source/year/semester/type/table metadata
  - Refresh/delete/force-delete registry entries
  - delete action uses a confirmation modal, supports `DELETE /admin/api/datasets/<id>`, and now runs a hardened transactional delete flow with structured logs
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
- Catalog filtering applies odd/even year offering text guards when course metadata includes odd-only/even-only language.
- Alembic migrations and startup bootstrap protections
- Policy enforcement for `PROJECT_CONTEXT.md` in both local pre-commit and GitHub Actions CI

---

# 5. Known Issues / Pending Fixes

1. Test invocation environment sensitivity:
   - `pytest` may require `PYTHONPATH=.` for module imports in current local setup.
2. Legacy data model retention:
   - `ScrapedDataset` and `ScrapedCourse` models remain for backward compatibility cleanup, but ingestion no longer uses them.
3. CI workflow maintenance:
   - Keep branch protection configured to require the `Project Context Guard` workflow so policy checks cannot be bypassed.
4. Python 3.14 local runtime compatibility:
  - SQLAlchemy/Flask stack may require newer SQLAlchemy than historical pin when running full app imports under Python 3.14.
  - Local validation used an isolated SQLite DB harness to execute admin ingestion handlers directly.

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

## 2026-03-23 — Beta 0.9 (CSV parser hardening + diagnostics + preview)

- Hardened CSV header matching with normalized spacing/underscore handling and fuzzy alias fallback in `utils.py`.
- Expanded central mapping aliases in `config/data_mapping.py` to match observed production-style headers (`Course Number`, `Course Title`, `Course Instructor`, `Day and Period`, `Year Available`, `Subject Area`, `Prerequisites`).
- Added strict ingestion diagnostics in `Routes/admin_routes.py`:
  - detected-column logging,
  - first-5 row raw/extracted field logs,
  - explicit `FAILED TO EXTRACT` row warnings,
  - instructor/parsed-faculty sample logs,
  - ingestion totals log,
  - fail-fast error when zero valid rows are extracted.
- Added admin CSV preview endpoint (`/admin/api/datasets/preview`) and dataset-tab preview UI in `templates/Admin/manage_datasets.html`.
- Real CSV validation run (using `2026_Spring.csv` in isolated SQLite harness) confirmed:
  - required mappings detected,
  - no missing required mapping fields,
  - row-level extraction logs emitted,
  - ingestion totals emitted (`total_rows_read=118`, `total_courses_updated=107`, warnings for missing `day_period` rows).

## 2026-03-23 — Beta 1 (mapping/parsing completion and admin cleanup)

- Enforced mapped-payload-driven extraction across ingestion loop to ensure centralized mapping is always applied.
- Updated mapping defaults to include required aliases:
  - `subject_area`: `subject area`, `area`, `department`
  - `day_period`: `day and period`, `schedule`, `time`
  - introduced `year_level` mapping (separate from `year`) to avoid polluting year parsing.
- Added robust term parser for values like `Fall Semester 2025` -> `semester=Fall`, `year=2025`.
- Added `Course.year_level` model field and Alembic migration `a1b2c3d4e5f7_add_year_level_to_courses.py`.
- Added auto dataset naming from parsed CSV term coverage:
  - `YYYY Full Course List`
  - `YYYY Fall Course List`
  - `YYYY Spring Course List`
  - manual dataset name override remains supported.
- Added required debug logging:
  - `Extracted Subject Area: ...`
  - `Extracted Day Period: ...`
  - structured per-row parsed payload logs with empty-critical-field error logging.
- Added subject-area persistence sync during ingestion (auto-creates missing `SubjectArea` rows).
- Simplified admin faculty course display to title-only labels in faculty payload serialization.

## 2026-03-23 — Beta 1.1 (data consistency + future planning)

- Refactored CSV ingestion update matching to be term-scoped (`course_number + day_period + year + semester + dataset_name`) so cross-term course history is not overwritten.
- Added semester-specific dataset split behavior for mixed Fall/Spring CSV imports and per-term `CatalogStatus` updates.
- Enforced schedule creation year/semester availability strictly from `DatasetRegistry` with past/future windows.
- Added future-term fallback resolver (up to `current_year + 5`) for schedule creation and AI recommendation preview.
- Updated create-schedule UI to dynamically populate semester options per selected year from backend-provided registry map.
- Added odd/even offering filtering pass based on course text signals (odd years only / even years only).
- Added global theme alias tokens (`--text-primary`, `--text-secondary`, `--background`, `--surface`) and baseline contrast-safe form/card/table text rules.

## 2026-03-24 — Beta 1.2 (system stability + UX fixes)

- Replaced token-string clash checks with real time-window overlap checks in student add-course backend flow and schedule editor section-selection UI.
- Added period-time parsing utilities and overlap comparison helpers to enforce `same day + overlapping time` conflict semantics.
- Added stable dataset delete route support for `DELETE /admin/api/datasets/<id>` and ensured table drops use `DROP TABLE IF EXISTS ... CASCADE`.
- Added admin dataset delete confirmation modal and wired delete UI to the DELETE endpoint.
- Normalized faculty/instructor display formatting for list-like encoded values so UI output is human-readable.
- Hardened course detail endpoint with safer null handling and defensive error responses while preserving unlock dependency analysis.
- Removed list mode from schedule editor and standardized on grid-only interaction.
- Added in-grid grade chips (`A/B/C/D/F`) with select/deselect toggle behavior and themed tooltip instructions.

## 2026-03-24 — Beta 1.3 (dependency intelligence + UX/final fixes)

- Added `Schedule.level` persistence and migration (`b8d9e0f1a2b3_add_level_to_schedules.py`) with create-schedule level selector.
- Added level-aware catalog filtering in `student.courses.search` and schedule editor UI toggle (`my level` / `all levels`).
- Updated unlock dependency derivation to prefer latest dataset catalog context so reverse prerequisites reflect current offerings.
- Added dashboard mini schedule overview section with compact day/period/course/faculty rows and deep links to full schedule pages.
- Added editor-side PNG export for the schedule grid.
- Hardened admin dataset delete API transaction flow and added explicit success/failure logging with registry-row cleanup fallback.
- Simplified student course detail and courses view presentation by removing non-essential day/period emphasis in detail/unlock surfaces.

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
