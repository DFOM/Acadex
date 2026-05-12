// static/js/student.js

document.addEventListener('DOMContentLoaded', () => {

    let allCatalogCourses = [];

    // DOM Elements
    const searchInput = document.getElementById('courseSearchInput');
    const filterSubjectInput = document.getElementById('filterSubject');
    const filterInstructorInput = document.getElementById('filterInstructor');
    const resultsContainer = document.getElementById('searchResults');
    const scheduleIdInput = document.getElementById('current_schedule_id');
    const coursesDataElement = document.getElementById('currentCoursesData');
    const obtainedCoursesElement = document.getElementById('obtainedCoursesData');
    const resultCountBadge = document.getElementById('resultCountBadge');
    const totalCreditsBadge = document.getElementById('totalCreditsBadge');
    const levelScopeSelect = document.getElementById('filterLevelScope');
    const exportSchedulePngBtn = document.getElementById('exportSchedulePngBtn');

    // Load the student's historical obtained courses
    const obtainedCourses = obtainedCoursesElement ? JSON.parse(obtainedCoursesElement.textContent) : [];
    const scheduleType = String(window.SCHEDULE_TYPE || '').toLowerCase();
    const scheduleLevel = Number(window.SCHEDULE_LEVEL);

    // Current schedule courses (server-rendered initial state)
    let currentScheduleCourses = coursesDataElement ? JSON.parse(coursesDataElement.textContent) : [];

    // Formatter Helpers (backward-compatible fallbacks)
    const formatCourseCode = (rawCode) => String(rawCode || '').replace(/-S(\d+)-(Sp|Fa).*/i, ' Section $1').replace(/-V\d+/i, '');
    const formatShortCourseCode = (rawCode) => String(rawCode || '').replace(/-S(\d+)-(Sp|Fa).*/i, ' Sec $1').replace(/-V\d+/i, '');
    const getBaseCode = (rawCode) => String(rawCode || '').replace(/-S\d+-.*/i, '').trim().toUpperCase();
    const normalizePrereqToken = (raw) => String(raw || '').replace(/-(S\d+|SP|FA|V\d+).*/i, '').replace(/[^A-Za-z0-9]/g, '').toUpperCase();
    const obtainedCourseSet = new Set(obtainedCourses.map((c) => normalizePrereqToken(c)).filter(Boolean));
    const prereqTitleLookup = new Map();

    const escapeHtml = (value) => String(value || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');

    const cleanInstructorDisplay = (value) => {
        if (Array.isArray(value)) {
            return value.map((v) => String(v || '').trim()).filter(Boolean).join(', ');
        }
        let text = String(value || '').trim();
        if (!text) return 'TBA';
        try {
            const parsed = JSON.parse(text);
            if (Array.isArray(parsed)) {
                const joined = parsed.map((v) => String(v || '').trim()).filter(Boolean).join(', ');
                if (joined) return joined;
            }
        } catch (_) {}
        if (text.startsWith('[') && text.endsWith(']')) {
            text = text.slice(1, -1).replace(/["']/g, '').trim();
        }
        return text || 'TBA';
    };

    const getDisplayCourseNumber = (course) => {
        return (
            course?.display_course_number ||
            course?.displayCourseNumber ||
            formatCourseCode(course?.Course_Number || course?.course_number || '')
        );
    };

    const getDisplayTitle = (course) => {
        return course?.display_title || course?.displayTitle || course?.Course_Title || course?.course_title || '';
    };

    const instanceIdentityKey = (course) => {
        const courseNumber = String(course?.Course_Number || course?.course_number || '').trim().toUpperCase();
        const dayPeriod = String(course?.identity_day_period || course?.Day_and_Period || course?.day_period || '').trim().toLowerCase();
        const semester = String(course?.identity_semester || course?.semester || '').trim().toLowerCase();
        const year = String(course?.identity_year || course?.year || '').trim().toLowerCase();
        const datasetName = String(course?.identity_dataset_name || course?.dataset_name || '').trim().toLowerCase();
        return [courseNumber, dayPeriod, semester, year, datasetName].join('|');
    };

    const splitPrereqTerms = (prereqString) => {
        const raw = String(prereqString || '').trim();
        if (!raw) return [];
        const standardized = raw
            .replace(/AND\/OR/gi, ' OR ')
            .replace(/\s*&\s*/g, ' AND ')
            .replace(/\s*\|\s*/g, ' OR ');
        return standardized
            .split(/\bAND\b|\bOR\b|\(|\)|,|;/gi)
            .map((part) => part.trim())
            .filter(Boolean);
    };

    const analyzePrerequisiteStatus = (prereqString) => {
        const raw = String(prereqString || '').trim();
        if (scheduleType !== 'future' || !raw || ['none', 'n/a', 'na'].includes(raw.toLowerCase())) {
            return {
                hasPrerequisites: false,
                state: 'none',
                summary: 'No prerequisites.',
                items: [],
                warningMessage: null,
                satisfiedCount: 0,
                missingCount: 0,
                untrackableCount: 0,
            };
        }

        const terms = splitPrereqTerms(raw);
        let satisfiedCount = 0;
        let missingCount = 0;
        let untrackableCount = 0;

        const items = terms.map((term) => {
            const lowered = term.toLowerCase();
            const looksLikeCourse = /[A-Za-z]{2,}\s*-?\s*\d{2,}/.test(term);

            if (looksLikeCourse) {
                const normalized = normalizePrereqToken(term);
                const displayTerm = prereqTitleLookup.get(normalized) || 'Required course';
                if (normalized && obtainedCourseSet.has(normalized)) {
                    satisfiedCount += 1;
                    return {
                        term: displayTerm,
                        status: 'satisfied',
                        reason: 'Found in your completed past schedules with a passing grade.'
                    };
                }
                missingCount += 1;
                return {
                    term: displayTerm,
                    status: 'missing',
                    reason: 'Not found in your completed past schedules.'
                };
            }

            if (lowered.includes('major') || lowered.includes('minor')) {
                untrackableCount += 1;
                return {
                    term,
                    status: 'untrackable',
                    reason: 'Depends on declared major/minor or policy checks outside past-course history.'
                };
            }

            untrackableCount += 1;
            return {
                term,
                status: 'untrackable',
                reason: 'Cannot be auto-verified (placement/external or policy requirement).'
            };
        });

        const allSatisfied = missingCount === 0 && untrackableCount === 0 && items.length > 0;
        return {
            hasPrerequisites: true,
            state: allSatisfied ? 'satisfied' : 'warning',
            summary: allSatisfied
                ? 'All prerequisite items are satisfied from past schedules.'
                : `${satisfiedCount}/${items.length} prerequisite items verified.`,
            items,
            warningMessage: allSatisfied ? null : 'Missing/untrackable prerequisites are allowed but shown as a warning.',
            satisfiedCount,
            missingCount,
            untrackableCount,
        };
    };

    const prereqItemBadge = (item) => {
        if (!item || !item.term) return '';
        const status = item.status || 'untrackable';
        const theme = status === 'satisfied'
            ? 'bg-success-subtle text-success border-success-subtle'
            : status === 'missing'
                ? 'bg-danger-subtle text-danger border-danger-subtle'
                : 'bg-secondary-subtle text-secondary border-secondary-subtle';
        return `<span class="badge ${theme} border rounded-pill" title="${escapeHtml(item.reason || '')}">${escapeHtml(item.term)}</span>`;
    };

    const getPrereqBadgeHtml = (prereqString) => {
        const status = analyzePrerequisiteStatus(prereqString);
        if (!status.hasPrerequisites) return '';
        if (status.state === 'satisfied') {
            return `<span class="badge bg-success bg-opacity-10 text-success border border-success-subtle rounded-pill ms-2" title="${escapeHtml(status.summary)}">${heroIcon('check-circle')}Prereqs Met</span>`;
        }
        return `<span class="badge bg-warning bg-opacity-10 text-warning border border-warning-subtle rounded-pill ms-2" title="${escapeHtml(status.warningMessage || status.summary)}">${heroIcon('exclamation-triangle')}Prereq Check</span>`;
    };

    const prereqInlineListHtml = (prereqString) => {
        const status = analyzePrerequisiteStatus(prereqString);
        if (!status.hasPrerequisites || !status.items.length) return '';
        const badges = status.items.map(prereqItemBadge).join(' ');
        return `<div class="mt-1 d-flex flex-wrap gap-1">${badges}</div>`;
    };

    const decodeDataAttr = (value) => {
        try {
            return decodeURIComponent(String(value || ''));
        } catch (_) {
            return String(value || '');
        }
    };

    const openCourseDetail = async ({ scheduleId, courseNumber, dayAndPeriod }) => {
        const titleEl = document.getElementById('courseDetailTitle');
        const bodyEl = document.getElementById('courseDetailBody');
        const modalEl = document.getElementById('courseDetailModal');
        if (!titleEl || !bodyEl || !modalEl) return;

        titleEl.textContent = 'Course Detail';
        bodyEl.innerHTML = '<div class="text-muted small">Loading course detail...</div>';
        const modal = new bootstrap.Modal(modalEl);
        modal.show();

        try {
            const params = new URLSearchParams({
                schedule_id: String(scheduleId || ''),
                course_number: String(courseNumber || ''),
                day_and_period: String(dayAndPeriod || ''),
            });
            const response = await fetch(`/student/api/course_detail?${params.toString()}`);
            const result = await response.json();
            if (!response.ok || !result.success) {
                bodyEl.innerHTML = `<div class="alert alert-warning mb-0">${escapeHtml(result.message || 'Unable to load course detail.')}</div>`;
                return;
            }

            const course = result.course || {};
            const prereqStatus = result.prerequisite_status || {};
            const prereqItems = Array.isArray(prereqStatus.items) ? prereqStatus.items : [];
            const unlocks = Array.isArray(result.unlocks) ? result.unlocks : [];
            const unlockCount = Number(result.unlock_count || unlocks.length || 0);

            titleEl.textContent = course.display_title || course.course_title || 'Course Detail';

            const prereqBadges = prereqItems.length
                ? `<div class="d-flex flex-wrap gap-1 mt-1">${prereqItems.map(prereqItemBadge).join('')}</div>`
                : '<div class="small text-muted mt-1">No prerequisite items.</div>';

            const unlockRows = unlocks.length
                ? unlocks.slice(0, 60).map((item) => `
                    <li class="list-group-item px-0 py-2 border-0 border-bottom">
                        <div class="fw-semibold">${escapeHtml(item.display_title || item.course_title || 'Untitled')}</div>
                        <div class="small text-muted">${escapeHtml(item.display_course_number || item.course_number || '')} · ${escapeHtml(item.instructor || 'TBA')}</div>
                    </li>
                `).join('')
                : '<div class="small text-muted">No reverse dependencies found in this term.</div>';

            bodyEl.innerHTML = `
                <div class="mb-3">
                    <div class="d-flex flex-wrap gap-2 mb-1">
                        <span class="badge bg-light text-dark border">${escapeHtml(course.display_course_number || course.course_number || '')}</span>
                        <span class="badge bg-primary-subtle text-primary border border-primary-subtle">${escapeHtml(String(course.credits ?? 0))} CR</span>
                    </div>
                    <div class="small text-muted">Instructor: ${escapeHtml(course.instructor || 'TBA')} · Area: ${escapeHtml(course.area_of_study || 'General')}</div>
                </div>

                <div class="mb-3">
                    <div class="fw-bold mb-1">Prerequisites</div>
                    <div class="small text-muted">${escapeHtml(prereqStatus.summary || 'No prerequisites.')}</div>
                    ${prereqBadges}
                </div>

                <div>
                    <div class="fw-bold mb-1">Unlocks (${unlockCount})</div>
                    <ul class="list-group list-group-flush">${unlockRows}</ul>
                </div>
            `;
        } catch (err) {
            bodyEl.innerHTML = '<div class="alert alert-danger mb-0">Unable to load course detail right now.</div>';
        }
    };

    // ==========================================
    // Heroicons (inline SVG strings)
    // ==========================================
    const heroIcon = (name) => {
        const common = 'class="ax-icon me-1" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"';
        switch (name) {
            case 'check-circle':
                return `<svg ${common}><path d="M20 6 9 17l-5-5"/></svg>`;
            case 'exclamation-triangle':
                return `<svg ${common}><path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.29 3.86h3.42l7.32 12.68A2 2 0 0 1 19.32 20H4.68a2 2 0 0 1-1.71-3.46l7.32-12.68a2 2 0 0 1 0 0Z"/></svg>`;
            case 'lock-open':
                return `<svg ${common}><path d="M7 10V8a5 5 0 0 1 10 0"/><path d="M5 10h14v10H5z"/><path d="M12 14v2"/></svg>`;
            case 'info':
                return `<svg ${common}><path d="M12 16v-4"/><path d="M12 8h.01"/><circle cx="12" cy="12" r="10"/></svg>`;
            case 'clock':
                return `<svg ${common}><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>`;
            case 'stack':
                return `<svg ${common}><path d="M12 3 2 9l10 6 10-6-10-6Z"/><path d="M2 15l10 6 10-6"/><path d="M2 12l10 6 10-6"/></svg>`;
            default:
                return '';
        }
    };

    // ==========================================
    // 0. GRID VIEW RENDERING (Calendar-like)
    // ==========================================
    // Keep the UI calm + premium: use the theme primary for blocks.

    const normalizeDayToken = (value) => {
        const text = String(value || '').trim().toLowerCase();
        if (/^m(on(day)?)?$/.test(text)) return 'Mon';
        if (/^tu(e(s(day)?)?)?$/.test(text)) return 'Tue';
        if (/^w(ed(nesday)?)?$/.test(text)) return 'Wed';
        if (/^th(u(r(s(day)?)?)?)?$/.test(text)) return 'Thu';
        if (/^f(ri(day)?)?$/.test(text)) return 'Fri';
        const match = text.match(/mon|tue|wed|thu|fri/);
        return match ? match[0].charAt(0).toUpperCase() + match[0].slice(1) : '';
    };

    const normalizePeriodToken = (value) => {
        const match = String(value || '').match(/\d+/);
        return match ? String(Number(match[0])) : String(value || '').trim();
    };

    const parseDayPeriodBlocks = (timeStr) => {
        // Converts strings like "Mon 1 Wed 2", "Mon Period 1", or "Mon1,Wed1" into grid blocks.
        if (!timeStr) return [];
        const raw = String(timeStr).trim();
        if (!raw || ['TBA', 'N/A', 'NA', 'NONE'].includes(raw.toUpperCase())) return [];

        const tokens = raw.split(/[\s,;|\/]+/).filter(Boolean);
        let currentDay = '';
        const blocks = [];
        const seen = new Set();

        for (const token of tokens) {
            const dayMatch = token.match(/(Mon(day)?|Tue(s(day)?)?|Wed(nesday)?|Thu(r(s(day)?)?)?|Fri(day)?)/i)
                || token.match(/^(M|Tu|W|Th|F)$/i);
            if (dayMatch) currentDay = normalizeDayToken(dayMatch[0]);

            const rangeMatch = token.match(/(\d+)\s*[-–—]\s*(\d+)/);
            const nums = rangeMatch
                ? Array.from({ length: Math.max(0, Number(rangeMatch[2]) - Number(rangeMatch[1]) + 1) }, (_, idx) => Number(rangeMatch[1]) + idx)
                : (token.match(/\d+/g) || []).map(Number);

            for (const num of nums) {
                if (!currentDay || !Number.isFinite(num)) continue;
                const period = String(num);
                const key = `${currentDay}|${period}`;
                if (seen.has(key)) continue;
                seen.add(key);
                blocks.push({ day: currentDay, period });
            }
        }
        return blocks;
    };

    const getScheduleCell = (day, period) => {
        const exact = document.getElementById(`cell-${day}${period}`);
        if (exact) return exact;
        const normalizedPeriod = normalizePeriodToken(period);
        return Array.from(document.querySelectorAll('.schedule-cell')).find((cell) => {
            return String(cell.getAttribute('data-day') || '') === day
                && normalizePeriodToken(cell.getAttribute('data-period-name')) === normalizedPeriod;
        }) || null;
    };

    const parseHHMMToMinutes = (value) => {
        const text = String(value || '').trim();
        const match = text.match(/^(\d{1,2}):(\d{2})$/);
        if (!match) return null;
        const hour = Number(match[1]);
        const minute = Number(match[2]);
        if (!Number.isFinite(hour) || !Number.isFinite(minute) || hour < 0 || hour > 23 || minute < 0 || minute > 59) return null;
        return (hour * 60) + minute;
    };

    const getPeriodTimeMap = () => {
        const map = {};
        document.querySelectorAll('th[data-period-name]').forEach((th) => {
            const period = String(th.getAttribute('data-period-name') || '').trim();
            const startMins = parseHHMMToMinutes(th.getAttribute('data-start-time'));
            const endMins = parseHHMMToMinutes(th.getAttribute('data-end-time'));
            if (!period || startMins === null || endMins === null || endMins <= startMins) return;
            map[period] = { start: startMins, end: endMins };
        });
        return map;
    };

    const buildTimeWindows = (dayPeriodValue, periodMap) => {
        return parseDayPeriodBlocks(dayPeriodValue)
            .map((block) => {
                const slot = periodMap[String(block.period || '')];
                if (!slot) return null;
                return { day: block.day, start: slot.start, end: slot.end };
            })
            .filter(Boolean);
    };

    const hasAnyTimeOverlap = (windowsA, windowsB) => {
        for (const left of windowsA) {
            for (const right of windowsB) {
                if (left.day !== right.day) continue;
                if (left.start < right.end && right.start < left.end) return true;
            }
        }
        return false;
    };

    const popoverHtmlForCourse = (course) => {
        const title = getDisplayTitle(course) || '(Untitled)';
        const credits = course.Credits ?? '';
        const instructor = (course.Instructor || '').trim() || 'TBA';
        const when = course.Day_and_Period || 'TBA';
        const prereq = (course.Prerequisite || '').trim();
        const prereqStatus = analyzePrerequisiteStatus(prereq);

        const prereqLines = prereqStatus.hasPrerequisites
            ? prereqStatus.items.map((item) => {
                const icon = item.status === 'satisfied' ? '✅' : (item.status === 'missing' ? '❌' : '⚠️');
                return `<div>${icon} ${escapeHtml(item.term)} <span class="text-muted">(${escapeHtml(item.reason)})</span></div>`;
            }).join('')
            : '<div>None</div>';

        return `
            <div class="small">
                <div class="fw-bold mb-1">${title}</div>
                <div><span class="text-muted">Faculty:</span> ${instructor}</div>
                <div><span class="text-muted">Credits:</span> ${credits}</div>
                <div><span class="text-muted">When:</span> ${when}</div>
                <div class="mt-1"><span class="text-muted">Prereqs:</span> ${escapeHtml(prereqStatus.summary)}</div>
                <div class="mt-1">${prereqLines}</div>
            </div>
        `.trim();
    };

    const initPopovers = () => {
        const els = [].slice.call(document.querySelectorAll('[data-bs-toggle="popover"]'));
        els.forEach((el) => {
            try {
                const existing = bootstrap.Popover.getInstance(el);
                if (existing) existing.dispose();
                new bootstrap.Popover(el, { html: true, sanitize: true, container: 'body' });
            } catch (e) {
                // no-op
            }
        });
    };

    const initTooltips = () => {
        const els = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
        els.forEach((el) => {
            try {
                const existing = bootstrap.Tooltip.getInstance(el);
                if (existing) existing.dispose();
                new bootstrap.Tooltip(el, { container: 'body', trigger: 'hover focus' });
            } catch (_) {}
        });
    };

    const renderGrid = (courses) => {
        const cells = document.querySelectorAll('.schedule-cell');
        if (!cells.length) return;
        cells.forEach(c => (c.innerHTML = ''));

        const unscheduledPanel = document.getElementById('unscheduledCoursesPanel');
        const unscheduledList = document.getElementById('unscheduledCoursesList');
        const unscheduledCount = document.getElementById('unscheduledCount');
        const unscheduledCourses = [];

        courses.forEach((course, index) => {
            const blocks = parseDayPeriodBlocks(course.Day_and_Period);
            if (!blocks.length) {
                unscheduledCourses.push(course);
                return;
            }

            const title = getDisplayTitle(course) || '(Untitled)';
            const instructor = cleanInstructorDisplay(course.Instructor);
            const prereqStatus = analyzePrerequisiteStatus(course.Prerequisite);
            const prereqPill = prereqStatus.hasPrerequisites
                ? `<span class="badge ${prereqStatus.state === 'satisfied' ? 'bg-success-subtle text-success border border-success-subtle' : 'bg-warning-subtle text-warning border border-warning-subtle'} rounded-pill mt-1" style="font-size: 0.58rem;">${prereqStatus.state === 'satisfied' ? 'Prereq ✓' : 'Prereq !'}</span>`
                : '';
            const isAuthed = Boolean(window.IS_AUTHENTICATED);
            const gradeValue = String(course.grade || '').toUpperCase();
            const gradeChips = ['A', 'B', 'C', 'D', 'F']
                .map((gradeLabel) => {
                    const isActive = gradeValue === gradeLabel;
                    return `<button type="button" class="grade-chip ${isActive ? 'grade-active' : ''}" data-entry-id="${course.entry_id}" data-grade="${gradeLabel}" ${isAuthed ? '' : 'disabled'} data-bs-toggle="tooltip" data-bs-title="Optional: Select a grade to help estimate GPA and performance.">${gradeLabel}</button>`;
                }).join('');
            const gradeRow = `<div class="d-flex flex-wrap gap-1 mt-1">${gradeChips}</div>`;
            const popTitle = title.replace(/"/g, '&quot;');
            const popContent = popoverHtmlForCourse(course).replace(/"/g, '&quot;');

            blocks.forEach(({ day, period }) => {
                const cell = getScheduleCell(day, period);
                if (!cell) return;

                const wrapper = document.createElement('div');
                wrapper.className = 'ax-course ax-appear mb-1 text-white text-start position-relative w-100';
                wrapper.style.minHeight = '75px';
                wrapper.setAttribute('tabindex', '0');
                wrapper.setAttribute('role', 'button');
                wrapper.setAttribute('data-bs-toggle', 'popover');
                wrapper.setAttribute('data-bs-trigger', 'focus');
                wrapper.setAttribute('data-bs-title', popTitle);
                wrapper.setAttribute('data-bs-content', popContent);

                wrapper.innerHTML = `
                    <div class="p-2 d-flex flex-column justify-content-center pe-4">
                        <strong class="d-block lh-sm mb-1" style="font-size: 0.75rem; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;">${title}</strong>
                        <span class="d-block text-truncate" style="font-size: 0.65rem; opacity: 0.9;">${instructor}</span>
                        ${prereqPill}
                        ${gradeRow}
                    </div>
                    <button class="btn btn-sm btn-link text-white position-absolute top-0 end-0 p-0 m-1 remove-course-btn drop-no-confirm" 
                            data-entry-id="${course.entry_id}" 
                            style="opacity: 0.85; font-size: 0.8rem; text-decoration: none; z-index: 10;">✖</button>
                `;

                cell.appendChild(wrapper);
            });
        });

        // Render unscheduled/TBA courses panel
        if (unscheduledPanel && unscheduledList) {
            if (unscheduledCourses.length > 0) {
                unscheduledPanel.style.display = '';
                if (unscheduledCount) unscheduledCount.textContent = unscheduledCourses.length;
                const isAuthed = Boolean(window.IS_AUTHENTICATED);
                unscheduledList.innerHTML = unscheduledCourses.map((course) => {
                    const title = getDisplayTitle(course) || '(Untitled)';
                    const displayCode = getDisplayCourseNumber(course);
                    const credits = course.Credits ?? '';
                    const gradeValue = String(course.grade || '').toUpperCase();
                    const gradeChips = ['A', 'B', 'C', 'D', 'F']
                        .map((g) => {
                            const isActive = gradeValue === g;
                            return `<button type="button" class="grade-chip ${isActive ? 'grade-active' : ''}" data-entry-id="${course.entry_id}" data-grade="${g}" ${isAuthed ? '' : 'disabled'}>${g}</button>`;
                        }).join('');
                    return `<div class="ax-card p-2 d-flex align-items-center gap-2" style="min-width:220px;max-width:340px;background:var(--ax-surface);border:1px solid var(--ax-border);">
                        <div class="flex-grow-1" style="min-width:0;">
                            <div class="fw-bold text-truncate" style="font-size:.82rem;color:var(--ax-text);">${escapeHtml(title)}</div>
                            <div class="d-flex align-items-center gap-1 mt-1">
                                <span class="badge bg-light text-dark border" style="font-size:.65rem;">${escapeHtml(displayCode)}</span>
                                <span class="badge bg-primary-subtle text-primary border border-primary-subtle" style="font-size:.65rem;">${credits} CR</span>
                                <span class="badge bg-warning-subtle text-warning border border-warning-subtle" style="font-size:.65rem;">TBA</span>
                            </div>
                            <div class="d-flex flex-wrap gap-1 mt-1">${gradeChips}</div>
                        </div>
                        <button class="btn btn-sm btn-link text-danger p-0 remove-course-btn" data-entry-id="${course.entry_id}" style="font-size:.8rem;text-decoration:none;">✖</button>
                    </div>`;
                }).join('');
            } else {
                unscheduledPanel.style.display = 'none';
                unscheduledList.innerHTML = '';
            }
        }

        initPopovers();
        initTooltips();
    };

    const updateTotalCredits = (courses) => {
        if (!totalCreditsBadge) return;
        const total = courses.reduce((acc, c) => {
            const v = parseFloat(c.Credits);
            return acc + (Number.isFinite(v) ? v : 0);
        }, 0);

        const from = parseFloat(totalCreditsBadge.getAttribute('data-value') || '0') || 0;
        const to = total;
        totalCreditsBadge.setAttribute('data-value', String(to));

        const start = performance.now();
        const duration = 220;
        const step = (now) => {
            const t = Math.min(1, (now - start) / duration);
            // easeOutCubic
            const eased = 1 - Math.pow(1 - t, 3);
            const val = from + (to - from) * eased;
            const shown = Number.isFinite(val) ? (Math.round(val * 10) / 10) : to;
            totalCreditsBadge.innerText = `Total Credits: ${shown}`;
            if (t < 1) requestAnimationFrame(step);
        };
        requestAnimationFrame(step);
    };

    const refreshScheduleCourses = async () => {
        if (!scheduleIdInput) return;
        const scheduleId = scheduleIdInput.value;
        try {
            const response = await fetch(`/student/api/schedule/${scheduleId}/courses`);
            const result = await response.json();
            if (!result.success) return;

            currentScheduleCourses = result.courses || [];
            if (coursesDataElement) coursesDataElement.textContent = JSON.stringify(currentScheduleCourses);
            renderGrid(currentScheduleCourses);
            updateTotalCredits(currentScheduleCourses);
            applyFiltersAndRender();
        } catch (e) {
            console.error('Failed to refresh schedule courses:', e);
        }
    };

    // ==========================================
    // 1. FETCH CATALOG & BUILD FILTERS
    // ==========================================
    const loadCatalog = async () => {
        if (!resultsContainer || !scheduleIdInput) return;
        const scheduleId = scheduleIdInput.value;

        try {
            const params = new URLSearchParams({
                query: '',
                schedule_id: String(scheduleId || ''),
                json: 'true',
            });
            if (levelScopeSelect && Number.isFinite(scheduleLevel) && scheduleLevel > 0) {
                params.set('level_scope', String(levelScopeSelect.value || 'my'));
            }
            const response = await fetch(`/student/courses/search?${params.toString()}`);
            allCatalogCourses = await response.json();

            prereqTitleLookup.clear();
            allCatalogCourses.forEach((course) => {
                const code = normalizePrereqToken(course?.Course_Number || course?.course_number || '');
                const title = getDisplayTitle(course);
                if (code && title) {
                    prereqTitleLookup.set(code, title);
                }
            });

            buildFilters();
            applyFiltersAndRender();
        } catch(e) {
            console.error("Error loading catalog:", e);
            resultsContainer.innerHTML = '<p class="text-danger text-center mt-4">Failed to load catalog.</p>';
        }
    };

    const buildFilters = () => {
        const subjects = new Set();
        const instructors = new Set();
        const credits = new Set();
        const periods = new Set();

        allCatalogCourses.forEach(c => {
            let subj = c.Area_of_Study;
            if (!subj || subj.trim() === "") {
                const match = getDisplayCourseNumber(c).match(/^[A-Za-z]+/);
                subj = match ? match[0].toUpperCase() : 'General';
            }
            c.ComputedSubject = subj.trim();
            subjects.add(c.ComputedSubject);

            const instructorName = cleanInstructorDisplay(c.Instructor);
            c.Instructor = instructorName;
            if (instructorName && instructorName !== 'TBA') instructors.add(instructorName);
            if (c.Credits !== null && c.Credits !== undefined) credits.add(String(c.Credits));
            
            if (c.Day_and_Period && c.Day_and_Period !== 'TBA') {
                const matches = c.Day_and_Period.match(/\d/g);
                if (matches) matches.forEach(m => periods.add(`Period ${m}`));
            }
        });

        const subjectList = document.getElementById('subjectList');
        if (subjectList) subjectList.innerHTML = Array.from(subjects).sort().map(s => `<option value="${s}">`).join('');

        const instructorList = document.getElementById('instructorList');
        if (instructorList) instructorList.innerHTML = Array.from(instructors).sort().map(i => `<option value="${i}">`).join('');

        generatePillHTML('creditsCheckboxContainer', 'filter-credits', Array.from(credits).sort((a,b)=>a-b));
        generatePillHTML('periodCheckboxContainer', 'filter-period', Array.from(periods).sort());

        document.querySelectorAll('.filter-checkbox').forEach(chk => chk.addEventListener('change', applyFiltersAndRender));
        if(filterSubjectInput) filterSubjectInput.addEventListener('input', applyFiltersAndRender);
        if(filterInstructorInput) filterInstructorInput.addEventListener('input', applyFiltersAndRender);
    };

    const generatePillHTML = (containerId, filterClass, values) => {
        const container = document.getElementById(containerId);
        if (!container) return;
        if (values.length === 0) {
            container.innerHTML = '<span class="small text-muted">No options</span>';
            return;
        }

        let html = '';
        values.forEach(val => {
            if(!val) return;
            const cleanId = val.replace(/[^a-zA-Z0-9]/g, '');
            html += `
                <input type="checkbox" class="btn-check filter-checkbox ${filterClass}" id="chk_${filterClass}_${cleanId}" value="${val}" autocomplete="off">
                <label class="btn btn-outline-primary rounded-pill btn-sm filter-pill me-1 mb-1 px-3" for="chk_${filterClass}_${cleanId}">${val.replace('Period ', 'P')}</label>
            `;
        });
        container.innerHTML = html;
    };

    // ==========================================
    // 2. APPLY FILTERS & RENDER MIDDLE COLUMN
    // ==========================================
    const applyFiltersAndRender = () => {
        const getCheckedValues = (className) => Array.from(document.querySelectorAll(`.${className}:checked`)).map(cb => cb.value);

        const searchQuery = searchInput ? searchInput.value.toLowerCase().trim() : '';
        const subjectQuery = filterSubjectInput ? filterSubjectInput.value.trim() : '';
        const instructorQuery = filterInstructorInput ? filterInstructorInput.value.trim() : '';
        
        const selectedCredits = getCheckedValues('filter-credits');
        const selectedPeriods = getCheckedValues('filter-period');

        const filteredCourses = allCatalogCourses.filter(c => {
            if (searchQuery) {
                const dispCode = getDisplayCourseNumber(c);
                const formattedCode = formatCourseCode(c.Course_Number || dispCode).toLowerCase();
                const title = getDisplayTitle(c);
                const titleMatch = title.toLowerCase().includes(searchQuery);
                const codeMatch = dispCode.toLowerCase().includes(searchQuery);
                const formatMatch = formattedCode.includes(searchQuery);
                if (!titleMatch && !codeMatch && !formatMatch) return false;
            }
            if (subjectQuery && c.ComputedSubject !== subjectQuery) return false;
            if (instructorQuery && (!c.Instructor || c.Instructor.trim() !== instructorQuery)) return false;
            if (selectedCredits.length > 0 && !selectedCredits.includes(String(c.Credits))) return false;
            if (selectedPeriods.length > 0) {
                if (!c.Day_and_Period || c.Day_and_Period === 'TBA') return false;
                const coursePeriods = (c.Day_and_Period.match(/\d/g) || []).map(m => `Period ${m}`);
                const hasMatchingPeriod = selectedPeriods.some(p => coursePeriods.includes(p));
                if (!hasMatchingPeriod) return false;
            }
            return true;
        });

        renderCourseList(filteredCourses);
    };

    const renderCourseList = (coursesToRender) => {
        if (!resultsContainer) return;
        if (resultCountBadge) resultCountBadge.innerText = coursesToRender.length;

        if (coursesToRender.length === 0) {
            resultsContainer.innerHTML = '<div class="alert alert-light text-center mt-4 border-0 text-muted small">No courses match your filters.</div>';
            return;
        }

        const groupedCourses = {};
        coursesToRender.forEach(c => {
            let baseTitle = getDisplayTitle(c).replace(/\s+Sec\s+\d+.*$/i, '').trim();
            if (!groupedCourses[baseTitle]) groupedCourses[baseTitle] = [];
            groupedCourses[baseTitle].push(c);
        });

        const currentCourses = coursesDataElement ? JSON.parse(coursesDataElement.textContent) : [];
        const periodTimeMap = getPeriodTimeMap();
        const occupiedWindows = [];
        currentCourses.forEach(c => {
            const windows = buildTimeWindows(c.Day_and_Period, periodTimeMap);
            windows.forEach((w) => {
                occupiedWindows.push({ ...w, title: getDisplayTitle(c) || 'Existing Course' });
            });
        });

        const findOverlapTitle = (candidateWindows) => {
            for (const window of candidateWindows) {
                for (const occupied of occupiedWindows) {
                    if (window.day !== occupied.day) continue;
                    if (window.start < occupied.end && occupied.start < window.end) {
                        return occupied.title;
                    }
                }
            }
            return '';
        };

        let html = '<div class="list-group list-group-flush">';
        for (const [baseTitle, sections] of Object.entries(groupedCourses)) {
            const sample = sections[0];
            const sectionsData = encodeURIComponent(JSON.stringify(sections));
            
            const baseCodeForCheck = normalizePrereqToken(sample.Course_Number || getDisplayCourseNumber(sample));
            const isObtained = obtainedCourseSet.has(baseCodeForCheck);
            let obtainedBadgeHtml = isObtained ? `<span class="badge bg-success bg-opacity-10 text-success border border-success-subtle rounded-pill ms-2">${heroIcon('check-circle')}Obtained</span>` : '';
            
            let prereqBadgeHtml = getPrereqBadgeHtml(sample.Prerequisite);
            let prereqItemsHtml = prereqInlineListHtml(sample.Prerequisite);

            let actionButton = '';
            let sectionBadge = '';
            const detailButton = `<button class="btn btn-sm btn-outline-secondary view-course-detail-btn rounded-pill px-3 py-1 shadow-sm fw-bold" style="font-size: 0.75rem;" data-course-number="${encodeURIComponent(sample.Course_Number || '')}" data-day-period="${encodeURIComponent(sample.Day_and_Period || '')}" data-schedule-id="${scheduleIdInput.value}">Details</button>`;

            if (sections.length > 1) {
                sectionBadge = `<span class="badge bg-primary bg-opacity-10 text-primary border border-primary-subtle rounded-pill">${heroIcon('stack')}${sections.length} Sections</span>`;
                actionButton = `<button class="btn btn-sm btn-primary view-sections-btn rounded-pill px-3 py-1 shadow-sm fw-bold" style="font-size: 0.75rem;" data-sections="${sectionsData}" data-schedule-id="${scheduleIdInput.value}">Options</button>`;
            } else {
                let isClash = false;
                let clashMessage = '';
                const candidateWindows = buildTimeWindows(sample.Day_and_Period, periodTimeMap);
                const overlapTitle = findOverlapTitle(candidateWindows);
                if (overlapTitle) {
                    isClash = true;
                    clashMessage = `Clash: ${overlapTitle}`;
                }
                if (currentCourses.some((c) => instanceIdentityKey(c) === instanceIdentityKey(sample))) {
                    isClash = true; clashMessage = "Added";
                }

                if (isClash) {
                    sectionBadge = `<span class="badge bg-danger bg-opacity-10 text-danger border border-danger-subtle rounded-pill">Conflict</span>`;
                    actionButton = `<button class="btn btn-sm btn-secondary rounded-pill px-2 py-1 shadow-sm fw-bold" style="font-size: 0.7rem;" disabled>${clashMessage}</button>`;
                } else {
                    const encodedSampleCourseNumber = encodeURIComponent(sample.Course_Number || '');
                    const encodedSampleDayPeriod = encodeURIComponent(sample.Day_and_Period || '');
                    const encodedSampleSemester = encodeURIComponent(sample.semester || sample.Semester || '');
                    const encodedSampleYear = encodeURIComponent(sample.year || sample.Year || '');
                    const encodedSampleDatasetName = encodeURIComponent(sample.dataset_name || sample.Dataset_Name || '');
                    const encodedSampleSource = encodeURIComponent(sample.source || sample.Source || '');
                    sectionBadge = `<span class="badge bg-secondary bg-opacity-10 text-secondary border rounded-pill">${heroIcon('clock')}${sample.Day_and_Period || 'TBA'}</span>`;
                    actionButton = `<button class="btn btn-sm btn-success add-specific-section-btn rounded-pill px-3 py-1 shadow-sm fw-bold" style="font-size: 0.75rem;" data-course-number="${encodedSampleCourseNumber}" data-day-period="${encodedSampleDayPeriod}" data-semester="${encodedSampleSemester}" data-year="${encodedSampleYear}" data-dataset-name="${encodedSampleDatasetName}" data-source="${encodedSampleSource}" data-schedule-id="${scheduleIdInput.value}">+ Add</button>`;
                }
            }

            html += `
                <div class="list-group-item px-2 py-3 border-bottom bg-transparent hover-shadow transition-all">
                    <div class="d-flex justify-content-between align-items-start mb-1">
                        <div>
                            <strong class="text-dark lh-sm" style="font-size: 0.9rem;">${baseTitle}</strong>
                            <div class="mt-1 d-flex flex-wrap gap-1">
                                ${obtainedBadgeHtml}
                                ${prereqBadgeHtml}
                            </div>
                            ${prereqItemsHtml}
                        </div>
                        <span class="badge bg-light text-dark border rounded-pill shadow-sm ms-2">${sample.Credits} CR</span>
                    </div>
                    <div class="d-flex justify-content-between align-items-center mt-2">
                        ${sectionBadge}
                        <div class="d-flex gap-1 align-items-center">${detailButton}${actionButton}</div>
                    </div>
                </div>
            `;
        }
        html += '</div>';
        resultsContainer.innerHTML = html;
        
        const tooltipTriggerList = [].slice.call(document.querySelectorAll('[title]'))
        tooltipTriggerList.map(function (tooltipTriggerEl) {
            return new bootstrap.Tooltip(tooltipTriggerEl)
        });
    };

    if (scheduleIdInput) loadCatalog();
    if (searchInput) searchInput.addEventListener('input', applyFiltersAndRender);
    if (levelScopeSelect) levelScopeSelect.addEventListener('change', loadCatalog);

    // Initial grid/list render (only on schedule editor page)
    if (scheduleIdInput && document.querySelectorAll('.schedule-cell').length) {
        renderGrid(currentScheduleCourses);
        updateTotalCredits(currentScheduleCourses);
    }

    if (exportSchedulePngBtn) {
        exportSchedulePngBtn.addEventListener('click', async () => {
            const gridRoot = document.querySelector('#grid-view .ax-grid-wrap');
            if (!gridRoot || typeof html2canvas !== 'function') {
                alert('PNG export is unavailable right now.');
                return;
            }

            exportSchedulePngBtn.disabled = true;
            const originalText = exportSchedulePngBtn.textContent;
            exportSchedulePngBtn.textContent = 'Exporting...';

            try {
                const canvas = await html2canvas(gridRoot, {
                    backgroundColor: '#ffffff',
                    scale: 2,
                    useCORS: true,
                    onclone: (doc) => {
                        doc.querySelectorAll('.remove-course-btn, .grade-chip, .badge').forEach((el) => el.remove());
                        const cloneRoot = doc.querySelector('#grid-view .ax-grid-wrap');
                        if (cloneRoot) {
                            cloneRoot.querySelectorAll('.ax-course strong').forEach((el) => {
                                el.style.opacity = '1';
                                el.style.fontWeight = '800';
                                el.style.color = '#ffffff';
                            });
                            cloneRoot.querySelectorAll('.ax-course span').forEach((el) => {
                                el.style.opacity = '1';
                                el.style.fontWeight = '700';
                                el.style.color = '#ffffff';
                            });
                        }
                    },
                });
                const link = document.createElement('a');
                const stamp = new Date().toISOString().slice(0, 10);
                link.download = `schedule-${stamp}.png`;
                link.href = canvas.toDataURL('image/png');
                link.click();
            } catch (err) {
                alert('Unable to export schedule right now.');
            } finally {
                exportSchedulePngBtn.disabled = false;
                exportSchedulePngBtn.textContent = originalText;
            }
        });
    }


    // ==========================================
    // 3. MODAL, GRADES, & ADD/REMOVE LISTENERS
    // ==========================================
    
    document.addEventListener('click', async (e) => {
        const gradeBtn = e.target.closest('.grade-chip');
        if (gradeBtn) {
            e.preventDefault();
            e.stopPropagation();
            if (!window.IS_AUTHENTICATED) return;

            const entryId = gradeBtn.getAttribute('data-entry-id');
            const selectedGrade = String(gradeBtn.getAttribute('data-grade') || '').toUpperCase();
            if (!entryId || !selectedGrade) return;

            const currentCourse = currentScheduleCourses.find((c) => String(c.entry_id) === String(entryId));
            const currentGrade = String((currentCourse && currentCourse.grade) || '').toUpperCase();
            const nextGrade = currentGrade === selectedGrade ? '' : selectedGrade;

            try {
                const response = await fetch('/student/api/update_grade', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ entry_id: entryId, grade: nextGrade })
                });
                const result = await response.json();
                if (!response.ok || !result.success) {
                    alert(`Failed to save grade: ${result.message || 'Unknown error'}`);
                    return;
                }

                currentScheduleCourses = currentScheduleCourses.map((course) => {
                    if (String(course.entry_id) === String(entryId)) {
                        return { ...course, grade: nextGrade };
                    }
                    return course;
                });
                if (coursesDataElement) coursesDataElement.textContent = JSON.stringify(currentScheduleCourses);
                renderGrid(currentScheduleCourses);
            } catch (err) {
                console.error(err);
            }
            return;
        }

        const detailBtn = e.target.closest('.view-course-detail-btn');
        if (detailBtn) {
            const scheduleId = detailBtn.getAttribute('data-schedule-id') || '';
            const courseNumber = decodeDataAttr(detailBtn.getAttribute('data-course-number'));
            const dayAndPeriod = decodeDataAttr(detailBtn.getAttribute('data-day-period'));
            openCourseDetail({ scheduleId, courseNumber, dayAndPeriod });
            return;
        }
        
        // OPEN SECTION MODAL
        if (e.target.classList.contains('view-sections-btn')) {
            const btn = e.target;
            const sections = JSON.parse(decodeURIComponent(btn.getAttribute('data-sections')));
            const scheduleId = btn.getAttribute('data-schedule-id');

            const baseTitle = getDisplayTitle(sections[0]).replace(/\s+Sec\s+\d+.*$/i, '').trim();
            document.getElementById('sectionModalTitle').innerText = baseTitle;

            const currentCourses = JSON.parse(document.getElementById('currentCoursesData').textContent);
            const periodTimeMap = getPeriodTimeMap();
            const occupiedWindows = [];
            currentCourses.forEach(c => {
                buildTimeWindows(c.Day_and_Period, periodTimeMap).forEach((w) => {
                    occupiedWindows.push({ ...w, title: getDisplayTitle(c) || 'Existing Course' });
                });
            });

            let bodyHtml = '';
            sections.forEach(sec => {
                let isClash = false;
                let clashMessage = '';

                const candidateWindows = buildTimeWindows(sec.Day_and_Period, periodTimeMap);
                for (const w of candidateWindows) {
                    const overlap = occupiedWindows.find((o) => o.day === w.day && w.start < o.end && o.start < w.end);
                    if (overlap) {
                        isClash = true;
                        clashMessage = `Overlap: ${overlap.title}`;
                        break;
                    }
                }

                if (currentCourses.some((c) => instanceIdentityKey(c) === instanceIdentityKey(sec))) {
                    isClash = true;
                    clashMessage = "Already Added";
                }

                const baseCodeForCheck = normalizePrereqToken(sec.Course_Number || getDisplayCourseNumber(sec));
                const isObtained = obtainedCourseSet.has(baseCodeForCheck);
                let obtainedBadgeHtml = isObtained ? `<span class="badge bg-success bg-opacity-10 text-success border border-success-subtle rounded-pill ms-2">${heroIcon('check-circle')}Obtained</span>` : '';
                let prereqBadgeHtml = getPrereqBadgeHtml(sec.Prerequisite);
                let prereqItemsHtml = prereqInlineListHtml(sec.Prerequisite);

                const disableAttr = isClash ? 'disabled' : '';
                const btnClass = isClash ? 'btn-secondary opacity-50' : 'btn-success';
                const btnText = isClash ? clashMessage : '+ Select';
                const rowClass = isClash ? 'bg-secondary bg-opacity-10' : '';
                const modalDisplayTitle = getDisplayTitle(sec) || 'Untitled Course';
                const encodedCourseNumber = encodeURIComponent(sec.Course_Number || '');
                const encodedDayPeriod = encodeURIComponent(sec.Day_and_Period || '');

                bodyHtml += `
                    <div class="list-group-item d-flex justify-content-between align-items-center p-3 ${rowClass}">
                        <div>
                            <div class="d-flex align-items-center flex-wrap gap-1 mb-1">
                                <strong class="text-dark fs-6">${modalDisplayTitle}</strong>
                                ${obtainedBadgeHtml}
                                ${prereqBadgeHtml}
                            </div>
                            ${prereqItemsHtml}
                            <small class="text-muted d-block fw-bold mb-2" style="font-size: 0.75rem;">👤 ${sec.Instructor || 'TBA'}</small>
                            <span class="badge ${isClash ? 'bg-secondary' : 'bg-info text-dark'} px-2 py-1 rounded-pill">${heroIcon('clock')}${sec.Day_and_Period || 'TBA'}</span>
                        </div>
                        <div class="d-flex flex-column align-items-end gap-2">
                            <button class="btn btn-outline-secondary btn-sm view-course-detail-btn fw-bold px-3 py-1 rounded-pill"
                                    data-course-number="${encodedCourseNumber}"
                                    data-day-period="${encodedDayPeriod}"
                                    data-schedule-id="${scheduleId}">
                                Details
                            </button>
                            <button class="btn ${btnClass} btn-sm add-specific-section-btn fw-bold px-4 py-2 rounded-pill shadow-sm"
                                    data-course-number="${encodedCourseNumber}"
                                    data-day-period="${encodedDayPeriod}"
                                    data-semester="${encodeURIComponent(sec.semester || sec.Semester || '')}"
                                    data-year="${encodeURIComponent(sec.year || sec.Year || '')}"
                                    data-dataset-name="${encodeURIComponent(sec.dataset_name || sec.Dataset_Name || '')}"
                                    data-source="${encodeURIComponent(sec.source || sec.Source || '')}"
                                    data-schedule-id="${scheduleId}"
                                    ${disableAttr}>
                                ${btnText}
                            </button>
                        </div>
                    </div>
                `;
            });

            document.getElementById('sectionModalBody').innerHTML = bodyHtml;
            const modal = new bootstrap.Modal(document.getElementById('sectionModal'));
            modal.show();
            
            const tooltipTriggerList = [].slice.call(document.querySelectorAll('#sectionModalBody [title]'))
            tooltipTriggerList.map(function (tooltipTriggerEl) { return new bootstrap.Tooltip(tooltipTriggerEl) });
        }

        // ADD CONFIRMED SECTION TO DB
        if (e.target.classList.contains('add-specific-section-btn')) {
            const btn = e.target;
            btn.disabled = true;
            btn.innerText = 'Adding...';

            try {
                const response = await fetch('/student/api/add_course', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        schedule_id: btn.getAttribute('data-schedule-id'),
                        course_number: decodeDataAttr(btn.getAttribute('data-course-number')),
                        day_period: decodeDataAttr(btn.getAttribute('data-day-period')),
                        semester: decodeDataAttr(btn.getAttribute('data-semester')),
                        year: decodeDataAttr(btn.getAttribute('data-year')),
                        dataset_name: decodeDataAttr(btn.getAttribute('data-dataset-name')),
                        source: decodeDataAttr(btn.getAttribute('data-source')),
                    })
                });
                const result = await response.json();
                if (result.success) {
                    const status = result.prerequisite_status || {};
                    if (status.warning_message) {
                        const alert = document.createElement('div');
                        alert.className = 'alert alert-warning alert-dismissible fade show position-fixed shadow-sm';
                        alert.style.top = '1rem';
                        alert.style.right = '1rem';
                        alert.style.zIndex = '1080';
                        alert.style.maxWidth = '420px';
                        alert.innerHTML = `<strong>Prerequisite warning:</strong> ${escapeHtml(status.warning_message)}<button type="button" class="btn-close" data-bs-dismiss="alert"></button>`;
                        document.body.appendChild(alert);
                        setTimeout(() => {
                            try { alert.remove(); } catch (_) {}
                        }, 5500);
                    }
                    try { localStorage.setItem('acadex-courses-updated', String(Date.now())); } catch (_) {}
                    await refreshScheduleCourses();
                }
                else { alert(result.message); btn.disabled = false; btn.innerText = '+ Add'; }
            } catch (err) {
                console.error(err);
                btn.disabled = false; btn.innerText = '+ Add';
            }
        }

        // REMOVE COURSE
        if (e.target.classList.contains('remove-course-btn')) {
            const entryId = e.target.getAttribute('data-entry-id');
            try {
                const response = await fetch(`/student/api/remove_course/${entryId}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                });
                const result = await response.json();
                if (result.success) {
                    try { localStorage.setItem('acadex-courses-updated', String(Date.now())); } catch (_) {}
                    await refreshScheduleCourses();
                }
            } catch (err) { console.error(err); }
        }
    });
});
