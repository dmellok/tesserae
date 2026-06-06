// f1_weekend, Spectra list archetype with day-grouped sessions.
// Sessions cluster under FRIDAY / SATURDAY / SUNDAY headers so the
// weekend's shape reads at a glance instead of being smuggled into
// each row's subtitle. Race row gets an accent-1 tinted block + bold
// weight so the headline session always pops. Country flag in the
// title row matches the f1_next title style (and renders via
// fonts-noto-color-emoji on the Linux Docker image).

import { getCircuit, trackSvg } from "../f1_core/static/circuits.js";

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Country → flag emoji. Same map as f1_next; widgets in the F1 family
// share the title-row treatment so a dashboard reads as one unit.
const COUNTRY_FLAG = {
  "Australia": "🇦🇺",
  "Bahrain": "🇧🇭",
  "Saudi Arabia": "🇸🇦",
  "China": "🇨🇳",
  "USA": "🇺🇸",
  "United States": "🇺🇸",
  "Italy": "🇮🇹",
  "Monaco": "🇲🇨",
  "Canada": "🇨🇦",
  "Spain": "🇪🇸",
  "Austria": "🇦🇹",
  "UK": "🇬🇧",
  "United Kingdom": "🇬🇧",
  "Great Britain": "🇬🇧",
  "Hungary": "🇭🇺",
  "Belgium": "🇧🇪",
  "Netherlands": "🇳🇱",
  "Azerbaijan": "🇦🇿",
  "Singapore": "🇸🇬",
  "Japan": "🇯🇵",
  "Qatar": "🇶🇦",
  "Mexico": "🇲🇽",
  "Brazil": "🇧🇷",
  "UAE": "🇦🇪",
  "United Arab Emirates": "🇦🇪",
  "France": "🇫🇷",
  "Germany": "🇩🇪",
  "Russia": "🇷🇺",
  "South Africa": "🇿🇦",
  "Argentina": "🇦🇷",
  "Portugal": "🇵🇹",
  "Turkey": "🇹🇷",
  "South Korea": "🇰🇷",
  "India": "🇮🇳",
  "Malaysia": "🇲🇾",
};

function flagFor(country) {
  return COUNTRY_FLAG[String(country || "").trim()] || "";
}

// "2025-03-14" → "FRIDAY · 14 MAR" for day-group headers. UTC timezone
// to match Ergast's date convention (the date the session begins in
// the track's local day, not the user's). Falls back to the raw ISO
// slice if Date can't parse the string.
function fmtDayHeader(date) {
  if (typeof date !== "string") return "";
  try {
    const dt = new Date(date + "T12:00:00Z");
    if (!Number.isFinite(dt.getTime())) return date;
    const day = dt.toLocaleDateString("en-GB", { weekday: "long", timeZone: "UTC" });
    const num = dt.getUTCDate();
    const month = dt.toLocaleDateString("en-GB", { month: "short", timeZone: "UTC" });
    return `${day} · ${num} ${month}`;
  } catch {
    return date;
  }
}

function fmtTime(time) {
  if (typeof time !== "string" || !time) return "";
  return time.slice(0, 5);
}

// Map the server's label string to a Phosphor icon so the row list
// reads like an F1 timing board. Practice = stopwatch (timing laps),
// sprint = lightning (short flat-out race), qualifying = target
// (chasing pole), race = checkered flag.
function sessionIcon(label) {
  const norm = String(label || "").toLowerCase();
  if (norm.startsWith("race")) return "ph-flag-checkered";
  if (norm.startsWith("qual")) return "ph-target";
  if (norm.startsWith("sprint")) return "ph-lightning";
  if (norm.startsWith("fp") || norm.startsWith("practice")) return "ph-stopwatch";
  return "ph-clock";
}

// Long-form session labels, server emits short codes that read fine
// in the list (FP1 / QUAL / RACE) but the day-group headers can
// breathe a bit so spell them out where the room allows.
function expandLabel(label) {
  const norm = String(label || "").toUpperCase();
  if (norm === "QUAL") return "QUALIFYING";
  if (norm === "SPRINT_Q") return "SPRINT QUALI";
  return norm;
}

// Bucket sessions by their date string. Preserves first-seen ordering
// of the days, so the resulting groups stay in calendar order
// regardless of how the server emitted the session list.
function groupByDay(sessions) {
  const order = [];
  const buckets = new Map();
  for (const s of sessions) {
    const key = s.date || "";
    if (!buckets.has(key)) {
      buckets.set(key, []);
      order.push(key);
    }
    buckets.get(key).push(s);
  }
  return order.map((date) => ({ date, sessions: buckets.get(date) }));
}

export default async function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="f1_weekend">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Weekend</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const sessions = Array.isArray(data.sessions) ? data.sessions : [];
  const subBits = [data.circuitName, data.locality].filter(Boolean).join(" · ");
  const flag = flagFor(data.country);

  let track = null;
  try { track = await getCircuit(data.circuitId); } catch { track = null; }

  const days = groupByDay(sessions);
  const dayBlocks = days.map((group) => {
    const rows = group.sessions.map((s) => {
      const isRace = String(s.label || "").toLowerCase() === "race";
      const ph = sessionIcon(s.label);
      const label = isRace ? "RACE" : expandLabel(s.label);
      return `
        <div class="weekend-row ${isRace ? "is-race" : ""}">
          <i class="ph-bold ${ph} weekend-row-icon"></i>
          <span class="weekend-row-label">${escapeHtml(label)}</span>
          <span class="weekend-row-time">${escapeHtml(fmtTime(s.time))}</span>
        </div>`;
    }).join("");
    return `
      <div class="weekend-day">
        <div class="weekend-day-head">${escapeHtml(fmtDayHeader(group.date))}</div>
        ${rows}
      </div>`;
  }).join("");

  const layout = `
    .f1-body {
      flex: 1 1 auto;
      min-height: 0;
      display: grid;
      grid-template-columns: 1fr;
      gap: var(--space-4);
      align-items: stretch;
    }
    .f1-data {
      display: flex;
      flex-direction: column;
      gap: var(--space-3);
      min-height: 0;
      min-width: 0;
    }
    .weekend-meta {
      flex: 0 0 auto;
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      letter-spacing: var(--ls-label);
      text-transform: var(--label-transform, uppercase);
      color: var(--text-muted);
      display: inline-flex;
      align-items: center;
      gap: 0.4em;
      align-self: flex-start;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 100%;
      min-width: 0;
    }
    .weekend-meta .ph-bold {
      color: var(--accent-1);
      font-size: 1.1em;
      flex: 0 0 auto;
    }

    .weekend-days {
      flex: 1 1 auto;
      min-height: 0;
      display: flex;
      flex-direction: column;
      gap: var(--space-3);
      overflow: hidden;
    }
    .weekend-day {
      display: flex;
      flex-direction: column;
      gap: var(--space-1);
    }
    .weekend-day-head {
      font-size: var(--fs-caption);
      font-weight: var(--fw-black);
      letter-spacing: var(--ls-label);
      text-transform: var(--label-transform, uppercase);
      color: var(--text-muted);
      padding: 0 var(--space-2);
      border-bottom: var(--stroke-1) solid var(--surface-sunken);
      padding-bottom: var(--space-1);
      margin-bottom: 0.15em;
    }

    .weekend-row {
      display: grid;
      grid-template-columns: auto 1fr auto;
      align-items: center;
      gap: var(--space-3);
      padding: var(--space-1) var(--space-2);
      border-radius: var(--radius-0);
    }
    .weekend-row-icon {
      color: var(--text-secondary);
      font-size: var(--icon-md);
      line-height: 1;
    }
    .weekend-row-label {
      font-size: var(--fs-body);
      font-weight: var(--fw-bold);
      letter-spacing: var(--ls-tight);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      min-width: 0;
    }
    .weekend-row-time {
      font-size: var(--fs-body);
      font-weight: var(--fw-black);
      font-variant-numeric: tabular-nums;
      color: var(--text-primary);
    }
    /* Race row pops, soft accent-1 tinted background with a left
       border + bold label + accent-coloured time. So a quick scan
       always lands on "the race is at 13:00" first. */
    .weekend-row.is-race {
      background: color-mix(in oklab, var(--accent-1) 14%, transparent);
      border-left: var(--stroke-3) solid var(--accent-1);
      padding-left: var(--space-2);
    }
    .weekend-row.is-race .weekend-row-icon,
    .weekend-row.is-race .weekend-row-label,
    .weekend-row.is-race .weekend-row-time {
      color: var(--accent-1);
    }
    .weekend-row.is-race .weekend-row-label,
    .weekend-row.is-race .weekend-row-time {
      font-weight: var(--fw-black);
    }

    /* Country flag chip in the title. Explicit emoji font stack so
       the Linux Docker image's Noto Color Emoji wins the fallback
       race. */
    .weekend-flag {
      font-size: 1.3em;
      line-height: 1;
      margin-right: 0.2em;
      font-family: "Noto Color Emoji", "Apple Color Emoji",
                   "Segoe UI Emoji", "Twemoji Mozilla", sans-serif;
    }

    /* Circuit silhouette, hidden by default; LG only. */
    .f1-track {
      display: none;
      color: var(--accent-1);
      overflow: hidden;
      align-items: center;
      justify-content: center;
    }
    .f1-track svg { width: 100%; height: 100%; display: block; }

    /* xs / sm: drop the location meta line so the day-grouped sessions
       have full vertical room. */
    @container (max-width: 440px) {
      .weekend-meta { display: none; }
    }

    /* lg: side-by-side data + circuit. */
    @container (min-width: 700px) {
      .f1-body { grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr); }
      .f1-track { display: flex; }
      .weekend-row { padding: var(--space-2) var(--space-3); }
      .weekend-row-label { font-size: var(--fs-lead); }
      .weekend-row-time { font-size: var(--fs-lead); }
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="f1_weekend">
      <div class="w-title">
        ${flag ? `<span class="weekend-flag" aria-hidden="true">${flag}</span>` : `<i class="ph-bold ph-flag-checkered" style="color:var(--accent-1)"></i>`}
        <h3>${escapeHtml(data.raceName || "Weekend")}</h3>
        ${data.round ? `<span class="w-title-meta">R${escapeHtml(String(data.round))}</span>` : ""}
      </div>
      <div class="w-body">
        <div class="f1-body">
          <div class="f1-data">
            ${subBits ? `<span class="weekend-meta"><i class="ph-bold ph-map-pin"></i>${escapeHtml(subBits)}</span>` : ""}
            <div class="weekend-days">${dayBlocks}</div>
          </div>
          ${track ? `<div class="f1-track">${trackSvg(track, { stroke: "var(--accent-1)" })}</div>` : ""}
        </div>
      </div>
    </div>`;
}
