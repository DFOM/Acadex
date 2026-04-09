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
- **Course Catalog browser**: Combined deduplicated catalog across ALL datasets/years. Card-based list with search, area, level, semester (Spring/Fall/Both), credits, and has-prerequisites filters. AJAX-powered detail drawer with prerequisites (satisfied/missing/untrackable — now resolves title-based prereqs like "Statistics"), full dependency graph, multi-area display for cross-listed courses (derived from course number prefixes), and sections.
- Add/remove schedule courses with:
  - real time-overlap conflict detection using Period start/end windows
  - prerequisite status checks
- Schedule course persistence now supports section-level identity dimensions (`course_number`, `day_period`, `semester`, `year`, `dataset_name`, optional `source`) for stable duplicate handling.
- Course detail API now returns a cleaner student payload (title/area/credits/faculty/prerequisites/unlocks) and unlock analysis uses latest active dataset when available.
- Schedule editor is grid-only and includes inline grade selection chips (A/B/C/D/F) with tooltip guidance, plus PNG export.
- Dashboard with GPA/progress, notifications, and mini schedule overview cards.
- Graduation requirements dashboard: dynamic checklist tracking general requirements (English, Japanese, Foundation, Pathway, QR, Major, Total Credits), major-specific designated courses (Psychology, Data Science variants), and minor-specific requirements with level distribution and grade thresholds.
- Area progress bars show earned, planned, total available, and required (for major/minor) credits.
- AI recommendation preview and generation flows (feature-gated)

## Admin-facing

- Dataset management:
  - CSV upload and metadata registration
  - Dynamic metadata ingestion now captures non-canonical CSV/API attributes into structured course metadata (`Course.metadata_json`) with auto-discovered schema tracking in `DatasetRegistry.metadata_schema_json`.
  - Admin metadata visibility controls are now available via API (`/admin/api/course-metadata-fields` and `/admin/api/course-metadata-fields/<field_key>/visibility`) with `student` vs `admin_only` scopes.
  - Mixed-term CSV uploads (Fall+Spring) are split into semester-specific dataset names during ingestion.
  - Registry listing with source/year/semester/type/table metadata
  - Refresh/delete/force-delete registry entries
  - delete action uses a confirmation modal with dependency impact counts, supports `DELETE /admin/api/datasets/<id>`, and runs hardened transactional delete flow with linked-schedule cleanup + user notifications
  - Bulk clean invalid datasets
  - Orphan table scan/cleanup
  - Dataset column inspection
  - Mapping edit API/UI
- Course/faculty CRUD and linkage workflows
- Subject area/period/user/role/registration-field management
- Analytics + system health dashboards
- **Activity Log page** (`/admin/activity`): Combined BehaviorEvent + AIEvent viewer with source/type/user filters, paginated table, and metadata JSON inspection modal.
- **Enhanced dashboard**: Stat cards now include platform event counts (behavior + AI) and new-users-this-week indicator. Announcements widget shows 5 latest announcements with type badges on the dashboard.
- **User management search/filter**: Real-time client-side filtering by username/email/name, role dropdown, and graduation year on the manage_users page.
- Landing page and announcements management

## Platform capabilities

- RBAC + per-route permission enforcement
- Notifications + behavior/AI events
- Persistent System Health action logs for mutating requests, exposed in health metrics and copyable from the admin health UI.
- Catalog filtering applies odd/even year offering text guards when course metadata includes odd-only/even-only language.
- Alembic migrations and startup bootstrap protections
- Policy enforcement for `PROJECT_CONTEXT.md` in both local pre-commit and GitHub Actions CI

## 🚨 Current Critical Issues (Beta Stabilization)

1. Duplicated course period bug:
  - Status: ✅ Implemented (Beta 1.4)
  - Issue: When course is duplicated in admin panel and period is changed, user still sees OLD period.
  - Resolution summary: Student schedule read/write and UI duplicate checks now use section-level course instance identity and preserve identity metadata in payloads.
  - Affected files (implemented): `models.py`, `migrations/versions/c2d4e6f8a9b0_add_course_instance_identity_to_schedule_courses.py`, `Routes/student_routes.py`, `static/js/student.js`.

2. Course view crash:
  - Status: ✅ Implemented (Beta 1.4)
  - Issue: Clicking course in user tab → Internal Server Error.
  - Resolution summary: `courses_view` now uses fail-safe guarded rendering with rollback + safe fallback payloads instead of propagating unhandled exceptions.
  - Affected files (implemented): `Routes/student_routes.py`.

3. Dataset deletion failure:
  - Status: ✅ Implemented (Beta 1.4.3 dependency-aware cleanup)
  - Issue: Deleting dataset does nothing; system cannot be cleaned.
  - Resolution summary: Delete flow now pre-syncs registry state, performs robust normalized cleanup, shows admin impact counts (linked schedules/users) before delete, and automatically removes linked schedules while notifying affected users.
  - Affected files (implemented): `Routes/admin_routes.py`.

4. Missing courses:
  - Status: ✅ Implemented (Beta 1.4)
  - Issue: Courses without day/period (e.g., Fuji Culture) not appearing.
  - Resolution summary: CSV ingestion and preview now allow missing day/period mapping and normalize empty values to `TBA`.
  - Affected files (implemented): `Routes/admin_routes.py`.

5. PNG export issue:
  - Status: ✅ Implemented (Beta 1.4)
  - Issue: Course title & faculty opacity too low (unreadable).
  - Resolution summary: Export clone path now forces full-opacity, higher-weight, high-contrast text for course title/faculty before image capture.
  - Affected files (implemented): `static/js/student.js`.

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
5. Migration rollout order requirement:
  - Identity write paths expect `schedule_courses` identity columns to exist. Deployments must run Alembic upgrade before serving write traffic.
6. Beta 1.5.0+ roadmap execution status:
  - A new 4-phase architecture expansion roadmap is now documented (see Change Log entry dated 2026-04-09).
  - Current status: Phase 1 backend architecture completed (dynamic metadata schema + ingestion + admin visibility APIs).
  - Remaining status: Phases 2-4 are pending implementation.
7. Delivery protocol for upcoming phase work:
  - Execute phase-by-phase in order (Phase 1 -> Phase 2 -> Phase 3 -> Phase 4).
  - After each completed fix/feature: run validations, increment `VERSION`, update `README.md`, update `PROJECT_CONTEXT.md`, then commit and push.
8. Pending production confirmation (not yet marked fixed):
  - Candidate patch deployed for `/student/courses` internal-server-error on course click (template prerequisite iteration guard). **Confirmed fixed.**
  - Phase 2 dependency-graph endpoint candidate deployed (`/api/student/course-details/<id>` alias + student blueprint path).
  - OR/AND dependency display: courses view now uses `dependency_graph_for_course` engine to distinguish hard (AND) prerequisites from conditional (OR) alternatives in the "What This Course Unlocks" panel.
  - Text-based fallback: graph engine now falls back to substring matching when free-text prerequisites (commas, colons, course titles) cannot be parsed by the strict AST parser. This ensures reverse dependency lookup works with real-world catalog data.
  - Direct prerequisites panel now shows raw prerequisite text alongside status badges for transparency.
  - Dataset deletion fix: `_delete_registry_dataset_row` and `api_force_delete_dataset` now clear `course_faculty` FK join rows before deleting courses. Added missing imports for `ScheduleCourse`, `AIEvent`, `BehaviorEvent`, `course_faculty`. Removed premature `db.session.commit()` inside `_safe_drop_table_if_exists`.
  - Dataset management UI reworked: cleaner layout, "Delete All Datasets" button, orphan/invalid cleanup consolidated into Danger Zone section.
  - Nuclear system reset endpoint: `POST /admin/api/system-reset` wipes all datasets, courses, schedules, events, notifications, users and creates fresh admin account. Requires `{"confirm":"RESET"}` payload. Double-confirmation in UI (confirm dialog + type "RESET"). Clears `faculty_advisor_area` FK before Faculty delete. Script moved to `extra_scripts` block so it loads after Bootstrap JS.
  - TBA/unscheduled courses: courses without a specific period now render in an "Unscheduled / TBA Courses" ribbon above the grid with grade chips + remove button. Previously invisible.
  - Auto-complete schedules: `update_app_settings_route` now flips `future` schedules to `past` when their (year,semester) is before the newly set current year/semester.
  - Per-schedule GPA & cumulative GPA: dashboard shows semester GPA, cumulative GPA, credits, and course count for each schedule independently (all schedules, not just top 3).
  - Interactive credit progress bars: overall credit progress bar in stat card + per-area progress bars showing earned (green), planned/in-progress (purple), and remaining (grey) segments.
  - Local validation passed (`56 passed`), awaiting Render/live confirmation.

---

# 6. Change Log (Chronological)

## 2026-04-09 — Dashboard area progress + mini schedule fixes

- Area credit progress bars now only show required/remaining for areas where user has declared major or minor. Other areas display earned and planned credits without a requirement target.
- Added Major/Minor badges next to area names in progress bars for clarity.
- Fixed doubled courses in dashboard mini schedules caused by `_day_period_structured_blocks()` creating separate entries per day-period pair. Each course now appears once with its full day/period string.
- Converted mini schedule display from table rows to compact card grid layout.
- Read iCLA graduation requirement PDFs (2024AY and 2025AY) to inform area requirement logic.

## 2026-04-09 — Validation Candidate (Phase 2 + courses click fix, pending live confirmation)

- Added deterministic prerequisite dependency graph service sets:
  - direct requirements
  - dependent unlockables
  - conditional paths
- Added student dependency graph endpoints:
  - `/student/api/course-details/<id>`
  - `/api/student/course-details/<id>` alias route
- Added safeguard fix for student courses detail panel rendering to prevent template errors when prerequisite status payload is a dict.
- Added/updated tests for dependency graph behavior, endpoint contracts, and template prerequisite iteration safety.
- Status: shipped as deployment candidate, pending production confirmation before being recorded as fully fixed.

## 2026-04-09 — Beta 1.5.1-phase1 (dynamic metadata backend)

- Implemented Phase 1 backend architecture for dynamic course metadata handling.
- Added structured metadata fields to core models:
  - `Course.metadata_json`
  - `DatasetRegistry.metadata_schema_json`
  - `CourseMetadataField` (visibility control table)
- Added dynamic ingestion utilities in `utils.py` to split canonical fields from arbitrary incoming CSV/API attributes without requiring schema migrations for new attributes.
- Updated CSV ingestion and admin manual course APIs to persist dynamic metadata, auto-register discovered fields, and merge dataset-level metadata schemas.
- Added admin visibility control APIs:
  - `GET/POST /admin/api/course-metadata-fields`
  - `POST /admin/api/course-metadata-fields/<field_key>/visibility`
- Extended dataset resolver with optional `required_metadata_fields` filtering while preserving default exact/full_year/latest fallback semantics.
- Added migration: `migrations/versions/e7f2a1b3c4d5_dynamic_course_metadata_schema.py`.
- Added contract coverage: `tests/test_dynamic_metadata_phase1_contracts.py`.
- Validation after implementation: `44 passed` (`PYTHONPATH=. pytest -q`).

## 2026-04-09 — Beta 1.5.0-planning (multi-phase roadmap queued)

- Added a planning-only roadmap for a stepwise architecture expansion; no implementation in this entry.
- Documented Phase 1 scope: dynamic course metadata schema, dynamic ingestion utility, admin visibility controls, and dataset resolver compatibility constraint.
- Documented Phase 2 scope: deterministic prerequisite reverse-mapping graph sets (direct requirements, dependent unlockables, conditional paths) and new student course-details graph endpoint.
- Documented Phase 3 scope: data-driven student UI builder based on admin-configured visible metadata, dependency explorer UX, and security posture parity.
- Documented Phase 4 scope: automated sync/health service with mocked external API polling, runtime smoke checks, and machine-readable sync failure alerts.
- Added explicit delivery rule to commit/push and update version + docs after each completed fix/feature.

## 2026-03-24 — Beta 1.4.5 (action-log diagnostic detail)

- Added structured `detail` payload support in persisted System Health action logs.
- Dataset impact/delete/force-delete routes now emit route-level diagnostics into action logs (including ids, names, cleanup counts, and error/partial-cleanup metadata).
- System Health Action Logs UI copy output now includes `detail=...` for direct debugging handoff.
- Expanded `tests/test_system_health_action_logs_contracts.py` coverage for detail payload and dataset route diagnostic hooks.
- Verified focused contracts after fix: `11 passed`.

## 2026-03-24 — Beta 1.4.4 (system health action logs)

- Added settings-backed persistent action log writer in `app.py` for mutating requests across admin/student/schedule routes.
- Extended `GET /admin/api/health-metrics` payload with `audit.action_logs` and `audit.action_log_count`.
- Added System Health UI panel for Action Logs with one-click copy-to-clipboard support.
- Added regression contract suite `tests/test_system_health_action_logs_contracts.py`.
- Verified focused contracts after fix: `10 passed`.

## 2026-03-24 — Beta 1.4.3 (dataset dependency-aware deletion)

- Added dataset impact endpoint (`GET /admin/api/datasets/<id>/impact`) to compute linked schedule/user impact before deletion.
- Updated admin dataset delete modal to display impact counts and explicit warning messaging before confirming delete.
- Delete flow now removes linked schedules and schedule-linked events in the same transaction and creates notifications for affected signed-in users.
- Added contract assertions for impact endpoint + linked schedule cleanup/notification behavior.
- Verified focused contract suite after fix: `16 passed`.

## 2026-03-24 — Beta 1.4 (stabilization implementation batch)

- Added ScheduleCourse instance-identity fields in model (`day_period`, `semester`, `year`, `dataset_name`, optional `source`) with supporting indexes (`course_term` and full identity index).
- Added migration scaffold `c2d4e6f8a9b0_add_course_instance_identity_to_schedule_courses.py` for identity columns/indexes rollout.
- Updated student schedule read paths to hydrate by identity-first matching with legacy-safe fallback.
- Updated add-course write path to persist identity dimensions and use identity-aware duplicate checks when section context is present.
- Updated schedule editor UI payloads and duplicate checks to use full instance identity semantics.
- Hardened `courses_view` with guarded rendering and safe fallback behavior.
- Improved dataset delete idempotency/reliability by syncing registry before lookup and returning partial-cleanup success when registry row removal succeeds.
- Updated CSV ingestion/preview to accept missing day/period and normalize to `TBA`.
- Improved PNG export readability by enforcing high-contrast clone styles during html2canvas capture.
- Added regression tests:
  - `tests/test_schedule_course_identity_schema.py`
  - `tests/test_identity_write_path_contracts.py`
  - `tests/test_admin_dataset_and_tba_contracts.py`
  - `tests/test_student_view_and_export_contracts.py`

## 2026-03-24 — Beta 1.4.1 (production deploy syntax hotfix)

- Fixed a production-blocking `SyntaxError` in `Routes/student_routes.py` where identity helper functions were accidentally nested inside `_safe_json_load_dict` before its `except` block.
- Restored proper function boundaries for:
  - `_safe_json_load_dict`
  - `_catalog_identity_key`
  - `_schedule_course_rows_for_schedule`
  - `_hydrate_schedule_courses`
- Verified local test suite after fix: `31 passed`.

## 2026-03-24 — Beta 1.4.2 (dataset deletion hardening)

- Fixed remaining dataset deletion failures by introducing `_csv_courses_for_dataset_name(...)` with normalized dataset-name matching (`trim` + lowercase) and legacy-source-inclusive filtering (`csv` and empty source) for cleanup.
- Applied the robust filter to:
  - `_delete_registry_dataset_row`
  - `api_force_delete_dataset`
  - `api_clean_invalid_datasets`
- Added regression contract assertion for normalized legacy-safe dataset cleanup filter behavior.
- Verified local test suite after fix: `32 passed`.

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

## 2026-07-21 — Beta 1.4 (dashboard fixes)

- Area progress bars now only show required/remaining credits for user's declared major/minor areas (not all areas).
- Fixed mini schedule deduplication: courses no longer doubled from multi-period time slots.
- Converted mini schedule from HTML tables to compact card grid layout with flex-wrap.

## 2026-07-21 — Beta 1.5 (graduation requirements dashboard)

- New `config/graduation_reqs.py`: encodes iCLA graduation requirements from official 2024AY and 2025AY documents.
  - General requirements: English Language, Japanese Language, Foundation, Pathway, QR & Natural Sciences, Major, Total Credits (124).
  - Major-specific designated courses for Psychology (7 courses), Data Science variants (PolEcon, Psychology, Finance).
  - Minor-specific requirements with level distribution and grade B thresholds (Psychology minor).
  - Pathway courses mapped by subject area for area-diversity tracking.
- New `_graduation_requirements_context()` helper in `student_routes.py`:
  - Builds comprehensive checklist evaluating each graduation category against user's completed/planned courses.
  - Auto-detects entry year from expected graduation to select appropriate requirement set.
  - Foundation: auto-detects writing path (Composition track vs Expository Research Writing track).
  - Pathway: tracks unique areas covered (need 3 courses from 3 different subject areas).
  - Major: credit progress with designated course tracking for Psychology and Data Science majors.
  - Minor: level distribution tracking, grade B threshold tracking.
- New Graduation Requirements dashboard section in `dashboard.html`:
  - General requirements checklist with status icons (completed/in-progress/not started).
  - Course chips colored by completion status.
  - Major section with credit progress bar, designated course chips, and other-credits note.
  - Minor section with per-level progress, per-level-group grade B tracking, and eligible course chips.
  - Prompts user to set major/minor in profile when not declared.
- Area progress bars now display total available credits per area (from catalog) as additional metric.
- Added `check-circle` and `minus-circle` SVG icons to the heroicons partial.
- New `_area_matches_major()` fuzzy matcher for area name → major name matching (handles sub-areas like "Interdisciplinary Arts - Language Arts").

### 2025-06-10 – Graduation requirements fixes: title matching, PSYC minor, entry year toggle

- **Fix: Foundation OR logic** – Course title matching now uses prefix/startswith matching instead of exact equality. Catalog titles include section suffixes (e.g., "Expository Research Writing Sec 1") that caused false "missing" status. `_match_completed_title()` and `_match_planned_title()` helper functions replace the old `_is_completed_title()` / `_is_planned_title()` / `_grade_for_title()`.
- **Fix: PSYC minor level distribution** – Corrected from `{100: 2, 200: 3, 300: 1}` to `{100: 2, 200: 2, 300: 2}` per positional analysis of official PDF.
- **Fix: PSYC minor grade B structure** – Replaced flat `grade_b_minimum: 2` with per-level-group structure: `grade_b_by_group: [{levels: [100], needed: 1}, {levels: [200, 300], needed: 3}]` (total 4 courses need B or higher).
- **Feature: Entry year toggle** – Dashboard now includes ≤2024AY / 2025AY+ toggle buttons in the Graduation Requirements header. User can override auto-detected entry year via `?req_year=` query parameter. `_graduation_requirements_context()` accepts `entry_year_override` parameter.
- **Template update** – Minor section now renders grade B requirements per level-group instead of a flat counter.

### 2025-06-10b – Major Option 1 / Option 2 toggle (Seminar+GRP vs courses only)

- **For ≤2024AY entry**: every major offers two options (both total 33 credits):
  - **Option 1**: 30 lecture credits + Seminar (1cr) + Graduation Research Project (2cr)
  - **Option 2**: 33 lecture credits only
- Updated `MAJOR_DESIGNATED` with `other_credits_2024_opt1` and `other_credits_2024_opt2` for all Data Science variants.
- `get_major_requirements()` now accepts `major_option` parameter; returns `has_options`, `major_option`, `lecture_credits`, `seminar_required`.
- `_graduation_requirements_context()` passes `major_option` through; tracks Seminar and Graduation Research Project completion status.
- Dashboard major section shows Option 1 / Option 2 toggle (only for ≤2024AY), option description, and Seminar+GRP status checklist items.
- `?major_option=1|2` query parameter added to dashboard route alongside existing `?req_year=`.
- For 2025AY+: 36 credits, no options, no toggle shown.

### 2025-06-10c – Redesign "My Schedules" card on dashboard

- Replaced boxy individual schedule items with a clean row-based list inside the card.
- Status shown as colored dot (gray=completed, indigo=future) + small pill badge instead of bordered badge.
- Delete button now hidden by default, appears on row hover (ghost icon → red on hover).
- Added calendar icon in card header and total count badge.
- Empty state improved with large faded icon and smaller CTA button.
- New CSS classes: `.ax-sched-row`, `.ax-sched-link`, `.ax-sched-dot`, `.ax-sched-delete`.

### 2025-06-12 – Full redesign of Courses tab (student catalog browser)

- **Layout**: Replaced old table+sidebar with card-based course list + sticky detail drawer.
- **Instant search**: Text search filters by title, course code, or instructor without page reloads.
- **Filter pills**: Dropdown filters for Subject Area and Year Level, with live match counter.
- **AJAX detail loading**: Clicking a course card fetches full detail via new `/student/api/catalog_course_detail` endpoint — no more full-page reloads.
- **Prerequisite status**: Color-coded pills (green=satisfied, red=missing, grey=untrackable) plus eligibility banner ("You meet all prerequisites" / "Some prerequisites not met").
- **Dependency graph**: "What This Unlocks" section with Required/OR classification, alternative branches shown as badge pills. Uses full `dependency_graph_for_course()` AST-based analysis (AND/OR logic).
- **Areas of study**: When a course appears under multiple subject areas, all are shown as violet area pills.
- **Sections**: If multiple sections exist for a course, they're listed with day/period and instructor.
- **Loading skeleton**: Shimmer skeleton placeholders during AJAX loading.
- **Responsive**: Stacks to single column on mobile (≤992px).
- **Route simplification**: `courses_view()` now only loads the catalog list; all detail logic moved to the API endpoint.
- **New CSS classes**: `.ax-catalog-layout`, `.ax-catalog-list`, `.ax-catalog-drawer`, `.ax-cat-card`, `.ax-detail-*`, `.ax-prereq-*`, `.ax-unlock-*`, `.ax-area-pill`, `.ax-eligible-banner`.

### 2025-06-13 – Course catalog: combined catalog, multi-area, prereq fix, more filters

- **Combined catalog**: `_build_combined_course_catalog()` merges ALL datasets into a single deduplicated course list. Groups by normalized course number, picks best row, collects semester availability (Spring/Fall/Both).
- **Multi-area fix**: Cross-listed courses (e.g. ECON/DATA291) now derive areas from course number prefixes via `_PREFIX_TO_AREA` mapping + `_derive_areas_from_course_number()`. Both areas shown as pills on cards and detail drawer.
- **Prerequisite fix**: `_build_prerequisite_status()` now recognizes title-based prerequisites (e.g. "Statistics") by checking `prereq_title_lookup` before marking as untrackable. Previously only course code patterns (XX123) were trackable.
- **Year_Level field fix**: Added lowercase `year_level` key alongside `Year_Level` in `_manual_or_csv_course_to_catalog_row()` to fix template/JS mismatch.
- **New filters**: Semester (Spring/Fall/Both), Credits, and Has Prerequisites filters added to the course catalog filter bar.
- **Template updates**: Removed year/period display, added semester badge on cards, multi-area display on card bottom row, semester badge in detail drawer badges.
- **Data quality**: Bad area values (misaligned CSV columns like "1Year", instructor names) are filtered out during catalog building.

### 2025-06-13b – Catalog cleanup, prereq title substitution, admin sidebar rework

- **Catalog detail drawer cleanup**: Removed sections/instructor/days/periods from the detail drawer and API response. Catalog is now a pure reference tool showing: course name, semester, credits, areas, level, prerequisites, and dependency graph ("what this unlocks"). Removed sections-building code from `api_catalog_course_detail` endpoint. Added level badge to course cards.
- **Prerequisite title substitution pre-processor**: CSV prerequisites use course TITLES (e.g. "Financial Management", "Research Design") not codes. Multi-word titles caused the AST parser to break (adjacent COURSE tokens → syntax error) and AND/OR inside titles (e.g. "Painting AND Sculpting 1") were incorrectly parsed as operators. New `_build_title_to_code_map()` + `_substitute_titles_in_prereq()` functions in `services/prereqs.py` replace known multi-word course titles with their course codes before parsing. Uses longest-match-first strategy. Applied to both `dependency_graph_for_course()` and `_split_prereq_terms()` in student routes.
- **Admin sidebar restructure**: Removed fake "Settings" link (pointed back to dashboard), removed duplicate "Datasets" from System section, moved "Registration Fields" from System to People section, renamed "Users" section to "People", renamed "Landing Management" to "Landing Page". System section now only contains System Health + Landing Page.

### 2025-06-14 – Admin panel features: Activity Log, enhanced dashboard, user search

- **Activity Log page** (`/admin/activity`): New full-page activity log viewer combining `BehaviorEvent` and `AIEvent` records. Features: source filter (behavior/AI), event type dropdown, user search, paginated table (50 per page), metadata JSON modal. Stat cards show total/behavior/AI event counts. API endpoint `/admin/api/activity` returns paginated, filtered, combined events sorted by timestamp.
- **Enhanced dashboard stats**: Replaced generic "Recent Activity / Notifications" stat card with "Platform Events" card showing combined behavior + AI event counts. Added "+N this week" new user indicator on the Total Students card (queries `User.created_at >= 7 days ago` with graceful fallback).
- **Announcements widget on dashboard**: New "Recent Announcements" card on the dashboard showing the 5 most recent announcements with type badges (info/update/warning), dates, and truncated content. Links to Landing Management for full editing.
- **User search/filter on manage_users**: Complete template modernization to match admin design system (`ax-admin-card`, `admin-table`, `ax-page-header`). Added real-time client-side search by username/email/name, role dropdown filter, and graduation year filter. Live counter badge shows "N of M" during filtering. Reset button clears all filters.
- **Sidebar**: Added "Activity Log" link under System section (requires `manage_users` permission).

### 2026-04-09 – Hotfix: IndentationError in admin_routes.py on deploy

- **Bug**: When the Activity Log routes were appended after the system_reset `except` block, the `return jsonify(...)` line from the except handler was accidentally displaced below the new code, leaving it orphaned outside any function. This caused an `IndentationError` at line 5049 preventing Render deploy.
- **Fix**: Removed the stray duplicate return and restored the missing `return` inside the system_reset except block.

---

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
