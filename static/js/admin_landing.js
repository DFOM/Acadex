(function () {
  // --- Sections ordering (drag/drop) ---
  const list = document.getElementById('axSectionsList');
  const orderInput = document.getElementById('axSectionsOrder');

  const updateOrder = () => {
    if (!list || !orderInput) return;
    const names = Array.from(list.querySelectorAll('[data-name]')).map((el) => el.getAttribute('data-name'));
    orderInput.value = names.filter(Boolean).join(',');
  };

  if (list) {
    let dragging = null;

    list.addEventListener('dragstart', (e) => {
      const li = e.target.closest('li[data-name]');
      if (!li) return;
      dragging = li;
      li.classList.add('opacity-50');
      e.dataTransfer.effectAllowed = 'move';
    });

    list.addEventListener('dragend', () => {
      if (dragging) dragging.classList.remove('opacity-50');
      dragging = null;
      updateOrder();
    });

    list.addEventListener('dragover', (e) => {
      if (!dragging) return;
      e.preventDefault();
      const over = e.target.closest('li[data-name]');
      if (!over || over === dragging) return;

      const rect = over.getBoundingClientRect();
      const shouldInsertBefore = e.clientY < rect.top + rect.height / 2;
      if (shouldInsertBefore) {
        list.insertBefore(dragging, over);
      } else {
        list.insertBefore(dragging, over.nextSibling);
      }
    });

    updateOrder();
  }

  // --- Features editor ---
  const featuresForm = document.getElementById('axFeaturesForm');
  const featuresJson = document.getElementById('axFeaturesJson');
  const featuresTitle = document.getElementById('axFeaturesTitle');
  const cardsWrap = document.getElementById('axFeatureCards');
  const addCardBtn = document.getElementById('axAddFeatureCard');

  const cardTemplate = () => {
    return `
      <div class="card mb-3" data-feature-card>
        <div class="card-body">
          <div class="row g-3">
            <div class="col-md-3">
              <label class="form-label fw-semibold">Icon</label>
              <select class="form-select" data-icon>
                <option value="calendar">calendar</option>
                <option value="sparkles" selected>sparkles</option>
                <option value="shield-check">shield-check</option>
                <option value="chart-bar">chart-bar</option>
                <option value="bolt">bolt</option>
                <option value="book-open">book-open</option>
                <option value="megaphone">megaphone</option>
                <option value="info">info</option>
              </select>
            </div>
            <div class="col-md-4">
              <label class="form-label fw-semibold">Title</label>
              <input class="form-control" data-title>
            </div>
            <div class="col-md-5">
              <label class="form-label fw-semibold">Description</label>
              <input class="form-control" data-description>
            </div>
          </div>
          <div class="mt-3 text-end">
            <button type="button" class="btn btn-sm btn-outline-danger" data-remove-card>Remove</button>
          </div>
        </div>
      </div>
    `;
  };

  const serializeFeatures = () => {
    if (!featuresJson || !cardsWrap) return;

    const cards = [];
    for (const cardEl of cardsWrap.querySelectorAll('[data-feature-card]')) {
      const icon = cardEl.querySelector('[data-icon]')?.value || 'sparkles';
      const title = (cardEl.querySelector('[data-title]')?.value || '').trim();
      const description = (cardEl.querySelector('[data-description]')?.value || '').trim();
      if (!title && !description) continue;
      cards.push({ icon, title, description });
    }

    const payload = {
      title: (featuresTitle?.value || '').trim() || 'Why Acadex',
      cards,
    };

    featuresJson.value = JSON.stringify(payload);
  };

  if (cardsWrap) {
    cardsWrap.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-remove-card]');
      if (!btn) return;
      const card = btn.closest('[data-feature-card]');
      if (card) card.remove();
    });
  }

  if (addCardBtn && cardsWrap) {
    addCardBtn.addEventListener('click', () => {
      cardsWrap.insertAdjacentHTML('beforeend', cardTemplate());
    });
  }

  if (featuresForm) {
    featuresForm.addEventListener('submit', () => {
      serializeFeatures();
    });
  }
})();
