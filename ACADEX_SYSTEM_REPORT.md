# ACADEX BETA 0.9 SYSTEM EXPLANATION AND IMPROVEMENT REPORT

Version: Draft generated from repository inspection
Scope: Architecture, runtime behavior, data flow, risks, and prioritized fixes
Audience: Engineering team, operators, and LLM-based assistant workflows

---

## SECTION 1: What This App Is

Acadex is a Flask-based academic planning platform for a university context. It supports two primary personas:

1. Students: Build and maintain past/future schedules, search catalog courses, track progress, and use AI-assisted recommendations.
2. Admins: Manage datasets, mappings, subjects, instructors, faculty links, periods, analytics, and platform health.

The core value proposition is contextual planning:

- Schedules are tied to year/semester and schedule type (past/future).
- Course availability is resolved from dataset metadata rather than a single global active catalog.
- Validation is integrated in the planner: prerequisites, time conflicts, and duplicate identity checks.

The app is best thought of as:

- A schedule planner
- A catalog governance tool
- A rules engine (prereq/conflict/eligibility)
- A lightweight analytics and operations panel

---

## SECTION 2: High-Level Architecture and Connections

### 2.1 Backend Framework and Modules

- Framework: Flask
- Persistence: Flask-SQLAlchemy + Alembic migrations
- Session/Auth: Flask-Login + server-side session table support
- Blueprints:
  - auth routes: login/register/logout
  - main routes: landing page and public announcement APIs
  - admin routes: dataset, governance, analytics, health, AI settings
  - student routes: planning, schedule editing, notifications, AI actions
  - schedule routes: dashboard alias and schedule deletion path

Entry point responsibilities in app.py:

- App config and extension setup
- Blueprint registration
- Security middleware (headers, CSRF checks, throttling)
- Error handlers for API and HTML behaviors
- Startup data/bootstrap guards
- Runtime schema compatibility checks

### 2.2 Data Model Domains

1. Identity and RBAC
- User, Role, Permission, LoginAttempt, RegistrationField

2. Academic Planning
- Schedule, ScheduleCourse, SubjectArea, Period

3. Catalog and Dataset Governance
- Course, DatasetRegistry, CatalogStatus
- Legacy retained models: ScrapedDataset, ScrapedCourse

4. Faculty Linking
- Faculty with many-to-many bridge via course_faculty

5. Telemetry and Messaging
- Notification, AIEvent, BehaviorEvent

6. App Content/Settings
- Setting, Announcement, LandingPageSection

### 2.3 Why DatasetRegistry Is Central

DatasetRegistry stores authoritative metadata about available catalogs:

- year, semester
- dataset type
- table_name/source lineage
- naming and creation metadata

The resolver utility picks the best dataset for a planning context:

1. Exact year+semester match
2. Fallback to same-year full_year dataset
3. Fallback to latest available entry

This keeps planning resilient when exact term data is missing.

---

## SECTION 3: End-to-End Request and Data Flow

### 3.1 Student Planning Flow

1. Student creates a schedule with name, year, semester, and type.
2. Backend resolves dataset context for that term.
3. Course search uses resolved dataset rows and user filters.
4. Student adds course sections through API.
5. Backend validates:
   - ownership/guest access scope
   - duplicate identity rules
   - conflict checks (day/period block overlap)
   - prerequisite status pipeline
6. UI refreshes grid/list state from server payload.
7. Grades can be updated for completed/past usage; GPA/progress metrics update.

### 3.2 Admin Ingestion and Governance Flow

1. Admin uploads CSV.
2. Header mapping is normalized through central mapping helpers.
3. Dataset context is inferred from filename/form metadata.
4. Semester/type are normalized.
5. Course rows are upserted with source='csv' and dataset_name.
6. Faculty parsing and linking runs through normalization/sync pipeline.
7. DatasetRegistry is upserted and CatalogStatus synchronized.
8. Admin can preview columns, refresh metadata, inspect impact, and delete/force-delete.

### 3.3 AI Recommendation/Generation Flow

1. Student consent and context are read.
2. Catalog candidates are filtered by prerequisite eligibility.
3. Recommendation ranking scores include:
   - major/minor area alignment
   - missing credits and historical area behavior
   - peer and behavior affinity signals
   - faculty and course historical grade outcomes
4. Generated schedule assembly then excludes time conflicts using period block parsing.

---

## SECTION 4: Services and Internal Engines

### 4.1 Prerequisite Engine (services/prereqs.py)

This module parses prerequisite text into expression tokens and evaluates eligibility against completed courses.

Notable capabilities:

- Handles separators and grouped conditions
- Supports passing-grade semantics from prior schedules
- Produces explainable statuses for UI/API display

### 4.2 Conflict Engine (services/conflicts.py)

This module converts day/period strings into normalized occupancy blocks and checks overlap.

Design outcome:

- Conflict detection is deterministic and reusable by both manual add and AI generation flows.

### 4.3 Eligibility Layer (services/eligibility.py)

Filters course rows by prerequisite eligibility and returns both eligible set and parse/evaluation warnings.

### 4.4 AI Utilities (services/ai/*)

- common.py: prerequisite-aware filtering bridge
- recommendations.py: multi-factor ranking and explainable reasons
- generator.py: picks conflict-free, prerequisite-eligible sections
- degree_solver.py: area-focused eligibility helper

---

## SECTION 5: Frontend Structure and Backend Integration

### 5.1 Rendering Strategy

- Jinja templates provide server-rendered page shells.
- JavaScript layer handles dynamic planner interactions and API-driven updates.

### 5.2 Student JS Responsibilities

The student script orchestrates:

- course search requests
- schedule grid rendering
- add/remove course operations
- course detail modal retrieval
- grade update calls
- notifications operations
- schedule PNG export via html2canvas

This keeps planner interactions responsive while preserving server authority on validation.

### 5.3 Security and CSRF in Client-Server Calls

Mutating APIs are guarded in backend middleware. Frontend requests include token-aware handling and rely on session-auth context.

---

## SECTION 6: Security Posture (Current)

### 6.1 Existing Positive Controls

1. Global pre-request security hook for mutating verbs
2. CSRF token verification for state-changing endpoints
3. Rate-limiting buckets by route/actor class
4. Session/cookie hardening and optional HSTS behavior
5. Safe API error wrappers (reduce accidental stack leak)
6. Identifier validation helpers for dynamic SQL identifiers
7. Admin access check + permission map enforcement infrastructure

### 6.2 Security-Adjacent Risks Identified

1. Runtime environment drift can invalidate imports despite passing tests.
2. Legacy password fallback logic in auth path may keep weak compatibility paths alive longer than desired.
3. One admin endpoint impact path had enforcement-map ambiguity risk (policy consistency concern).
4. In-process rate limit strategy is limited under multi-worker/distributed deployment.

---

## SECTION 7: Reliability and Operational Behavior

### 7.1 Runtime Validation vs Test Coverage Gap

Observed in audit execution:

- Test suite passed in local run (contract-heavy tests).
- Runtime import sanity failed due corrupted SQLAlchemy installation state in the active virtual environment.

Key lesson:

Passing tests do not automatically guarantee environment integrity or production startup safety.

### 7.2 Startup and Self-Heal Behavior

app.py includes bootstrap and schema compatibility checks to improve startup resilience, but package corruption still requires external remediation.

### 7.3 Data Integrity Features

- Dataset impact analysis before destructive actions
- Dependency-aware dataset deletion cleanup
- Action logging in system health metrics for mutating operations
- Notifications to affected users during linked cleanup events

---

## SECTION 8: Suggested Fixes and Improvements (Prioritized)

### Priority 0 (Immediate)

1. Repair and lock Python environment deterministically.
- Recreate virtual environment from clean base.
- Reinstall pinned dependencies from requirements.
- Add startup smoke check in CI/CD: import Flask app + minimal DB init.

2. Add runtime boot test to automated pipeline.
- Example: instantiate app and hit lightweight health route in test harness.
- Purpose: catch import/runtime dependency corruption that contract tests miss.

### Priority 1 (Security and Governance)

1. Remove legacy plaintext password fallback after migration window.
- Keep one-time migration script for remaining legacy users.
- Enforce hashed-password-only verification path.

2. Make admin permission map completeness test mandatory.
- Add a contract that scans admin blueprint endpoints and asserts every mutating endpoint is explicitly mapped/justified.

3. Move rate limiting state to shared backend for scale.
- Redis-backed limiter avoids per-process loopholes under multiple workers.

### Priority 2 (Data/Platform Quality)

1. Add schema-level guardrails where feasible.
- Unique constraints/indexes for schedule course identity dimensions aligned with current logic.

2. Standardize structured logging fields.
- Keep action logs machine-readable with stable keys for external SIEM or incident workflows.

3. Add catalog resolver telemetry.
- Track fallback mode usage rates (exact vs full_year vs latest) to detect dataset coverage gaps.

### Priority 3 (Developer Experience)

1. Add make-like command shortcuts.
- bootstrap, test, lint, run, migrate in one documented interface.

2. Expand architecture tests.
- Route ownership and authorization invariants.
- Dataset resolver behavior across edge terms.
- Faculty-linking enforcement in CSV ingestion.

---

## SECTION 9: How Major Components Connect (Dependency Map)

1. app.py
- Composes app, security hooks, error handling, bootstrap, and blueprint registration.

2. Routes/*
- HTTP orchestration layer: validates request context and delegates to model/services/util helpers.

3. models.py
- Persistence contracts and entity relationships consumed by routes/services.

4. utils.py
- Shared glue for dataset resolution, mapping normalization, faculty sync, settings helpers.

5. services/*
- Deterministic business logic engines (prereqs, conflicts, eligibility, AI helpers).

6. static/js/* + templates/*
- UX and interaction layer that calls student/admin APIs and renders server/context data.

This separation is generally healthy: request orchestration in routes, domain logic in services, storage in models, and multi-route helper logic in utils.

---

## SECTION 10: Environment and Deployment Notes

### 10.1 Current observed environment caveat

In audit execution, active venv exhibited SQLAlchemy package corruption symptoms:

- Import path failure on sqlalchemy.engine
- Irregular site-packages directory shape

Action:

- Recreate venv and reinstall dependencies before production verification.

### 10.2 Migration safety

Schedule identity write paths depend on identity columns being present. Deploy process must run Alembic migrations before write traffic is enabled.

### 10.3 Suggested deployment checklist

1. Create clean venv from approved Python version.
2. Install requirements and verify lock integrity.
3. Run Alembic upgrade.
4. Run automated tests.
5. Run runtime smoke test (app import + health endpoint).
6. Verify CSRF/session cookie settings for environment.
7. Confirm admin RBAC mapping checks pass.
8. Launch with production WSGI and shared limiter backend.

---

## SECTION 11: Testing Posture and Gaps

### Strengths

- Good set of contract tests around identity write path, dataset and TBA handling, student view/export behavior, health action logs, eligibility/prereqs/conflicts.

### Gaps

1. Runtime startup/import test not always present in default suite.
2. Security policy completeness tests can be extended.
3. End-to-end multi-worker operational tests are limited.

### Recommended additions

1. App boot test in CI (mandatory).
2. RBAC coverage assertions for all mutating admin endpoints.
3. Integration test for CSV ingestion with malformed header variants and faculty-missing rows.
4. Load/behavior test for limiter and conflict checks under concurrent calls.

---

## SECTION 12: Machine-Readable Summary for LLM Agents

Use the following structured summary block for downstream prompt generation.

```text
SYSTEM_NAME: Acadex
PRIMARY_DOMAIN: University schedule planning + catalog governance
CORE_STACK: Flask, SQLAlchemy, Alembic, Jinja, JavaScript
PRIMARY_BLUEPRINTS: auth, main, admin, student, schedule
PRIMARY_MODELS: User, Role, Permission, Schedule, ScheduleCourse, Course, DatasetRegistry, CatalogStatus, Faculty, Notification, AIEvent, BehaviorEvent, Setting
PRIMARY_SERVICES: prereqs, conflicts, eligibility, ai.recommendations, ai.generator
DATASET_RESOLUTION: exact(year+semester) -> full_year same year -> latest
CRITICAL_GUARDS: CSRF on mutating verbs, session hardening, admin RBAC checks, rate limiting
KNOWN_HIGH_RISK: environment drift/package corruption can bypass confidence from contract-only tests
TOP_FIXES:
- Rebuild and lock runtime environment
- Add boot/runtime smoke test in CI
- Remove legacy plaintext password fallback
- Enforce full RBAC map completeness tests
- Move limiter to shared backend for horizontal scale
```

---

## SECTION 13: Practical Next Sprint Plan

1. Reliability sprint
- Deliver clean reproducible environment setup and runtime smoke checks.

2. Security hardening sprint
- Remove legacy auth fallback and formalize endpoint-permission completeness tests.

3. Observability sprint
- Expand structured action logs and catalog resolver telemetry.

4. Scalability sprint
- Integrate Redis limiter backend and validate multi-worker behavior.

---

## SECTION 14: Final Assessment

Acadex has a strong modular foundation for a beta-stage planning platform, including notable maturity in dataset governance, schedule identity handling, and domain services for prerequisites/conflicts.

Primary near-term risk is not architectural shape, but operational consistency:

- Environment reproducibility,
- startup/runtime validation,
- and strict security policy completeness checks.

If the top-priority fixes are implemented, the platform can move from feature-complete beta toward significantly safer production-readiness.
