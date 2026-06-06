// github_repo, Spectra status archetype with a richer payload. Hero
// = star count, description as the hero sub. Below: language pill +
// latest-release tag, a six-cell status-grid for forks / issues /
// watchers / commits-this-year / license / last-push, a horizontal
// language-share bar coloured by Spectra accent, and a weekly-commit
// sparkline along the bottom.

import { sparkline, tokens } from "../../static/spectra-chart.js";

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtCount(n) {
  if (n == null) return "-";
  const v = Number(n) || 0;
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k`;
  return String(v);
}

function fmtAgo(iso) {
  if (typeof iso !== "string" || !iso) return "-";
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return "-";
  const secs = Math.max(0, (Date.now() - t) / 1000);
  if (secs < 3600) return `${Math.max(1, Math.floor(secs / 60))}m`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h`;
  if (secs < 604800) return `${Math.floor(secs / 86400)}d`;
  if (secs < 2592000) return `${Math.floor(secs / 604800)}w`;
  if (secs < 31536000) return `${Math.floor(secs / 2592000)}mo`;
  return `${Math.floor(secs / 31536000)}y`;
}

// Rotate through the six categorical accents so the language bar
// reads as distinct slices. "Other" rolls up the long tail and
// always lands on --text-muted so the dominant languages stand out.
const LANG_ACCENTS = [
  "var(--accent-5)", // slate blue, usually the top language
  "var(--accent-4)", // teal
  "var(--accent-3)", // moss
  "var(--accent-2)", // ochre
  "var(--accent-6)", // plum
  "var(--accent-1)", // terracotta
];

function languageBar(items) {
  if (!Array.isArray(items) || !items.length) return "";
  const segs = items.map((l, i) => {
    const color = l.name === "Other" ? "var(--text-muted)" : LANG_ACCENTS[i % LANG_ACCENTS.length];
    const pct = Number(l.pct) || 0;
    return `<div style="width:${pct}%;background:${color};height:100%" title="${escapeHtml(l.name)} ${pct}%"></div>`;
  }).join("");
  const legend = items.map((l, i) => {
    const color = l.name === "Other" ? "var(--text-muted)" : LANG_ACCENTS[i % LANG_ACCENTS.length];
    return `
      <span class="chart-key">
        <span class="dot" style="background:${color}"></span>${escapeHtml(l.name)}
        <small style="color:var(--text-muted);font-weight:var(--fw-semi);margin-left:.2em">${Number(l.pct).toFixed(1)}%</small>
      </span>`;
  }).join("");
  return `
    <div style="display:flex;flex-direction:column;gap:var(--space-2)">
      <div style="display:flex;height:var(--stroke-3);background:var(--surface-sunken);overflow:hidden">
        ${segs}
      </div>
      <div class="chart-legend">${legend}</div>
    </div>`;
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

  const repo = data.repo || "-";
  const stars = fmtCount(data.stars);
  const description = data.description || "";
  const language = data.language || "";
  const release = data.latest_release || "";
  const license = data.license || "";
  const branch = data.default_branch || "";
  const archived = data.is_archived;
  const series = Array.isArray(data.commit_weeks) ? data.commit_weeks : [];
  const langs = Array.isArray(data.languages) ? data.languages : [];
  const contributors = Array.isArray(data.contributors) ? data.contributors : [];

  // Six headline counters, laid out by the status-grid as 2 columns
  // × 3 rows. Counters everyone glances for in a repo card.
  const cells = [
    ["Forks", fmtCount(data.forks), "var(--accent-4)"],
    ["Issues", fmtCount(data.issues), "var(--accent-1)"],
    ["Watchers", fmtCount(data.watchers), "var(--accent-5)"],
    ["Year", fmtCount(data.commits_year), "var(--accent-3)"],
    ["License", license || "-", "var(--text-secondary)"],
    ["Pushed", fmtAgo(data.pushed_at), "var(--text-secondary)"],
  ];

  const grid = cells.map(([label, value, c]) => `
    <div class="status-cell">
      <span class="u-label">${escapeHtml(label)}</span>
      <span class="v" style="color:${c}">${escapeHtml(value)}</span>
    </div>`).join("");

  // Language + release sit on the same baseline as a sub-hero band.
  const chipRow = `
    <div class="u-row" style="gap:var(--space-2);flex-wrap:wrap">
      ${language ? `<span class="pill" style="background:var(--accent-5)">${escapeHtml(language)}</span>` : ""}
      ${release ? `<span class="pill" style="background:var(--accent-2)">${escapeHtml(release)}</span>` : ""}
      ${branch ? `<span class="u-label" style="font-size:var(--fs-caption)">${escapeHtml(branch)}</span>` : ""}
    </div>`;

  // Top-contributors strip, up to 5 leading committers with their
  // avatar, login, and contribution count. Renders below the language
  // bar at md+ sizes; hidden at xs/sm via the container query below.
  const contribStrip = contributors.length
    ? `
      <div class="contrib-row">
        <span class="u-label contrib-row-label">Top contributors</span>
        <div class="contrib-list">
          ${contributors.slice(0, 5).map((c) => {
            const initials = (c.login || "?").slice(0, 2).toUpperCase();
            const avatar = c.avatar_url
              ? `<img class="contrib-avatar" src="${escapeHtml(c.avatar_url)}&size=48" alt="" loading="lazy">`
              : `<span class="contrib-avatar contrib-avatar-fallback">${escapeHtml(initials)}</span>`;
            return `
              <span class="contrib-tile" title="${escapeHtml(c.login)} · ${c.contributions} commits">
                ${avatar}
                <span class="contrib-login">${escapeHtml(c.login)}</span>
                <span class="contrib-count">${fmtCount(c.contributions)}</span>
              </span>`;
          }).join("")}
        </div>
      </div>`
    : "";

  const layout = `
    .contrib-row {
      display: flex;
      flex-direction: column;
      gap: var(--space-1);
    }
    .contrib-row-label {
      color: var(--text-muted);
    }
    .contrib-list {
      display: flex;
      gap: var(--space-2);
      flex-wrap: wrap;
    }
    .contrib-tile {
      display: flex;
      align-items: center;
      gap: var(--space-1);
      padding: 3px var(--space-1);
      border-radius: var(--radius-1);
      background: color-mix(in oklab, var(--text-primary) 4%, transparent);
      font-size: var(--fs-caption);
      font-variant-numeric: tabular-nums;
      min-width: 0;
    }
    .contrib-avatar {
      width: 1.4em;
      height: 1.4em;
      border-radius: 50%;
      object-fit: cover;
      flex: 0 0 auto;
      background: var(--surface-sunken);
    }
    .contrib-avatar-fallback {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-weight: var(--fw-black);
      font-size: .7em;
      color: var(--text-secondary);
      letter-spacing: 0;
    }
    .contrib-login {
      font-weight: var(--fw-bold);
      color: var(--text-secondary);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      max-width: 8em;
    }
    .contrib-count {
      color: var(--text-muted);
      font-weight: var(--fw-semi);
    }
    @container (max-width: 360px) {
      .contrib-login { display: none; }
    }
    @container (max-width: 280px) {
      .contrib-row { display: none; }
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
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
            <span class="status-sub" style="white-space:normal;line-height:var(--lh-snug);max-height:2.6em;overflow:hidden">${escapeHtml(description) || `${data.commits_year || 0} commits this year`}</span>
          </div>
        </div>
        ${(language || release || branch) ? chipRow : ""}
        <div class="status-grid">${grid}</div>
        ${langs.length ? languageBar(langs) : ""}
        ${contribStrip}
        ${series.length >= 2 ? `<div style="flex:0 0 18%;min-height:1.8em;position:relative"><canvas></canvas></div>` : ""}
      </div>
    </div>`;

  if (series.length >= 2) {
    const canvas = shadow.querySelector("canvas");
    const t = tokens(shadow.host);
    sparkline(canvas, series, t.accent3);
  }
}
