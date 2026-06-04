// github_activity — Spectra status archetype. Hero = total events
// over the window; pill names the user; status-grid breaks out the
// activity type counts (commits / PRs / issues / releases); a
// Chart.js bar chart of the last 7 days sits at the bottom so a
// glance shows whether activity is trending up.

import { barChart, tokens } from "../../static/spectra-chart.js";

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const DOW_SHORT = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="github_activity">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>GitHub</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const user = data.user || "GitHub";
  const total = data.count || 0;
  const daily = Array.isArray(data.daily) ? data.daily : [];

  const cells = [
    ["Commits", data.type_commits ?? 0, "var(--accent-3)"],
    ["PRs", data.type_prs ?? 0, "var(--accent-4)"],
    ["Issues", data.type_issues ?? 0, "var(--accent-1)"],
    ["Releases", data.type_releases ?? 0, "var(--accent-2)"],
  ];

  const grid = cells.map(([label, value, c]) => `
    <div class="status-cell">
      <span class="u-label">${escapeHtml(label)}</span>
      <span class="v" style="color:${c}">${escapeHtml(String(value))}</span>
    </div>`).join("");

  // Last 7 days of activity. Server returns oldest-first; labels reflect
  // weekday in that order. Today is the last bar.
  const todayIdx = (new Date().getDay() + 6) % 7; // Mon = 0
  const labels = daily.map((_, i) => {
    const dow = (todayIdx - (daily.length - 1 - i) + 7) % 7;
    return DOW_SHORT[dow];
  });

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="github_activity">
      <div class="w-title">
        <i class="ph-bold ph-github-logo" style="color:var(--accent-5)"></i>
        <h3>${escapeHtml(user)}</h3>
        <span class="w-title-meta">${escapeHtml(String(data.repos_count || 0))} REPOS</span>
      </div>
      <div class="w-body status-body">
        <div class="status-hero">
          <i class="ph-bold ph-pulse" style="color:var(--accent-3)"></i>
          <div class="lockup">
            <span class="status-state">${escapeHtml(String(total))}</span>
            <span class="status-sub">events this week</span>
          </div>
        </div>
        <div class="status-grid">${grid}</div>
        ${daily.length >= 2 ? `<div style="flex:1 1 auto;min-height:2em;position:relative"><canvas></canvas></div>` : ""}
      </div>
    </div>`;

  if (daily.length >= 2) {
    const canvas = shadow.querySelector("canvas");
    const t = tokens(shadow.host);
    barChart(canvas, {
      tokens: t,
      labels,
      values: daily,
      color: t.accent5,
      highlightColor: t.accent3,
      highlightIdx: daily.length - 1,
      showY: false,
    });
  }
}
