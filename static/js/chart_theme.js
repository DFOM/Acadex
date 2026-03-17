/* Acadex Chart.js theming helper
 * Keeps charts readable across light/dark themes by mapping to CSS variables.
 */

(function () {
  function readCssVar(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name);
    const trimmed = (v || '').trim();
    return trimmed || fallback;
  }

  function hexToRgba(hex, alpha) {
    const h = (hex || '').trim();
    if (!h) return `rgba(59,130,246,${alpha})`;

    // Support rgb()/rgba() already.
    if (h.startsWith('rgb')) return h;

    const normalized = h.replace('#', '');
    const isShort = normalized.length === 3;
    const isLong = normalized.length === 6;
    if (!isShort && !isLong) return `rgba(59,130,246,${alpha})`;

    const r = parseInt(isShort ? normalized[0] + normalized[0] : normalized.slice(0, 2), 16);
    const g = parseInt(isShort ? normalized[1] + normalized[1] : normalized.slice(2, 4), 16);
    const b = parseInt(isShort ? normalized[2] + normalized[2] : normalized.slice(4, 6), 16);
    return `rgba(${r},${g},${b},${alpha})`;
  }

  function theme() {
    const root = document.documentElement;
    const t = root.getAttribute('data-theme');
    return t === 'dark' ? 'dark' : 'light';
  }

  function palette() {
    const text = readCssVar('--ax-text', '#111827');
    const muted = readCssVar('--ax-muted', '#6B7280');
    const border = readCssVar('--ax-border', '#E5E7EB');

    // Prefer Bootstrap vars if present (they’re bridged in design_system.css).
    const primary = readCssVar('--bs-primary', readCssVar('--ax-primary', '#3B82F6'));
    const success = readCssVar('--bs-success', '#22c55e');
    const info = readCssVar('--bs-info', '#06b6d4');
    const warning = readCssVar('--bs-warning', '#f59e0b');
    const danger = readCssVar('--bs-danger', '#ef4444');
    const secondary = readCssVar('--bs-secondary', '#64748b');

    return {
      theme: theme(),
      text,
      muted,
      border,
      primary,
      success,
      info,
      warning,
      danger,
      secondary,
      rgbaPrimary: (a) => hexToRgba(primary, a),
      rgbaSuccess: (a) => hexToRgba(success, a),
      rgbaInfo: (a) => hexToRgba(info, a),
      rgbaWarning: (a) => hexToRgba(warning, a),
      rgbaDanger: (a) => hexToRgba(danger, a),
      rgbaSecondary: (a) => hexToRgba(secondary, a),
    };
  }

  function applyChartDefaults() {
    if (typeof window.Chart === 'undefined') return;

    const p = palette();
    const fontFamily = readCssVar('--ax-font', "Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, Arial");

    Chart.defaults.font.family = fontFamily;
    Chart.defaults.color = p.muted;

    // Subtle animations: premium feel, not distracting.
    Chart.defaults.animation = { duration: 650, easing: 'easeOutQuart' };

    // Tooltip readability.
    Chart.defaults.plugins = Chart.defaults.plugins || {};
    Chart.defaults.plugins.tooltip = Chart.defaults.plugins.tooltip || {};
    Chart.defaults.plugins.tooltip.backgroundColor = p.theme === 'dark' ? 'rgba(15,23,42,0.92)' : 'rgba(17,24,39,0.92)';
    Chart.defaults.plugins.tooltip.titleColor = '#ffffff';
    Chart.defaults.plugins.tooltip.bodyColor = '#ffffff';
    Chart.defaults.plugins.tooltip.borderColor = p.border;
    Chart.defaults.plugins.tooltip.borderWidth = 1;

    // Default scale styling (Chart.js v3+).
    Chart.defaults.scale = Chart.defaults.scale || {};
    Chart.defaults.scale.grid = Chart.defaults.scale.grid || {};
    Chart.defaults.scale.grid.color = hexToRgba(p.border, p.theme === 'dark' ? 0.35 : 0.6);
    Chart.defaults.scale.border = Chart.defaults.scale.border || {};
    Chart.defaults.scale.border.color = hexToRgba(p.border, p.theme === 'dark' ? 0.5 : 0.8);
    Chart.defaults.scale.ticks = Chart.defaults.scale.ticks || {};
    Chart.defaults.scale.ticks.color = p.muted;
  }

  // Expose a tiny API for templates.
  window.AcadexCharts = {
    palette,
    applyChartDefaults,
  };
})();
