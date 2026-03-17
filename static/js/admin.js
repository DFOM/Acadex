// static/js/admin.js

$(document).ready(function() {

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

    // ==========================================
    // 3. INSTRUCTOR MANAGEMENT (/admin/instructors)
    // ==========================================
    $(document).on('click', '.edit-instructor-btn', function() {
        $('#edit_instructor_id').val($(this).data('id'));
        $('#edit_instructor_old_name').val($(this).data('name'));
        $('#edit_instructor_current_name').val($(this).data('name'));
        $('#edit_instructor_name').val($(this).data('name'));
        $('#editInstructorModal').show();
    });

    $('#editInstructorForm').on('submit', async function(e) {
        e.preventDefault();
        const id = $('#edit_instructor_id').val();
        const payload = {
            old_name: $('#edit_instructor_old_name').val(),
            name: $('#edit_instructor_name').val()
        };

        try {
            const response = await fetch(`/admin/api/update_instructor/${id}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const result = await response.json();
            if (result.success) window.location.reload();
            else alert(result.message || 'Failed to update instructor.');
        } catch (err) { console.error(err); }
    });

    $(document).on('click', '.delete-instructor-btn', function() {
        const name = ($(this).data('name') || '').toString();
        $('#delete_instructor_name').val(name);
        $('#delete_instructor_name_label').text(name);
        $('#delete_instructor_reassign_to').val('');
        $('#deleteInstructorModal').show();
    });

    $('#deleteInstructorForm').on('submit', async function(e) {
        e.preventDefault();
        const payload = {
            name: $('#delete_instructor_name').val(),
            reassign_to: $('#delete_instructor_reassign_to').val()
        };
        try {
            const response = await fetch('/admin/api/instructors/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const result = await response.json();
            if (result.success) {
                window.location.reload();
            } else {
                alert(result.message || 'Failed to delete instructor.');
            }
        } catch (err) {
            console.error(err);
            alert('A network error occurred.');
        }
    });

    // ==========================================
    // 4. COURSE MANAGEMENT (/admin/courses)
    // ==========================================
    $(document).on('click', '.edit-course-btn', function() {
        $('#edit_course_rowid').val($(this).data('rowid'));
        $('#edit_course_table').val($(this).data('table'));
        $('#edit_course_title').val($(this).data('title'));
        $('#edit_course_credits').val($(this).data('credits'));
        $('#editCourseModal').show();
    });

    $('#editCourseForm').on('submit', async function(e) {
        e.preventDefault();
        const payload = {
            _rowid: $('#edit_course_rowid').val(),
            source_table: $('#edit_course_table').val(),
            Course_Title: $('#edit_course_title').val(),
            Credits: $('#edit_course_credits').val()
        };

        try {
            const response = await fetch('/admin/api/update_course', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const result = await response.json();
            if (result.success) window.location.reload();
        } catch (err) { console.error(err); }
    });
});