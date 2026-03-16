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

    // Load the student's historical obtained courses
    const obtainedCourses = obtainedCoursesElement ? JSON.parse(obtainedCoursesElement.textContent) : [];

    // Formatter Helpers
    const formatCourseCode = (rawCode) => rawCode.replace(/-S(\d+)-(Sp|Fa).*/i, ' Section $1').replace(/-V\d+/i, '');
    const formatShortCourseCode = (rawCode) => rawCode.replace(/-S(\d+)-(Sp|Fa).*/i, ' Sec $1').replace(/-V\d+/i, '');
    const getBaseCode = (rawCode) => rawCode.replace(/-S\d+-.*/i, '').trim().toUpperCase();

    // ==========================================
    // PREREQUISITE SCANNER ENGINE
    // ==========================================
    const getPrereqBadgeHtml = (prereqString) => {
        if (!prereqString || prereqString.trim() === '' || prereqString.trim().toLowerCase() === 'none') {
            return '';
        }
        
        const prereqText = prereqString.trim();
        const regex = /[A-Za-z]{2,4}[-\s]*\d{3,4}/g;
        const codesFound = prereqText.match(regex) || [];
        
        let missing = [];
        codesFound.forEach(code => {
            const cleanCode = code.replace(/[-\s]+/g, '').toUpperCase();
            if (!obtainedCourses.includes(cleanCode)) {
                missing.push(cleanCode);
            }
        });

        if (missing.length > 0) {
            return `<span class="badge bg-warning bg-opacity-10 text-warning border border-warning-subtle rounded-pill ms-2" title="Requires: ${prereqText}"><i class="bi bi-exclamation-triangle-fill"></i> Prereq: ${missing.join(', ')}</span>`;
        } else if (codesFound.length > 0) {
            return `<span class="badge bg-info bg-opacity-10 text-info border border-info-subtle rounded-pill ms-2" title="All requirements met!"><i class="bi bi-unlock-fill"></i> Prereqs Met</span>`;
        } else {
            return `<span class="badge bg-secondary bg-opacity-10 text-secondary border border-secondary-subtle rounded-pill ms-2" title="${prereqText}"><i class="bi bi-info-circle-fill"></i> Check Prereqs</span>`;
        }
    };

    // ==========================================
    // 1. FETCH CATALOG & BUILD FILTERS
    // ==========================================
    const loadCatalog = async () => {
        if (!resultsContainer || !scheduleIdInput) return;
        const scheduleId = scheduleIdInput.value;

        try {
            const response = await fetch(`/student/courses/search?query=&schedule_id=${scheduleId}&json=true`);
            allCatalogCourses = await response.json();

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
                const match = c.Course_Number.match(/^[A-Za-z]+/);
                subj = match ? match[0].toUpperCase() : 'General';
            }
            c.ComputedSubject = subj.trim();
            subjects.add(c.ComputedSubject);

            if (c.Instructor && c.Instructor.trim() !== "") instructors.add(c.Instructor.trim());
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
                const formattedCode = formatCourseCode(c.Course_Number).toLowerCase();
                const titleMatch = c.Course_Title.toLowerCase().includes(searchQuery);
                const codeMatch = c.Course_Number.toLowerCase().includes(searchQuery);
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
            let baseTitle = c.Course_Title.replace(/\s+Sec\s+\d+.*$/i, '').trim();
            if (!groupedCourses[baseTitle]) groupedCourses[baseTitle] = [];
            groupedCourses[baseTitle].push(c);
        });

        const currentCourses = coursesDataElement ? JSON.parse(coursesDataElement.textContent) : [];
        const occupiedBlocks = {};
        currentCourses.forEach(c => {
            if(c.Day_and_Period && c.Day_and_Period !== 'TBA') {
                c.Day_and_Period.trim().split(/[\s,]+/).forEach(b => { 
                    const cleanB = b.replace(/[^a-zA-Z0-9]/g, '');
                    if(cleanB) occupiedBlocks[cleanB] = c.Course_Number; 
                });
            }
        });

        let html = '<div class="list-group list-group-flush">';
        for (const [baseTitle, sections] of Object.entries(groupedCourses)) {
            const sample = sections[0];
            const sectionsData = encodeURIComponent(JSON.stringify(sections));
            
            const displayCode = formatCourseCode(sample.Course_Number.replace(/-\d+$/, '')); 
            
            const baseCodeForCheck = getBaseCode(sample.Course_Number);
            const isObtained = obtainedCourses.includes(baseCodeForCheck);
            let obtainedBadgeHtml = isObtained ? `<span class="badge bg-success bg-opacity-10 text-success border border-success-subtle rounded-pill ms-2"><i class="bi bi-check-circle-fill"></i> Obtained</span>` : '';
            
            let prereqBadgeHtml = getPrereqBadgeHtml(sample.Prerequisite);

            let actionButton = '';
            let sectionBadge = '';

            if (sections.length > 1) {
                sectionBadge = `<span class="badge bg-primary bg-opacity-10 text-primary border border-primary-subtle rounded-pill"><i class="bi bi-stack"></i> ${sections.length} Sections</span>`;
                actionButton = `<button class="btn btn-sm btn-primary view-sections-btn rounded-pill px-3 py-1 shadow-sm fw-bold" style="font-size: 0.75rem;" data-sections="${sectionsData}" data-schedule-id="${scheduleIdInput.value}">Options</button>`;
            } else {
                let isClash = false;
                let clashMessage = '';
                const secBlocks = sample.Day_and_Period && sample.Day_and_Period !== 'TBA' ? sample.Day_and_Period.trim().split(/[\s,]+/) : [];
                
                for (let b of secBlocks) {
                    const cleanB = b.replace(/[^a-zA-Z0-9]/g, '');
                    if (cleanB && occupiedBlocks[cleanB]) {
                        isClash = true; clashMessage = `Clash: ${formatShortCourseCode(occupiedBlocks[cleanB])}`; break;
                    }
                }
                if (currentCourses.some(c => c.Course_Number === sample.Course_Number)) {
                    isClash = true; clashMessage = "Added";
                }

                if (isClash) {
                    sectionBadge = `<span class="badge bg-danger bg-opacity-10 text-danger border border-danger-subtle rounded-pill">Conflict</span>`;
                    actionButton = `<button class="btn btn-sm btn-secondary rounded-pill px-2 py-1 shadow-sm fw-bold" style="font-size: 0.7rem;" disabled>${clashMessage}</button>`;
                } else {
                    sectionBadge = `<span class="badge bg-secondary bg-opacity-10 text-secondary border rounded-pill"><i class="bi bi-clock"></i> ${sample.Day_and_Period || 'TBA'}</span>`;
                    actionButton = `<button class="btn btn-sm btn-success add-specific-section-btn rounded-pill px-3 py-1 shadow-sm fw-bold" style="font-size: 0.75rem;" data-course-number="${sample.Course_Number}" data-schedule-id="${scheduleIdInput.value}">+ Add</button>`;
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
                        </div>
                        <span class="badge bg-light text-dark border rounded-pill shadow-sm ms-2">${sample.Credits} CR</span>
                    </div>
                    <div class="text-muted small tracking-widest mb-2 mt-1" style="font-size: 0.70rem;">${displayCode}</div>
                    <div class="d-flex justify-content-between align-items-center mt-2">
                        ${sectionBadge}
                        ${actionButton}
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


    // ==========================================
    // 3. MODAL, GRADES, & ADD/REMOVE LISTENERS
    // ==========================================
    
    // NEW: Listen for Grade Dropdown changes
    document.addEventListener('change', async (e) => {
        if (e.target.classList.contains('grade-select')) {
            const entryId = e.target.getAttribute('data-entry-id');
            const grade = e.target.value;

            try {
                const response = await fetch('/student/api/update_grade', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ entry_id: entryId, grade: grade })
                });
                const result = await response.json();
                
                if (result.success) {
                    // Flash green to show it saved securely
                    e.target.classList.add('is-valid');
                    setTimeout(() => e.target.classList.remove('is-valid'), 1500);
                } else {
                    alert("Failed to save grade: " + result.message);
                }
            } catch (err) {
                console.error(err);
            }
        }
    });

    document.addEventListener('click', async (e) => {
        
        // OPEN SECTION MODAL
        if (e.target.classList.contains('view-sections-btn')) {
            const btn = e.target;
            const sections = JSON.parse(decodeURIComponent(btn.getAttribute('data-sections')));
            const scheduleId = btn.getAttribute('data-schedule-id');

            const baseTitle = sections[0].Course_Title.replace(/\s+Sec\s+\d+.*$/i, '').trim();
            document.getElementById('sectionModalTitle').innerText = baseTitle;

            const currentCourses = JSON.parse(document.getElementById('currentCoursesData').textContent);
            const occupiedBlocks = {};
            currentCourses.forEach(c => {
                if(c.Day_and_Period && c.Day_and_Period !== 'TBA') {
                    c.Day_and_Period.trim().split(/[\s,]+/).forEach(b => { 
                        const cleanB = b.replace(/[^a-zA-Z0-9]/g, '');
                        if(cleanB) occupiedBlocks[cleanB] = c.Course_Number; 
                    });
                }
            });

            let bodyHtml = '';
            sections.forEach(sec => {
                let isClash = false;
                let clashMessage = '';
                
                const secBlocks = sec.Day_and_Period && sec.Day_and_Period !== 'TBA' ? sec.Day_and_Period.trim().split(/[\s,]+/) : [];
                for (let b of secBlocks) {
                    const cleanB = b.replace(/[^a-zA-Z0-9]/g, '');
                    if (cleanB && occupiedBlocks[cleanB]) {
                        isClash = true;
                        clashMessage = `Overlap: ${formatShortCourseCode(occupiedBlocks[cleanB])}`;
                        break;
                    }
                }

                if (currentCourses.some(c => c.Course_Number === sec.Course_Number)) {
                    isClash = true;
                    clashMessage = "Already Added";
                }

                const baseCodeForCheck = getBaseCode(sec.Course_Number);
                const isObtained = obtainedCourses.includes(baseCodeForCheck);
                let obtainedBadgeHtml = isObtained ? `<span class="badge bg-success bg-opacity-10 text-success border border-success-subtle rounded-pill ms-2"><i class="bi bi-check-circle-fill"></i> Obtained</span>` : '';
                let prereqBadgeHtml = getPrereqBadgeHtml(sec.Prerequisite);

                const disableAttr = isClash ? 'disabled' : '';
                const btnClass = isClash ? 'btn-secondary opacity-50' : 'btn-success';
                const btnText = isClash ? clashMessage : '+ Select';
                const rowClass = isClash ? 'bg-secondary bg-opacity-10' : '';
                const modalDisplayCode = formatCourseCode(sec.Course_Number);

                bodyHtml += `
                    <div class="list-group-item d-flex justify-content-between align-items-center p-3 ${rowClass}">
                        <div>
                            <div class="d-flex align-items-center flex-wrap gap-1 mb-1">
                                <strong class="text-dark fs-6">${modalDisplayCode}</strong>
                                ${obtainedBadgeHtml}
                                ${prereqBadgeHtml}
                            </div>
                            <small class="text-muted d-block fw-bold mb-2" style="font-size: 0.75rem;">👤 ${sec.Instructor || 'TBA'}</small>
                            <span class="badge ${isClash ? 'bg-secondary' : 'bg-info text-dark'} px-2 py-1 rounded-pill"><i class="bi bi-clock"></i> ${sec.Day_and_Period || 'TBA'}</span>
                        </div>
                        <button class="btn ${btnClass} btn-sm add-specific-section-btn fw-bold px-4 py-2 rounded-pill shadow-sm"
                                data-course-number="${sec.Course_Number}"
                                data-schedule-id="${scheduleId}"
                                ${disableAttr}>
                            ${btnText}
                        </button>
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
                        course_number: btn.getAttribute('data-course-number')
                    })
                });
                const result = await response.json();
                if (result.success) window.location.reload();
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
                if (result.success) window.location.reload();
            } catch (err) { console.error(err); }
        }
    });
});