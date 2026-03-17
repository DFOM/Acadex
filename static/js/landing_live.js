(function () {
  const grid = document.getElementById('axAnnouncementsGrid');
  const empty = document.getElementById('axAnnouncementsEmpty');
  const markSeenBtn = document.getElementById('axMarkSeenBtn');

  if (!grid) return;

  const seenIds = new Set();
  for (const el of grid.querySelectorAll('[data-ann-id]')) {
    const id = Number(el.getAttribute('data-ann-id'));
    if (!Number.isNaN(id)) seenIds.add(id);
  }

  const typeBadge = (annType) => {
    const t = String(annType || 'info').toLowerCase();
    if (t === 'warning') return '<span class="badge bg-warning text-dark">Warning</span>';
    if (t === 'update') return '<span class="badge bg-success">Update</span>';
    return '<span class="badge bg-primary">Info</span>';
  };

  const fmtDate = (iso) => {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      return d.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' });
    } catch (_) {
      return '';
    }
  };

  const escapeHtml = (s) => {
    return String(s || '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  };

  const renderAnnouncementCard = (a, isFresh) => {
    const title = escapeHtml(a.title);
    const content = escapeHtml(a.content).replaceAll('\n', '<br>');
    const posted = fmtDate(a.date_posted);
    const highlight = a.is_new ? ' border-primary border-opacity-50' : ' border-secondary border-opacity-25';
    const newPill = a.is_new ? '<span class="badge bg-primary bg-opacity-10 text-primary border border-primary">New</span>' : '';

    return `
      <div class="col-md-6 col-lg-4 ${isFresh ? 'ax-fade-in' : ''}" data-ann-id="${a.id}">
        <div class="card h-100 border ${highlight} shadow-sm">
          <div class="card-body">
            <div class="d-flex justify-content-between align-items-start gap-2 mb-2">
              <h5 class="card-title fw-bold text-primary mb-0">${title}</h5>
              <div class="d-flex align-items-center gap-2 flex-shrink-0">
                ${newPill}
                ${typeBadge(a.ann_type)}
              </div>
            </div>
            <p class="card-text text-secondary" style="white-space: pre-line;">${content}</p>
          </div>
          <div class="card-footer border-0 pt-0" style="background: var(--ax-surface);">
            <small class="text-muted">Posted ${escapeHtml(posted)}</small>
          </div>
        </div>
      </div>
    `;
  };

  const setEmpty = (isEmpty) => {
    if (!empty) return;
    empty.style.display = isEmpty ? '' : 'none';
  };

  const refresh = async () => {
    try {
      const res = await fetch('/api/landing/announcements?limit=50', { headers: { 'Accept': 'application/json' } });
      const data = await res.json();
      if (!data || !data.success) return;

      const anns = Array.isArray(data.announcements) ? data.announcements : [];
      setEmpty(anns.length === 0);

      // Re-render the grid every time (simple + predictable).
      const html = [];
      for (const a of anns) {
        const isFresh = !seenIds.has(a.id);
        html.push(renderAnnouncementCard(a, isFresh));
        seenIds.add(a.id);
      }
      grid.innerHTML = html.join('');
    } catch (_) {
      // Silent fail; server-rendered content remains.
    }
  };

  const markSeen = async () => {
    try {
      await fetch('/api/landing/announcements/mark_seen', {
        method: 'POST',
        headers: { 'Accept': 'application/json' },
      });
      await refresh();
    } catch (_) {
      // ignore
    }
  };

  if (markSeenBtn) {
    markSeenBtn.addEventListener('click', markSeen);
  }

  // Initial refresh shortly after load + polling.
  window.addEventListener('load', () => {
    setTimeout(refresh, 300);
  });
  setInterval(refresh, 30000);
})();
