/* Acadex Theme Controller
   - Persists user choice in localStorage
   - Falls back to system preference
   - Applies theme via data-theme on :root
*/

(function () {
  const STORAGE_KEY = 'acadex-theme';

  function getSystemTheme() {
    try {
      return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
        ? 'dark'
        : 'light';
    } catch (_) {
      return 'light';
    }
  }

  function getStoredTheme() {
    try {
      const v = localStorage.getItem(STORAGE_KEY);
      return v === 'dark' || v === 'light' ? v : null;
    } catch (_) {
      return null;
    }
  }

  function setTheme(theme) {
    const root = document.documentElement;
    root.setAttribute('data-theme', theme);

    // Keep a helpful meta theme-color for mobile browser UI.
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) {
      meta.setAttribute('content', theme === 'dark' ? '#0F172A' : '#F9FAFB');
    }

    // Update toggle button state if present.
    const btn = document.getElementById('themeToggle');
    if (btn) {
      btn.setAttribute('aria-pressed', theme === 'dark' ? 'true' : 'false');
      btn.setAttribute('data-theme', theme);
    }

    // Allow other UI (e.g., charts) to react.
    window.dispatchEvent(new CustomEvent('acadex:theme-changed', { detail: { theme } }));
  }

  function persistTheme(theme) {
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch (_) {
      // no-op
    }
  }

  function init() {
    const initial = getStoredTheme() || getSystemTheme();
    setTheme(initial);

    const btn = document.getElementById('themeToggle');
    if (btn) {
      btn.addEventListener('click', function () {
        const current = document.documentElement.getAttribute('data-theme') || 'light';
        const next = current === 'dark' ? 'light' : 'dark';
        setTheme(next);
        persistTheme(next);
      });
    }

    // If user has not explicitly chosen, live-update with OS.
    if (!getStoredTheme() && window.matchMedia) {
      const mq = window.matchMedia('(prefers-color-scheme: dark)');
      mq.addEventListener?.('change', () => setTheme(getSystemTheme()));
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
