/* Acadex Theme Controller v2
   - DARK is the default (no data-theme attr = dark)
   - Light mode is opt-in: sets data-theme="light"
   - Persists user choice in localStorage
   - Falls back to system preference only when no stored value
*/

(function () {
  const STORAGE_KEY = 'acadex-theme';

  function getSystemTheme() {
    try {
      return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
        ? 'dark'
        : 'light';
    } catch (_) {
      return 'dark'; // default to dark
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

  // Dark = default (no attribute). Light = data-theme="light".
  function setTheme(theme) {
    const root = document.documentElement;
    if (theme === 'light') {
      root.setAttribute('data-theme', 'light');
    } else {
      root.removeAttribute('data-theme');
    }

    // Mobile browser chrome color
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) {
      meta.setAttribute('content', theme === 'light' ? '#F9FAFB' : '#060D1A');
    }

    // Update toggle button icon (sun shown in dark, moon in light)
    const btn = document.getElementById('themeToggle');
    if (btn) {
      btn.setAttribute('aria-pressed', theme === 'light' ? 'true' : 'false');
      btn.setAttribute('data-current-theme', theme);
    }

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
        const current = document.documentElement.hasAttribute('data-theme') ? 'light' : 'dark';
        const next = current === 'light' ? 'dark' : 'light';
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
