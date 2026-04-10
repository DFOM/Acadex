// static/js/admin.js

$(document).ready(function() {

    // ==========================================
    // SIDEBAR TOGGLE
    // ==========================================
    $('#sidebarToggle').on('click', function() {
        document.body.classList.toggle('admin-sidebar-collapsed');
    });

    // ==========================================
    // 0. SMART DATATABLES WITH DROPDOWN FILTERS
    // ==========================================
    $('.smart-table').each(function() {
        var table = $(this);

        // Skip DataTables init if placeholder colspan rows exist (prevents incorrect column count errors).
        if (table.find('tbody td[colspan]').length > 0) {
            return;
        }

        // 1. Clone the header row to create an input filter row
        var thead = table.find('thead');
        var filterRow = $('<tr class="filter-row"></tr>').appendTo(thead);

        table.find('thead tr:first th').each(function() {
            var title = $(this).text().trim();
            // Do not add a search box to the 'Actions' column or empty columns
            if(title === 'Actions' || title === '') {
                filterRow.append('<th></th>');
            } else {
                // Add a blank dropdown select menu under each column
                filterRow.append('<th><select class="form-select form-select-sm"><option value="">All</option></select></th>');
            }
        });

        // 2. Initialize DataTable
        var dt = table.DataTable({
            "pageLength": 25,
            "orderCellsTop": true, 
            "lengthMenu": [10, 25, 50, 100],
            "language": {
                "search": "Global Search:"
            },
            initComplete: function () {
                // 3. Populate the dropdowns with unique data from each column
                this.api().columns().every(function (index) {
                    var column = this;
                    var select = $(column.header()).closest('thead').find('.filter-row th').eq(index).find('select');
                    
                    if(select.length) {
                        column.data().unique().sort().each(function (d, j) {
                            if(d) {
                                // Strip out any HTML tags (like badges) to get clean text
                                var cleanText = $('<div>').html(d).text().trim();
                                // Ensure we don't add duplicate empty strings
                                if(cleanText && select.find('option[value="' + cleanText + '"]').length === 0){
                                     select.append('<option value="' + cleanText + '">' + cleanText + '</option>');
                                }
                            }
                        });

                        // 4. Trigger the table search when a dropdown option is clicked
                        select.on('change', function () {
                            var val = $.fn.dataTable.util.escapeRegex($(this).val());
                            // Exact match regex so "Teacher A" doesn't trigger on "Teacher AB"
                            column.search(val ? '^' + val + '$' : '', true, false).draw();
                        });
                    }
                });
            }
        });
    });

    // ==========================================
    // 1. GLOBAL MODAL HANDLING
    // ==========================================
    const closeAllModals = () => {
        $('.modal').hide();
    };

    $('.close-modal, .modal-cancel-btn').on('click', function(e) {
        e.preventDefault();
        closeAllModals();
    });

    $(window).on('click', function(e) {
        if ($(e.target).hasClass('modal')) {
            closeAllModals();
        }
    });

    // ==========================================
    // 2. USER MANAGEMENT (/admin/users)
    // ==========================================
    $(document).on('click', '.delete-user-btn', async function(e) {
        const userId = $(this).data('id');
        if (!confirm("⚠️ WARNING: Are you sure you want to completely delete this user? This cannot be undone.")) return;

        try {
            const response = await fetch(`/admin/api/delete_user/${userId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            const result = await response.json();
            
            if (result.success) {
                window.location.reload();
            } else {
                alert("Error deleting user: " + result.message);
            }
        } catch (err) {
            console.error(err);
            alert("A network error occurred.");
        }
    });

    $(document).on('click', '.edit-user-btn', function() {
        $('#edit_user_id').val($(this).data('id'));
        $('#edit_user_gender').val($(this).data('gender'));
        $('#edit_user_year').val($(this).data('year'));
        $('#editUserModal').show();
    });

    $('#editUserForm').on('submit', async function(e) {
        e.preventDefault();
        const payload = {
            id: $('#edit_user_id').val(),
            gender: $('#edit_user_gender').val(),
            expected_graduation: $('#edit_user_year').val()
        };

        try {
            const response = await fetch('/admin/api/update_user_demographics', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const result = await response.json();
            if (result.success) window.location.reload();
        } catch (err) { console.error(err); }
    });

    const jsonFetch = async (url, options = {}) => {
        const response = await fetch(url, {
            headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
            ...options,
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || payload.success === false) {
            throw new Error(payload.message || 'Request failed.');
        }
        return payload;
    };

    const escapeHtml = (value) => {
        return String(value ?? '')
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#39;');
    };

    const filterSelectOptions = (selector, query) => {
        const q = (query || '').toString().trim().toLowerCase();
        $(selector).find('option').each(function() {
            const text = ($(this).text() || '').toLowerCase();
            $(this).toggle(!q || text.includes(q));
        });
    };

    // ==========================================
    // 3. COURSES MANAGEMENT (/admin/courses)
    // ==========================================
    const coursesApp = $('#coursesAdminApp');
    if (coursesApp.length) {
        const state = { courses: [], faculty: [], advisorAreas: [] };

        const collectCoursePayload = () => ({
            course_number: $('#course_course_number').val().trim(),
            title: $('#course_title').val().trim(),
            credits: $('#course_credits').val(),
            day_period: $('#course_day_period').val().trim(),
            semester: $('#course_semester').val().trim(),
            year: $('#course_year').val().trim(),
            prerequisite: $('#course_prerequisite').val().trim(),
            subject_area: $('#course_subject_area').val().trim(),
            faculty: $('#course_faculty_text').val().trim(),
            faculty_ids: ($('#course_faculty_ids').val() || []).map(v => Number(v)),
        });

        const setCourseError = (message) => {
            const box = $('#courseModalError');
            if (message) {
                box.text(message).removeClass('d-none');
            } else {
                box.addClass('d-none').text('');
            }
        };

        const setCourseFormValues = (row = null) => {
            $('#course_ref').val(row?.ref || '');
            $('#course_course_number').val(row?.course_number || '');
            $('#course_title').val(row?.title || '');
            $('#course_credits').val(row?.credits ?? '');
            $('#course_day_period').val(row?.day_period || '');
            $('#course_semester').val(row?.semester || '');
            $('#course_year').val(row?.year || '');
            $('#course_prerequisite').val(row?.prerequisite || '');
            $('#course_subject_area').val(row?.subject_area || '');
            const names = Array.isArray(row?.faculty_names) ? row.faculty_names : [];
            $('#course_faculty_text').val(row?.faculty || names.join('; ') || '');
            $('#course_faculty_ids').val((row?.faculty_ids || []).map(v => String(v)));
        };

        const renderCourseOptions = () => {
            const options = state.faculty
                .map(row => `<option value="${row.id}">${escapeHtml(row.name)}</option>`)
                .join('');
            $('#course_faculty_ids').html(options);

            const facultyNames = Array.from(
                new Set([
                    ...state.faculty.map(f => (f.name || '').trim()).filter(Boolean),
                    ...state.courses.flatMap((c) => (Array.isArray(c.faculty_names) ? c.faculty_names : [])).map(v => (v || '').trim()).filter(Boolean),
                ])
            ).sort();

            $('#courseFacultySuggestions').html(
                facultyNames.map(name => `<option value="${escapeHtml(name)}"></option>`).join('')
            );
        };

        const renderFacultyCell = (row) => {
            const names = (row.faculty_names || []).filter(Boolean);
            if (!names.length) {
                const fallback = (row.faculty || '').trim();
                return fallback ? escapeHtml(fallback) : '—';
            }
            if (names.length <= 2) {
                return escapeHtml(names.join(', '));
            }
            const compact = `${names[0]}, ${names[1]} +${names.length - 2}`;
            const tooltip = escapeHtml(names.join(', '));
            return `<span title="${tooltip}">${escapeHtml(compact)}</span>`;
        };

        const renderCourses = () => {
            const tbody = $('#coursesTableBody');
            const sourceFilter = ($('#courseSourceFilter').val() || '').trim().toLowerCase();
            const semesterFilter = ($('#courseSemesterFilter').val() || '').trim().toLowerCase();
            const yearFilter = ($('#courseYearFilter').val() || '').trim().toLowerCase();

            const filteredCourses = (state.courses || []).filter((row) => {
                const rowSource = (row.source || '').toLowerCase();
                const rowSemester = (row.semester || '').toLowerCase();
                const rowYear = String(row.year || '').toLowerCase();
                if (sourceFilter && rowSource !== sourceFilter) return false;
                if (semesterFilter && !rowSemester.includes(semesterFilter)) return false;
                if (yearFilter && !rowYear.includes(yearFilter)) return false;
                return true;
            });

            if (!filteredCourses.length) {
                tbody.html('<tr><td colspan="8" class="text-center text-muted py-4">No courses found.</td></tr>');
                return;
            }

            const rows = filteredCourses.map((row) => {
                let sourceBadge = '<span class="badge bg-secondary">scraped</span>';
                if ((row.source || '').toLowerCase() === 'manual') {
                    sourceBadge = '<span class="badge bg-success">manual</span>';
                } else if ((row.source || '').toLowerCase() === 'csv') {
                    sourceBadge = '<span class="badge bg-info text-dark">csv</span>';
                }
                const deleteBtn = row.source === 'manual'
                    ? `<button class="btn btn-sm btn-outline-danger ms-1 delete-course-btn" data-ref="${escapeHtml(row.ref)}">Delete</button>`
                    : '';
                return `
                    <tr>
                        <td>${escapeHtml(row.course_number || '')}</td>
                        <td>${escapeHtml(row.title || '')}</td>
                        <td>${escapeHtml(row.credits ?? '')}</td>
                        <td>${escapeHtml(row.day_period || '')}</td>
                        <td>${renderFacultyCell(row)}</td>
                        <td>${escapeHtml(row.subject_area || '')}</td>
                        <td>${sourceBadge}</td>
                        <td>
                            <button class="btn btn-sm btn-primary edit-course-btn" data-ref="${escapeHtml(row.ref)}">Edit</button>
                            <button class="btn btn-sm btn-outline-secondary ms-1 duplicate-course-btn" data-ref="${escapeHtml(row.ref)}">Duplicate</button>
                            ${deleteBtn}
                        </td>
                    </tr>
                `;
            });
            tbody.html(rows.join(''));
        };

        const loadCourses = async () => {
            const payload = await jsonFetch('/admin/api/courses');
            state.courses = payload.courses || [];
            state.faculty = payload.faculty || [];
            state.advisorAreas = payload.advisor_areas || [];
            renderCourseOptions();
            renderCourses();
        };

        $('#courseSourceFilter, #courseSemesterFilter, #courseYearFilter').on('change input', function() {
            renderCourses();
        });

        $('#addCourseBtn').on('click', function() {
            $('#courseModalTitle').text('Add Course');
            setCourseError('');
            setCourseFormValues(null);
            $('#courseModal').show();
        });

        $(document).on('click', '.edit-course-btn', function() {
            const ref = $(this).data('ref');
            const row = state.courses.find(c => c.ref === ref);
            if (!row) return;
            $('#courseModalTitle').text(`Edit Course (${row.source})`);
            setCourseError('');
            setCourseFormValues(row);
            $('#courseModal').show();
        });

        $(document).on('click', '.duplicate-course-btn', async function() {
            const ref = $(this).data('ref');
            try {
                const payload = await jsonFetch(`/admin/api/courses/${encodeURIComponent(ref)}/duplicate`, { method: 'POST' });
                $('#courseModalTitle').text('Duplicate Course');
                setCourseError('');
                setCourseFormValues({ ...payload.course, ref: '' });
                $('#courseModal').show();
            } catch (err) {
                alert(err.message || 'Failed to duplicate course.');
            }
        });

        $(document).on('click', '.delete-course-btn', async function() {
            const ref = $(this).data('ref');
            if (!confirm('Delete this manual course?')) return;
            try {
                await jsonFetch(`/admin/api/courses/${encodeURIComponent(ref)}`, { method: 'DELETE' });
                await loadCourses();
            } catch (err) {
                alert(err.message || 'Failed to delete course.');
            }
        });

        $('#courseModalForm').on('submit', async function(e) {
            e.preventDefault();
            const ref = ($('#course_ref').val() || '').trim();
            const payload = collectCoursePayload();
            setCourseError('');

            try {
                if (ref) {
                    await jsonFetch(`/admin/api/courses/${encodeURIComponent(ref)}`, {
                        method: 'PUT',
                        body: JSON.stringify(payload),
                    });
                } else {
                    await jsonFetch('/admin/api/courses', {
                        method: 'POST',
                        body: JSON.stringify(payload),
                    });
                }
                $('#courseModal').hide();
                await loadCourses();
            } catch (err) {
                setCourseError(err.message || 'Unable to save course.');
            }
        });

        loadCourses().catch((err) => {
            $('#coursesTableBody').html(`<tr><td colspan="8" class="text-danger text-center py-4">${escapeHtml(err.message || 'Unable to load courses.')}</td></tr>`);
        });
    }

    // ==========================================
    // 4. FACULTY MANAGEMENT (/admin/faculty)
    // ==========================================
    const facultyApp = $('#facultyAdminApp');
    if (facultyApp.length) {
        const state = { faculty: [], courses: [], advisorAreas: [] };

        const setFacultyError = (message) => {
            const box = $('#facultyModalError');
            if (message) {
                box.text(message).removeClass('d-none');
            } else {
                box.addClass('d-none').text('');
            }
        };

        const collectFacultyPayload = () => ({
            name: $('#faculty_name').val().trim(),
            email: $('#faculty_email').val().trim(),
            course_refs: $('#faculty_course_refs').val() || [],
            advisor_area_ids: ($('#faculty_advisor_area_ids').val() || []).map(v => Number(v)),
        });

        const renderFacultyOptions = () => {
            $('#faculty_course_refs').html(
                (state.courses || []).map((row) => (
                    `<option value="${escapeHtml(row.ref)}">${escapeHtml(`${row.course_number || ''} — ${row.title || ''} (${row.day_period || ''})`)}</option>`
                )).join('')
            );

            $('#faculty_advisor_area_ids').html(
                (state.advisorAreas || []).map((row) => (
                    `<option value="${row.id}">${escapeHtml(row.name)}</option>`
                )).join('')
            );
        };

        const setFacultyFormValues = (row = null) => {
            $('#faculty_id').val(row?.id || '');
            $('#faculty_name').val(row?.name || '');
            $('#faculty_email').val(row?.email || '');
            $('#faculty_course_refs').val(row?.course_refs || []);
            $('#faculty_advisor_area_ids').val((row?.advisor_area_ids || []).map(v => String(v)));
            $('#faculty_course_search').val('');
            $('#faculty_area_search').val('');
        };

        const renderFacultyRows = () => {
            const tbody = $('#facultyTableBody');
            if (!state.faculty.length) {
                tbody.html('<tr><td colspan="5" class="text-center text-muted py-4">No faculty found.</td></tr>');
                return;
            }

            tbody.html(
                state.faculty.map((row) => {
                    const nameHtml = row.is_placeholder
                        ? `<span class="badge bg-warning text-dark me-1" title="Placeholder — courses can be reassigned">Placeholder</span> ${escapeHtml(row.name || '')}`
                        : row.may_have_left
                        ? `<span style="opacity:.5;font-style:italic;">${escapeHtml(row.name || '')}</span> <span class="badge bg-secondary" style="font-size:.65rem;" title="No courses in current data">May have left</span>`
                        : escapeHtml(row.name || '');
                    const reassignBtn = row.is_placeholder && (row.assigned_courses_count || 0) > 0
                        ? ` <button class="btn btn-sm btn-outline-warning ms-1 reassign-faculty-btn" data-id="${row.id}" title="Reassign courses to a real faculty member">Reassign</button>`
                        : '';
                    return `<tr>
                        <td>${nameHtml}</td>
                        <td>${escapeHtml(row.email || '—')}</td>
                        <td>${escapeHtml((row.advisor_area_names || []).join(', ') || '—')}</td>
                        <td>${(function(){
                            const labels = row.course_labels || [];
                            const statuses = row.course_status || [];
                            const transfers = row.course_transferred || [];
                            if(!labels.length) return '—';
                            return labels.map((lbl, i) => {
                                const st = statuses[i] || 'active';
                                if(st === 'transferred'){
                                    const t = transfers[i];
                                    const tip = t ? `Now taught by ${t.faculty_name}` : 'Transferred';
                                    return `<span style="color:#f59e0b;font-style:italic;" title="${escapeHtml(tip)}">${escapeHtml(lbl)} ↗</span>`;
                                } else if(st === 'inactive'){
                                    return `<span style="opacity:.45;font-style:italic;text-decoration:line-through" title="No longer offered">${escapeHtml(lbl)}</span>`;
                                }
                                return escapeHtml(lbl);
                            }).join(', ');
                        })()}</td>
                        <td>
                            <button class="btn btn-sm btn-primary edit-faculty-btn" data-id="${row.id}">Edit</button>
                            <button class="btn btn-sm btn-outline-danger ms-1 delete-faculty-btn" data-id="${row.id}">Delete</button>${reassignBtn}
                        </td>
                    </tr>`;
                }).join('')
            );
        };

        const loadFaculty = async () => {
            const payload = await jsonFetch('/admin/api/faculty');
            state.faculty = payload.faculty || [];
            state.courses = payload.courses || [];
            state.advisorAreas = payload.advisor_areas || [];
            renderFacultyOptions();
            renderFacultyRows();
        };

        $('#faculty_course_search').on('input', function() {
            filterSelectOptions('#faculty_course_refs', $(this).val());
        });
        $('#faculty_area_search').on('input', function() {
            filterSelectOptions('#faculty_advisor_area_ids', $(this).val());
        });

        $('#addFacultyBtn').on('click', function() {
            $('#facultyModalTitle').text('Add Faculty');
            setFacultyError('');
            setFacultyFormValues(null);
            $('#facultyModal').show();
        });

        $(document).on('click', '.edit-faculty-btn', function() {
            const id = Number($(this).data('id'));
            const row = state.faculty.find(item => item.id === id);
            if (!row) return;
            $('#facultyModalTitle').text('Edit Faculty');
            setFacultyError('');
            setFacultyFormValues(row);
            $('#facultyModal').show();
        });

        $(document).on('click', '.delete-faculty-btn', async function() {
            const id = Number($(this).data('id'));
            if (!confirm('Delete this faculty member?')) return;
            try {
                await jsonFetch(`/admin/api/faculty/${id}`, { method: 'DELETE' });
                await loadFaculty();
            } catch (err) {
                alert(err.message || 'Failed to delete faculty.');
            }
        });

        $('#facultyModalForm').on('submit', async function(e) {
            e.preventDefault();
            const id = ($('#faculty_id').val() || '').trim();
            const payload = collectFacultyPayload();
            setFacultyError('');
            try {
                if (id) {
                    await jsonFetch(`/admin/api/faculty/${id}`, {
                        method: 'PUT',
                        body: JSON.stringify(payload),
                    });
                } else {
                    await jsonFetch('/admin/api/faculty', {
                        method: 'POST',
                        body: JSON.stringify(payload),
                    });
                }
                $('#facultyModal').hide();
                await loadFaculty();
            } catch (err) {
                setFacultyError(err.message || 'Unable to save faculty.');
            }
        });

        /* ── Reassign Modal Logic ── */
        $(document).on('click', '.close-reassign-modal', function() { $('#reassignModal').hide(); });
        $(document).on('click', '#reassignModal', function(e) { if (e.target === this) $(this).hide(); });

        $(document).on('click', '.reassign-faculty-btn', function() {
            const id = Number($(this).data('id'));
            const row = state.faculty.find(r => r.id === id);
            if (!row) return;

            $('#reassign_source_id').val(id);
            $('#reassignHint').text(`Reassign courses from "${row.name}" to another faculty member.`);
            $('#reassignError').addClass('d-none').text('');

            // Build course checkboxes
            const courseRefs = row.assigned_course_refs || [];
            const courseLabels = row.assigned_course_labels || [];
            let listHtml = '';
            const allCourses = row.courses || [];
            // Use refs for IDs and labels for display
            if (courseRefs.length) {
                courseRefs.forEach((ref, i) => {
                    // Extract ID from ref like "csv:123"
                    const courseId = ref.split(':').pop();
                    const label = courseLabels[i] || ref;
                    listHtml += `<div class="form-check"><input class="form-check-input reassign-course-cb" type="checkbox" value="${courseId}" id="rc_${courseId}" checked><label class="form-check-label small" for="rc_${courseId}">${escapeHtml(label)}</label></div>`;
                });
            } else {
                listHtml = '<span class="text-muted small">No courses linked.</span>';
            }
            $('#reassignCourseList').html(listHtml);

            // Populate target dropdown with all non-placeholder faculty
            const targetSelect = $('#reassign_target_id');
            targetSelect.html('<option value="">— Select existing faculty —</option>');
            state.faculty.filter(f => !f.is_placeholder && f.id !== id).forEach(f => {
                targetSelect.append(`<option value="${f.id}">${escapeHtml(f.name)}</option>`);
            });
            $('#reassign_new_name').val('');

            $('#reassignModal').show();
        });

        $('#reassignSubmitBtn').on('click', async function() {
            const sourceId = $('#reassign_source_id').val();
            const targetId = $('#reassign_target_id').val();
            const newName = ($('#reassign_new_name').val() || '').trim();
            const deleteEmpty = $('#reassign_delete_empty').is(':checked');

            if (!targetId && !newName) {
                $('#reassignError').removeClass('d-none').text('Select an existing faculty member or enter a new name.');
                return;
            }

            const courseIds = [];
            $('.reassign-course-cb:checked').each(function() { courseIds.push(Number($(this).val())); });
            if (!courseIds.length) {
                $('#reassignError').removeClass('d-none').text('Select at least one course to reassign.');
                return;
            }

            const payload = { course_ids: courseIds, delete_empty: deleteEmpty };
            if (targetId) payload.target_faculty_id = Number(targetId);
            else payload.target_faculty_name = newName;

            try {
                await jsonFetch(`/admin/api/faculty/${sourceId}/reassign`, {
                    method: 'POST',
                    body: JSON.stringify(payload),
                });
                $('#reassignModal').hide();
                await loadFaculty();
            } catch (err) {
                $('#reassignError').removeClass('d-none').text(err.message || 'Failed to reassign courses.');
            }
        });

        loadFaculty().catch((err) => {
            $('#facultyTableBody').html(`<tr><td colspan="5" class="text-danger text-center py-4">${escapeHtml(err.message || 'Unable to load faculty.')}</td></tr>`);
        });
    }
});