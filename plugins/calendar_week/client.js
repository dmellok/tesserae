// calendar_week, Spectra timetable, seven columns. Display header
// shows the date range ("JUN 1 → 7 · 2026"), columns show DOW + day
// number with today picked out via an inverse accent-1 chip and
// weekend columns tinted to read distinct from weekdays. Each
// column head also carries a small event-count chip so a glance
// answers "which day is the busiest?" without scrolling the lane.
//
// Same per-text-type sizing/spacing/label controls as calendar_day /
// calendar_three / calendar_schedule: event title/location scale,
// event row spacing, header/axis label scale, dashboard title scale,
// configurable day_start_hour/day_end_hour (or "always show the whole
// day"), a show_location toggle + rendering (absent from the bundled
// widget), and date_label_style (short/minimal weekday+month
// abbreviations).

const MONTH_SHORT = [
  "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
  "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
];
const DOW = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];

// date_label_style cell option: "short" (default, current 3-letter
// behaviour), "minimal" (1-2 chars, just enough to stay unambiguous), or
// "full" (the whole word, derived from the short code since that's all
// this widget stores).
const DOW_MINIMAL = { SUN: "SU", MON: "M", TUE: "TU", WED: "W", THU: "TH", FRI: "F", SAT: "SA" };
const MONTH_MINIMAL = { JAN: "JA", FEB: "F", MAR: "MR", APR: "AP", MAY: "MY", JUN: "JN", JUL: "JL", AUG: "AU", SEP: "S", OCT: "O", NOV: "N", DEC: "D" };
const DOW_FULL = { SUN: "Sunday", MON: "Monday", TUE: "Tuesday", WED: "Wednesday", THU: "Thursday", FRI: "Friday", SAT: "Saturday" };
const MONTH_FULL = { JAN: "January", FEB: "February", MAR: "March", APR: "April", MAY: "May", JUN: "June", JUL: "July", AUG: "August", SEP: "September", OCT: "October", NOV: "November", DEC: "December" };

export function styleShortLabel(label, style, minimalMap, fullMap) {
  if (style === "minimal") return minimalMap[label] || label.slice(0, 2);
  if (style === "full") return (fullMap && fullMap[label]) || label;
  return label;
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Clamps a cell-option slider value, defaulting on missing/non-numeric
// input. Same shape as the other calendar_* widgets' clampScale.
export function clampScale(raw, def, lo, hi) {
  const v = raw === null || raw === undefined || raw === "" ? def : Number(raw);
  return Number.isFinite(v) ? Math.max(lo, Math.min(hi, v)) : def;
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

// day_start_hour / day_end_hour cell options override either edge
// (-1 = keep auto-fitting that edge); set both to 0/24 to always show
// the whole day regardless of events.
export function computeRange(days, startOverride = -1, endOverride = -1) {
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
  const range = has
    ? { start: Math.max(0, Math.floor(lo) - 1), end: Math.min(24, Math.ceil(hi) + 1) }
    : { start: 8, end: 18 };
  if (startOverride >= 0) range.start = Math.min(24, startOverride);
  if (endOverride >= 0) range.end = Math.min(24, endOverride);
  if (range.end <= range.start) range.end = Math.min(24, range.start + 1);
  return range;
}

function hourLabels(range) {
  const out = [];
  for (let h = range.start; h <= range.end; h++) {
    if (h % 2 === 0) out.push(`<span>${String(h).padStart(2, "0")}:00</span>`);
    else out.push(`<span style="opacity:0"></span>`);
  }
  return out.join("");
}

function fmtRange(startIso, endIso, labelStyle) {
  if (!startIso || !endIso) return "";
  const [sy, sm, sd] = startIso.split("-").map(Number);
  const [ey, em, ed] = endIso.split("-").map(Number);
  const startMonth = styleShortLabel(MONTH_SHORT[sm - 1] || "", labelStyle, MONTH_MINIMAL, MONTH_FULL);
  const endMonth = styleShortLabel(MONTH_SHORT[em - 1] || "", labelStyle, MONTH_MINIMAL, MONTH_FULL);
  const startBit = `${startMonth} ${sd}`;
  const endBit = (sm === em) ? `${ed}` : `${endMonth} ${ed}`;
  const year = sy === ey ? `${sy}` : `${sy}/${ey}`;
  return `${startBit} → ${endBit} · ${year}`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const opts = ctx?.cell?.options || {};
  const hideLabels = opts.hide_labels === true;
  const titleScale = clampScale(opts.event_title_scale, 1.0, 0.01, 10.0);
  const locScale = clampScale(opts.event_location_scale, 1.0, 0.01, 10.0);
  const rowPad = clampScale(opts.event_row_padding_em, 0.0, 0.0, 3.0);
  const headerScale = clampScale(opts.header_scale, 1.0, 0.01, 10.0);
  const axisScale = clampScale(opts.axis_label_scale, 1.0, 0.01, 10.0);
  const dashTitleScale = clampScale(opts.title_scale, 1.0, 0.01, 10.0);
  const showLocations = opts.show_location === true;
  const labelStyle = ["short", "minimal", "full"].includes(opts.date_label_style) ? opts.date_label_style : "short";
  const startHour = clampScale(opts.day_start_hour, -1, -1, 24);
  const endHour = clampScale(opts.day_end_hour, -1, -1, 24);
  const styleAttr = `--tt-title-scale:${titleScale};--tt-loc-scale:${locScale};--tt-row-pad:${rowPad}em;--tt-header-scale:${headerScale};--tt-axis-scale:${axisScale};--tt-dash-title-scale:${dashTitleScale};`;
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
  const range = computeRange(days, startHour, endHour);
  const span = Math.max(1, range.end - range.start);
  const rangeMeta = fmtRange(data.start, data.end, labelStyle);

  const heads = days.map((d) => {
    const name = styleShortLabel(DOW[d.weekday] || "", labelStyle, DOW_MINIMAL, DOW_FULL);
    const isToday = !!d.is_today;
    const isWeekend = d.weekday === 5 || d.weekday === 6;
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

  // ponytail: all-day events render as same-day pills per column; a
  // multi-day event shows once per day it overlaps (server already
  // expands it into every covered day's bucket) rather than as a
  // single strip spanning columns. Add column-spanning rendering if
  // that turns out to matter in practice.
  const alldayRow = days.map((d) => {
    const allDayEvents = (d.events || []).filter((e) => e.all_day);
    if (!allDayEvents.length) return `<div class="tt-allday"></div>`;
    const pills = allDayEvents.map((ev) => {
      const colour = ev.colour || "var(--accent-4)";
      const tint = `color-mix(in oklab, ${colour} 28%, var(--surface))`;
      return `<span class="tt-allday-pill" style="border-left-color:${colour};--tt-bg:${tint}">${escapeHtml(ev.summary || "")}</span>`;
    }).join("");
    return `<div class="tt-allday">${pills}</div>`;
  }).join("");
  const hasAllDay = days.some((d) => (d.events || []).some((e) => e.all_day));

  const lanes = days.map((d) => {
    const isWeekend = d.weekday === 5 || d.weekday === 6;
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
      const location = ev.location || "";
      const showLocation = showLocations && location && height > 8;
      return `
        <div class="tt-event ${isTiny ? "is-tiny" : ""}" style="top:${top.toFixed(2)}%;height:${height.toFixed(2)}%;border-left-color:${colour};--tt-bg:${tint}" title="${escapeHtml(time)} ${escapeHtml(ev.summary || "")}${location ? ` · ${escapeHtml(location)}` : ""}">
          <span class="tt-name">${escapeHtml(ev.summary || "")}</span>
          ${showLocation ? `<span class="tt-loc"><i class="ph-bold ph-map-pin"></i>${escapeHtml(location)}</span>` : ""}
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
    .tt-col-head.is-today { color: var(--accent-1); }
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
    .tt-col-head {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 0.15em;
      padding-bottom: var(--space-1);
    }
    .tt-col-dow {
      font-size: calc(var(--fs-caption) * var(--tt-header-scale, 1));
      font-weight: var(--fw-black);
      letter-spacing: var(--ls-label);
      text-transform: var(--label-transform, uppercase);
      color: var(--text-muted);
    }
    .tt-col-head.is-today .tt-col-dow { color: var(--accent-1); }
    .tt-col-day {
      font-size: calc(var(--fs-body) * var(--tt-header-scale, 1));
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
    @container (max-width: 360px) {
      .tt-col-count { display: none; }
    }
    /* event_row_padding_em adds on top of the fixed 2px base, so 0
       (default) reproduces the previous fixed spacing. */
    .tt-event {
      padding-top: calc(2px + var(--tt-row-pad, 0em));
      padding-bottom: calc(2px + var(--tt-row-pad, 0em));
      gap: 0;
    }
    .tt-event .tt-name {
      font-size: calc(var(--fs-caption) * 0.88 * var(--tt-title-scale, 1));
      line-height: 1.05;
      font-weight: var(--fw-black);
      display: -webkit-box;
      -webkit-line-clamp: 3;
      line-clamp: 3;
      -webkit-box-orient: vertical;
      white-space: normal;
      overflow: hidden;
      word-break: break-word;
      hyphens: auto;
    }
    .tt-event.is-tiny .tt-name { display: none; }
    .tt-event.is-tiny { padding-top: 0; padding-bottom: 0; }

    /* Location row, off by default (show_location cell option). */
    .tt-event .tt-loc {
      display: inline-flex;
      align-items: center;
      gap: 0.2em;
      font-size: calc(var(--fs-caption) * 0.75 * var(--tt-loc-scale, 1));
      font-weight: var(--fw-bold);
      color: var(--text-muted);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 100%;
    }
    .tt-event .tt-loc .ph-bold { font-size: 0.9em; flex: 0 0 auto; }

    [data-hide-labels="true"] .tt-event .tt-name { display: none; }
    [data-hide-labels="true"] .tt-event .tt-loc { display: none; }
    [data-hide-labels="true"] .tt-event {
      padding-top: 0;
      padding-bottom: 0;
    }

    /* spectra-widgets.css's shared .tt-allday-pill has a fixed
       font-size; scope the title-scale override here. */
    .tt-allday-pill { font-size: calc(var(--fs-caption) * var(--tt-title-scale, 1)); }

    /* header_scale: the day column headers.
       axis_label_scale: the hour gutter labels.
       title_scale: the "THIS WEEK" dashboard title. */
    .tt-hours { font-size: calc(var(--fs-caption) * var(--tt-axis-scale, 1)); }
    .cal-head-title { font-size: calc(1em * var(--tt-dash-title-scale, 1)); }
    .cal-head-meta { font-size: calc(1em * var(--tt-dash-title-scale, 1)); }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="calendar_week" data-hide-labels="${hideLabels}" style="${styleAttr}">
      <div class="w-body" style="gap:var(--space-3)">
        <div class="cal-head">
          <div class="cal-head-row">
            <span class="cal-head-title">THIS WEEK</span>
            <span class="cal-head-meta">${escapeHtml(rangeMeta)}</span>
          </div>
          <div class="cal-head-rule"></div>
        </div>
        <div class="tt-body" style="--tt-hours:${span};flex:1 1 auto;min-height:0;display:flex;flex-direction:column">
          <div class="tt is-week" style="flex:1 1 auto;min-height:0;grid-template-rows:${hasAllDay ? "auto auto 1fr" : "auto 1fr"}">
            <div></div>
            ${heads}
            ${hasAllDay ? `<div></div>${alldayRow}` : ""}
            <div class="tt-hours">${hourLabels(range)}</div>
            ${lanes}
          </div>
        </div>
      </div>
    </div>`;
}
