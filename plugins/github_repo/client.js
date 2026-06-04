// github_repo — Spectra status archetype with a weekly-commit
// sparkline rail. Hero = star count; pill = primary language;
// status-grid breaks out forks / issues / watchers + the latest
// release; sparkline shows commits per week over the past year.

import { sparkline, tokens } from "../../static/spectra-chart.js";

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtCount(n) {
  if (n == null) return "—";
  const v = Number(n) || 0;
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k`;
  return String(v);
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="github_repo">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Repo</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const repo = data.repo || "—";
  const stars = fmtCount(data.stars);
  const language = data.language || "";
  const archived = data.is_archived;
  const series = Array.isArray(data.commit_weeks) ? data.commit_weeks : [];

  const cells = [
    ["Forks", fmtCount(data.forks), "var(--accent-4)"],
    ["Issues", fmtCount(data.issues), "var(--accent-1)"],
    ["Watchers", fmtCount(data.watchers), "var(--accent-5)"],
    ["Release", data.latest_release || "—", "var(--accent-2)"],
  ];

  const grid = cells.map(([label, value, c]) => `
    <div class="status-cell">
      <span class="u-label">${escapeHtml(label)}</span>
      <span class="v" style="color:${c}">${escapeHtml(value)}</span>
    </div>`).join("");

  const langPill = language
    ? `<span class="pill" style="background:var(--accent-5)">${escapeHtml(language)}</span>`
    : "";

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="github_repo">
      <div class="w-title">
        <i class="ph-bold ph-git-branch" style="color:var(--accent-3)"></i>
        <h3>${escapeHtml(repo)}</h3>
        ${archived ? `<span class="w-title-meta" style="color:var(--accent-1)">ARCHIVED</span>` : ""}
      </div>
      <div class="w-body status-body">
        <div class="status-hero">
          <i class="ph-bold ph-star" style="color:var(--accent-2)"></i>
          <div class="lockup">
            <span class="status-state">${escapeHtml(stars)}</span>
            <span class="status-sub">${escapeHtml(data.description || `${data.commits_year || 0} commits / year`)}</span>
          </div>
        </div>
        ${langPill}
        <div class="status-grid">${grid}</div>
        ${series.length >= 2 ? `<div style="flex:0 0 18%;min-height:1.5em;position:relative"><canvas></canvas></div>` : ""}
      </div>
    </div>`;

  if (series.length >= 2) {
    const canvas = shadow.querySelector("canvas");
    const t = tokens(shadow.host);
    sparkline(canvas, series, t.accent3);
  }
}
