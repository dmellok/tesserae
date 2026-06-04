// sky_aurora — Spectra status archetype. Hero is the current Kp
// index; status-pill carries the visibility band ("Quiet" through
// "Storm") with a band-mapped accent. A small Chart.js sparkline of
// the upcoming 24-hour Kp forecast sits at the bottom so a glance
// shows whether activity is climbing.

import { sparkline, tokens } from "../../static/spectra-chart.js";

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Kp band → accent token. 0-2 quiet (muted), 3-4 unsettled (slate),
// 5-6 minor / moderate storm (ochre / terracotta), 7-9 severe (plum).
function bandAccent(kp) {
  const v = Number(kp);
  if (!Number.isFinite(v)) return "var(--text-secondary)";
  if (v < 3) return "var(--text-muted)";
  if (v < 5) return "var(--accent-5)";
  if (v < 6) return "var(--accent-2)";
  if (v < 7) return "var(--accent-1)";
  return "var(--accent-6)";
}

function tokenKey(kp) {
  const v = Number(kp);
  if (!Number.isFinite(v)) return "textSecondary";
  if (v < 3) return "textMuted";
  if (v < 5) return "accent5";
  if (v < 6) return "accent2";
  if (v < 7) return "accent1";
  return "accent6";
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="sky_aurora">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Aurora</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const kp = data.current_kp;
  const accent = bandAccent(kp);
  const band = data.band_label || "—";
  const forecastBand = data.forecast_band || "";
  const visibleNow = data.visible_now === true;
  const visibleSoon = data.visible_soon === true;

  const forecast = Array.isArray(data.forecast) ? data.forecast.slice(0, 24) : [];
  const series = forecast.map((f) => Number(f.kp ?? f[1] ?? 0)).filter(Number.isFinite);

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="sky_aurora">
      <div class="w-title">
        <i class="ph-bold ph-rainbow" style="color:${accent}"></i>
        <h3>Aurora</h3>
        ${visibleNow ? `<span class="w-title-meta" style="color:var(--accent-3)">VISIBLE</span>`
          : visibleSoon ? `<span class="w-title-meta" style="color:var(--accent-2)">SOON</span>`
          : `<span class="w-title-meta">Kp ${escapeHtml(String(kp ?? "—"))}</span>`}
      </div>
      <div class="w-body status-body">
        <div class="status-hero">
          <i class="ph-bold ph-compass" style="color:${accent}"></i>
          <div class="lockup">
            <span class="status-state">${escapeHtml(String(kp ?? "—"))}</span>
            <span class="status-sub">Kp index · oval ${escapeHtml(String(data.oval_lat ?? "—"))}°</span>
          </div>
        </div>
        <span class="pill" style="background:${accent}">${escapeHtml(band)}</span>
        ${forecastBand ? `<div class="status-grid"><div class="status-cell"><span class="u-label">3-day</span><span class="v">${escapeHtml(forecastBand)}</span></div><div class="status-cell"><span class="u-label">Oval</span><span class="v">${escapeHtml(String(data.forecast_oval ?? "—"))}°</span></div></div>` : ""}
        ${series.length >= 2 ? `<div style="flex:1 1 auto;min-height:2em;position:relative"><canvas></canvas></div>` : ""}
      </div>
    </div>`;

  if (series.length >= 2) {
    const canvas = shadow.querySelector("canvas");
    const t = tokens(shadow.host);
    const lineColor = t[tokenKey(kp)] || t.accent5;
    sparkline(canvas, series, lineColor);
  }
}
