// f1_next, Spectra status archetype. Headline countdown to lights-out
// in accent-1 (F1 red), country flag in the title row, and the entire
// weekend schedule as a row of session mini-cards (FP1 / FP2 / FP3 /
// Sprint / Quali / Race) colour-coded by session type. Circuit
// silhouette on the right column at lg, hidden at smaller sizes so
// the schedule cards get the body width.

import { getCircuit, trackSvg } from "../f1_core/static/circuits.js";

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Country name → flag emoji. Covers every host on the current F1
// calendar plus historical / occasional venues. Falls back to no
// flag (empty string) for an unmapped country so a missed entry
// degrades gracefully instead of paining a broken codepoint.
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

// Each session-type's identity: short label, Phosphor icon, accent
// token. Practice = stopwatch (timing laps), sprint = lightning
// (short flat-out race), quali = target (chasing pole), race =
// checkered flag (the main event).
const SESSION_META = {
  fp1: { label: "FP1", icon: "ph-stopwatch", accent: "var(--text-secondary)" },
  fp2: { label: "FP2", icon: "ph-stopwatch", accent: "var(--text-secondary)" },
  fp3: { label: "FP3", icon: "ph-stopwatch", accent: "var(--text-secondary)" },
  sprint: { label: "Sprint", icon: "ph-lightning", accent: "var(--accent-2)" },
  qualifying: { label: "Quali", icon: "ph-target", accent: "var(--accent-2)" },
  race: { label: "Race", icon: "ph-flag-checkered", accent: "var(--accent-1)" },
};
const SESSION_ORDER = ["fp1", "fp2", "fp3", "sprint", "qualifying", "race"];

function combineDt(date, time) {
  if (!date) return null;
  const iso = time ? `${date}T${time.endsWith("Z") ? time : time + "Z"}` : `${date}T00:00:00Z`;
  const t = new Date(iso).getTime();
  return Number.isFinite(t) ? t : null;
}

function fmtCountdown(ms) {
  if (!Number.isFinite(ms) || ms <= 0) return "LIVE";
  const days = Math.floor(ms / 86400000);
  const hours = Math.floor((ms % 86400000) / 3600000);
  const mins = Math.floor((ms % 3600000) / 60000);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${mins}m`;
  return `${mins}m`;
}

// "2025-06-14" → "Sat 14". Falls back to the raw slice if Date can't
// parse the string (timezone-naive ISO dates like "2025-06-14" parse
// fine across browsers, so this path is mostly defensive).
function fmtSessionDate(date) {
  if (!date) return "";
  try {
    const d = new Date(date + "T12:00:00");
    if (!Number.isFinite(d.getTime())) return date.slice(5);
    return d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric" });
  } catch {
    return date.slice(5);
  }
}

// "14:00:00Z" → "14:00".
function fmtSessionClock(time) {
  if (!time) return "";
  return String(time).slice(0, 5);
}

export default async function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const opts = ctx?.cell?.options || {};
  const showSeconds = opts.show_seconds === true;
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="f1_next">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Next Race</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const sessions = data.sessions || {};
  const raceDt = combineDt(data.date, data.time);
  const now = Date.now();
  const countdown = raceDt ? fmtCountdown(raceDt - now) : "-";

  let countdownLabel = countdown;
  if (showSeconds && raceDt && raceDt - now < 3600000 && raceDt - now > 0) {
    const remain = Math.floor((raceDt - now) / 1000);
    countdownLabel = `${Math.floor(remain / 60)}:${String(remain % 60).padStart(2, "0")}`;
  }

  // Inject the race itself as a session entry so the schedule grid
  // reads end-to-end (FP1 → … → Race) instead of stopping at Quali
  // with the headline race hiding above. Race uses the top-level
  // date / time the server already provides.
  const sessionsForGrid = { ...sessions, race: { date: data.date, time: data.time } };

  const sessionCards = SESSION_ORDER.map((key) => {
    const s = sessionsForGrid[key];
    if (!s || !s.date) return "";
    const meta = SESSION_META[key];
    const dateLabel = fmtSessionDate(s.date);
    const timeLabel = fmtSessionClock(s.time);
    return `
      <div class="session-card" data-session="${key}" style="--accent:${meta.accent}">
        <span class="session-head">
          <i class="ph-bold ${meta.icon} session-icon" style="color:${meta.accent}"></i>
          <span class="session-name">${escapeHtml(meta.label)}</span>
        </span>
        <span class="session-when">
          <span class="session-date">${escapeHtml(dateLabel)}</span>
          ${timeLabel ? `<span class="session-time">${escapeHtml(timeLabel)}</span>` : ""}
        </span>
      </div>`;
  }).filter(Boolean).join("");

  const subBits = [data.circuitName, data.locality].filter(Boolean).join(" · ");

  let track = null;
  try { track = await getCircuit(data.circuitId); } catch { track = null; }

  const flag = flagFor(data.country);

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
      justify-content: center;
    }
    /* Hero countdown, large accent-1 number with a small leading
       clock icon. Sub line lists circuit + locality below. */
    .next-hero {
      display: flex;
      align-items: center;
      gap: var(--space-3);
      flex: 0 0 auto;
    }
    .next-hero .ph-clock-countdown {
      color: var(--accent-1);
      font-size: clamp(2em, 14cqmin, 5em);
      line-height: 1;
    }
    .next-hero-lockup {
      display: flex;
      flex-direction: column;
      gap: 0.1em;
      min-width: 0;
    }
    .next-countdown {
      font-size: clamp(2em, 12cqmin, 5em);
      font-weight: var(--fw-black);
      line-height: var(--lh-tight);
      letter-spacing: var(--ls-tight);
      color: var(--accent-1);
      font-variant-numeric: tabular-nums;
    }
    .next-sub {
      font-size: var(--fs-body);
      font-weight: var(--fw-semi);
      color: var(--text-secondary);
      display: inline-flex;
      align-items: center;
      gap: 0.3em;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 100%;
    }
    .next-sub .ph-bold { color: var(--text-muted); font-size: 0.85em; }

    /* Schedule grid, 6 session cards (FP1, FP2, FP3, Sprint, Quali,
       Race). Each card has an icon-led head row + a date / time
       row. Accent is set per session type via the --accent CSS
       variable on the card's inline style. */
    .schedule {
      flex: 0 0 auto;
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: var(--space-2);
    }
    .session-card {
      display: flex;
      flex-direction: column;
      gap: 0.2em;
      padding: var(--space-2) var(--space-3);
      background: color-mix(in oklab, var(--accent) 8%, var(--surface));
      border-left: var(--stroke-3) solid var(--accent);
      min-width: 0;
    }
    .session-head {
      display: inline-flex;
      align-items: center;
      gap: 0.35em;
      font-size: var(--fs-label);
      font-weight: var(--fw-black);
      letter-spacing: var(--ls-label);
      text-transform: var(--label-transform, uppercase);
      color: var(--text-primary);
    }
    .session-icon { font-size: 1.1em; line-height: 1; }
    .session-when {
      display: flex;
      flex-direction: column;
      gap: 0;
      font-size: var(--fs-caption);
      color: var(--text-secondary);
      font-weight: var(--fw-bold);
      font-variant-numeric: tabular-nums;
    }
    .session-date {
      letter-spacing: var(--ls-label);
      text-transform: var(--label-transform, uppercase);
    }
    .session-time {
      font-size: 1.05em;
      color: var(--text-primary);
      font-weight: var(--fw-black);
    }

    /* Country flag chip in the title row. Explicit emoji font stack
       so the Linux Docker image (which installs fonts-noto-color-
       emoji) wins the fallback race even when the page's primary
       font-family doesn't include emoji glyphs. */
    .next-flag {
      font-size: 1.3em;
      line-height: 1;
      margin-right: 0.2em;
      font-family: "Noto Color Emoji", "Apple Color Emoji",
                   "Segoe UI Emoji", "Twemoji Mozilla", sans-serif;
    }

    /* Circuit silhouette, hidden by default; only LG cells get it.
       SVG has no intrinsic dims so the container must provide both
       width + height with preserveAspectRatio handling the meet. */
    .f1-track {
      display: none;
      color: var(--accent-1);
      overflow: hidden;
      align-items: center;
      justify-content: center;
    }
    .f1-track svg { width: 100%; height: 100%; display: block; }

    /* xs: drop schedule + circuit, hero countdown fills the body. */
    @container (max-width: 280px) {
      .schedule { display: none; }
      .next-sub { display: none; }
      .next-hero { justify-content: center; }
    }

    /* sm: 2-column grid for the schedule, drop the hero icon to save
       width for the countdown number. */
    @container (min-width: 281px) and (max-width: 440px) {
      .schedule { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .next-hero .ph-clock-countdown { display: none; }
    }

    /* Short cells (height under 280): drop the schedule entirely so
       the countdown + sub line aren't competing with the cards for
       vertical room. Sub line stays so the location still reads. */
    @container (max-height: 280px) {
      .schedule { display: none; }
      .next-countdown { font-size: clamp(1.4em, 18cqh, 2.8em); }
      .next-hero .ph-clock-countdown { font-size: clamp(1.4em, 20cqh, 2.8em); }
    }

    /* MD wide-but-short (height under 450): keep the schedule but
       shrink the countdown so it doesn't crash into the title bar,
       drop the sub line (location reads from the title), and tighten
       the card padding so 5-6 cards fit in 2 rows. */
    @container (min-width: 441px) and (max-width: 699px) and (max-height: 449px) {
      .next-countdown { font-size: clamp(1.5em, 16cqh, 3em); }
      .next-hero .ph-clock-countdown { font-size: clamp(1.5em, 18cqh, 3em); }
      .next-sub { display: none; }
      .schedule { gap: var(--space-1); }
      .session-card { padding: var(--space-1) var(--space-2); gap: 0.1em; }
      .session-when { font-size: var(--fs-caption); }
    }

    /* lg: side-by-side data column + circuit. Schedule lays out in a
       3-column grid so each card has room for the icon + full label
       ("QUALI" was clipping to "QUA" at the previous 6-cards-in-one-
       row layout). 5 or 6 sessions takes 2 rows; the row dimension
       follows the data the calendar actually carries (Monaco skips
       Sprint, etc.), no empty cell because the grid auto-fills. */
    @container (min-width: 700px) {
      .f1-body { grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr); }
      .f1-track { display: flex; }
      .schedule {
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: var(--space-3);
      }
      .session-card { padding: var(--space-2) var(--space-3); }
      .next-countdown { font-size: clamp(2.2em, 12cqmin, 5em); }
      .next-hero .ph-clock-countdown { font-size: clamp(2.2em, 13cqmin, 5em); }
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="f1_next">
      <div class="w-title">
        ${flag ? `<span class="next-flag" aria-hidden="true">${flag}</span>` : `<i class="ph-bold ph-flag" style="color:var(--accent-1)"></i>`}
        <h3>${escapeHtml(data.raceName || "Next Race")}</h3>
        ${data.round ? `<span class="w-title-meta">R${escapeHtml(String(data.round))}</span>` : ""}
      </div>
      <div class="w-body">
        <div class="f1-body">
          <div class="f1-data">
            <div class="next-hero">
              <i class="ph-bold ph-clock-countdown"></i>
              <div class="next-hero-lockup">
                <span class="next-countdown">${escapeHtml(countdownLabel)}</span>
                ${subBits ? `<span class="next-sub"><i class="ph-bold ph-map-pin"></i>${escapeHtml(subBits)}</span>` : ""}
              </div>
            </div>
            ${sessionCards ? `<div class="schedule">${sessionCards}</div>` : ""}
          </div>
          ${track ? `<div class="f1-track">${trackSvg(track, { stroke: "var(--accent-1)" })}</div>` : ""}
        </div>
      </div>
    </div>`;
}
