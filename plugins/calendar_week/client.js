// calendar_week, Spectra timetable, seven columns. Display header
// shows the date range ("JUN 1 → 7 · 2026"), columns show DOW + day
// number with today picked out via an inverse accent-1 chip and
// weekend columns tinted to read distinct from weekdays. Each
// column head also carries a small event-count chip so a glance
// answers "which day is the busiest?" without scrolling the lane.

const MONTH_SHORT = [
  "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
  "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
];
const DOW = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];
const DOW_SUN = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Parse the ISO timestamp through Date so the resulting hour is in
// the renderer's LOCAL timezone, not whatever offset is baked into
// the ISO string. calendar_core's server normalises everything to
// UTC ISO; the previous string-slicing path was treating "14:00
// Melbourne → 04:00 UTC" as "render at hour 4", landing every event
// 10 hours from its true local time.
function parseTime(iso) {
  if (typeof iso !== "string") return null;
  const d = new Date(iso);
  if (!Number.isFinite(d.getTime())) return null;
  return d.getHours() + d.getMinutes() / 60;
}

function fmtHm(iso) {
  if (typeof iso !== "string") return "";
  const d = new Date(iso);
  if (!Number.isFinite(d.getTime())) return "";
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function computeRange(days) {
  let lo = 24, hi = 0, has = false;
  for (const d of days) {
    for (const ev of d.events || []) {
      if (ev.all_day) continue;
      const s = parseTime(ev.start);
      if (s != null) { lo = Math.min(lo, s); has = true; }
      const e = parseTime(ev.end) ?? (s != null ? s + 1 : null);
      if (e != null) { hi = Math.max(hi, e); has = true; }
    }
  }
  if (!has) return { start: 8, end: 18 };
  return {
    start: Math.max(0, Math.floor(lo) - 1),
    end: Math.min(24, Math.ceil(hi) + 1),
  };
}

function hourLabels(range) {
  const out = [];
  for (let h = range.start; h <= range.end; h++) {
    if (h % 2 === 0) out.push(`<span>${String(h).padStart(2, "0")}:00</span>`);
    else out.push(`<span style="opacity:0"></span>`);
  }
  return out.join("");
}

function fmtRange(startIso, endIso) {
  if (!startIso || !endIso) return "";
  const [sy, sm, sd] = startIso.split("-").map(Number);
  const [ey, em, ed] = endIso.split("-").map(Number);
  const startBit = `${MONTH_SHORT[sm - 1] || ""} ${sd}`;
  const endBit = (sm === em)
    ? `${ed}`
    : `${MONTH_SHORT[em - 1] || ""} ${ed}`;
  const year = sy === ey ? `${sy}` : `${sy}/${ey}`;
  return `${startBit} → ${endBit} · ${year}`;
}

// Which column indices are weekend days, given the chosen week start.
// Server passes week_start as "monday" (default) or "sunday".
// Monday-first weeks: Saturday is idx 5, Sunday idx 6.
// Sunday-first weeks: Sunday is idx 0, Saturday idx 6.
function weekendIndices(weekStart) {
  return weekStart === "sunday" ? new Set([0, 6]) : new Set([5, 6]);
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const opts = ctx?.cell?.options || {};
  const hideLabels = opts.hide_labels === true;
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="calendar_week">
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const days = Array.isArray(data.days) ? data.days.slice(0, 7) : [];
  const range = computeRange(days);
  const span = Math.max(1, range.end - range.start);
  const dowNames = (data.week_start === "sunday") ? DOW_SUN : DOW;
  const rangeMeta = fmtRange(data.start, data.end);
  const weekendCols = weekendIndices(data.week_start || "monday");

  const heads = days.map((d, i) => {
    const name = dowNames[i] || "";
    const isToday = !!d.is_today;
    const isWeekend = weekendCols.has(i);
    const count = Array.isArray(d.events) ? d.events.length : 0;
    const classes = ["tt-col-head"];
    if (isToday) classes.push("is-today");
    if (isWeekend) classes.push("is-weekend");
    return `
      <div class="${classes.join(" ")}">
        <span class="tt-col-dow">${escapeHtml(name)}</span>
        <span class="tt-col-day">${escapeHtml(String(d.day || ""))}</span>
        ${count > 0 ? `<span class="tt-col-count" title="${count} event${count === 1 ? "" : "s"}">${count}</span>` : ""}
      </div>`;
  }).join("");

  const now = new Date();
  const nowH = now.getHours() + now.getMinutes() / 60;
  const showNow = nowH >= range.start && nowH <= range.end;
  const nowPct = showNow ? ((nowH - range.start) / span) * 100 : 0;

  const lanes = days.map((d, i) => {
    const isWeekend = weekendCols.has(i);
    const isToday = !!d.is_today;
    const events = (d.events || []).filter((e) => !e.all_day);
    const blocks = events.map((ev) => {
      const s = parseTime(ev.start);
      if (s == null) return "";
      const e = parseTime(ev.end) ?? s + 1;
      const rawTop = ((s - range.start) / span) * 100;
      const rawHeight = Math.max(2, ((e - s) / span) * 100);
      // Clamp the event's top so a row sitting at the very end of the
      // range (e.g. an event at 24:00) doesn't overflow below the
      // lane. Cap at (100 - height) so the block always ends inside
      // the lane's bottom edge.
      const top = Math.max(0, Math.min(rawTop, 100 - rawHeight));
      const height = rawHeight;
      const colour = ev.colour || "var(--accent-4)";
      const tint = `color-mix(in oklab, ${colour} 28%, var(--surface))`;
      const time = `${fmtHm(ev.start)}${ev.end ? `–${fmtHm(ev.end)}` : ""}`;
      // Mark very short blocks so the CSS can hide the text entirely
      // (an illegible string of letter-tops is worse than a clean bar
      //, the title attr still carries the summary for any browser
      // that surfaces tooltips). Threshold is generous because the
      // lane's height varies; 4% of a typical 12-hour span ~= 24-30 px.
      const isTiny = height < 4;
      return `
        <div class="tt-event ${isTiny ? "is-tiny" : ""}" style="top:${top.toFixed(2)}%;height:${height.toFixed(2)}%;border-left-color:${colour};--tt-bg:${tint}" title="${escapeHtml(time)} ${escapeHtml(ev.summary || "")}">
          <span class="tt-name">${escapeHtml(ev.summary || "")}</span>
        </div>`;
    }).join("");
    const nowLine = (showNow && d.is_today)
      ? `<div class="tt-now" style="top:${nowPct.toFixed(2)}%"></div>`
      : "";
    const classes = ["tt-lane", "has-rule"];
    if (isWeekend) classes.push("is-weekend");
    if (isToday) classes.push("is-today");
    return `<div class="${classes.join(" ")}">${blocks}${nowLine}</div>`;
  }).join("");

  const layout = `
    /* Today marker, inverse-coloured chip instead of just an accent
       shift on the day number, so a quick scan locks on the current
       day. The DOW label drops to on-accent over a tinted backdrop,
       the day number becomes a filled circle. */
    .tt-col-head.is-today {
      color: var(--accent-1);
    }
    .tt-col-head.is-today .tt-col-day {
      background: var(--accent-1);
      color: var(--on-accent);
      width: 1.7em;
      height: 1.7em;
      display: inline-grid;
      place-items: center;
      border-radius: 999px;
      font-weight: var(--fw-black);
    }

    /* Weekend tint, soft sunken background on column heads + lanes
       to set Sat/Sun off from weekdays without yelling. Inset
       background instead of border so the existing grid gutters
       stay clean. */
    .tt-col-head.is-weekend {
      background: color-mix(in oklab, var(--text-primary) 4%, transparent);
    }
    .tt-lane.is-weekend {
      background: color-mix(in oklab, var(--text-primary) 3%, transparent);
    }
    .tt-lane.is-weekend.has-rule {
      background-image: repeating-linear-gradient(
        to bottom,
        transparent 0,
        transparent calc((2 * 100% / var(--tt-hours, 12)) - 1px),
        var(--surface-sunken) calc((2 * 100% / var(--tt-hours, 12)) - 1px),
        var(--surface-sunken) calc(2 * 100% / var(--tt-hours, 12))
      ),
      color-mix(in oklab, var(--text-primary) 3%, transparent);
    }

    /* Per-column event-count chip, small numeric badge under the
       day number. Hidden when count = 0 (handled in JS so the badge
       only renders when there are events). */
    .tt-col-head {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 0.15em;
      padding-bottom: var(--space-1);
    }
    .tt-col-dow {
      font-size: var(--fs-caption);
      font-weight: var(--fw-black);
      letter-spacing: var(--ls-label);
      text-transform: var(--label-transform, uppercase);
      color: var(--text-muted);
    }
    .tt-col-head.is-today .tt-col-dow { color: var(--accent-1); }
    .tt-col-day {
      font-size: var(--fs-body);
      font-weight: var(--fw-bold);
      color: var(--text-primary);
      line-height: 1.1;
    }
    .tt-col-count {
      font-size: var(--fs-caption);
      font-weight: var(--fw-black);
      color: var(--text-muted);
      font-variant-numeric: tabular-nums;
      background: var(--surface-sunken);
      padding: 0 0.4em;
      border-radius: 999px;
      min-width: 1.2em;
      text-align: center;
      line-height: 1.4;
    }
    .tt-col-head.is-today .tt-col-count {
      background: color-mix(in oklab, var(--accent-1) 20%, var(--surface));
      color: var(--accent-1);
    }

    /* xs / sm: drop the count chips to free space for the day-num
       circles. */
    @container (max-width: 360px) {
      .tt-col-count { display: none; }
    }

    /* Event blocks, week view squeezes events into narrow columns,
       so the title needs a tighter line-height + minimal top
       padding to keep all the letters visible inside the block.
       The base .tt-event styles in spectra-widgets.css set
       line-height: 1.1 plus var(--space-1) vertical padding, which
       combined with the small font-size we want here pushed the
       descender row past the overflow boundary, letters were
       clipping at the bottom.

       Title wraps for taller events via line-clamp (up to 3 lines),
       so a 90-min meeting can spell out "Architecture review" rather
       than truncating to "Archit...". Short events still single-line
       ellipsis since they don't have the height to wrap. */
    .tt-event {
      padding-top: 2px;
      padding-bottom: 2px;
      gap: 0;
    }
    .tt-event .tt-name {
      font-size: calc(var(--fs-caption) * 0.88);
      line-height: 1.05;
      font-weight: var(--fw-black);
      /* Multi-line wrap with line-clamp so taller events use their
         vertical room. White-space: normal lets the browser break on
         word boundaries; -webkit-line-clamp caps at 3 lines and the
         remaining lines truncate with ellipsis. */
      display: -webkit-box;
      -webkit-line-clamp: 3;
      line-clamp: 3;
      -webkit-box-orient: vertical;
      white-space: normal;
      overflow: hidden;
      word-break: break-word;
      hyphens: auto;
    }
    /* Tiny event blocks (under ~4% of lane height) drop the text
       and just paint as a coloured bar. An illegible string of
       letter-tops reads worse than a clean coloured strip; the
       title attribute still carries the summary for any browser
       that surfaces tooltips. */
    .tt-event.is-tiny .tt-name { display: none; }
    .tt-event.is-tiny { padding-top: 0; padding-bottom: 0; }

    /* hide_labels cell option, when on, every event block paints as
       its colour bar only (same treatment as is-tiny). Reads as a
       glance-friendly heatmap of feed colours when the dashboard
       cares about "what kind of day is this" more than "what's the
       title of each meeting". */
    [data-hide-labels="true"] .tt-event .tt-name { display: none; }
    [data-hide-labels="true"] .tt-event {
      padding-top: 0;
      padding-bottom: 0;
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="calendar_week" data-hide-labels="${hideLabels}">
      <div class="w-body" style="gap:var(--space-3)">
        <div class="cal-head">
          <div class="cal-head-row">
            <span class="cal-head-title">THIS WEEK</span>
            <span class="cal-head-meta">${escapeHtml(rangeMeta)}</span>
          </div>
          <div class="cal-head-rule"></div>
        </div>
        <div class="tt-body" style="--tt-hours:${span};flex:1 1 auto;min-height:0;display:flex;flex-direction:column">
          <div class="tt is-week" style="flex:1 1 auto;min-height:0;grid-template-rows:auto 1fr">
            <div></div>
            ${heads}
            <div class="tt-hours">${hourLabels(range)}</div>
            ${lanes}
          </div>
        </div>
      </div>
    </div>`;
}
