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

const MONTH_FULL = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];
const WEEKDAY_FULL = [
  "Monday", "Tuesday", "Wednesday", "Thursday",
  "Friday", "Saturday", "Sunday",
];

// date_label_style cell option: "short" (default, current 3-letter
// behaviour), "minimal" (1-2 chars, just enough to stay unambiguous), or
// "full" (the whole word). Base label is always the *full* localised
// name, short/minimal are derived from it. Labels keep their natural
// casing here; display casing belongs to CSS (--label-transform), so
// sentence-case styles like Editorial stay sentence-case.
const DOW_MINIMAL = { Monday: "M", Tuesday: "Tu", Wednesday: "W", Thursday: "Th", Friday: "F", Saturday: "Sa", Sunday: "Su" };
const MONTH_MINIMAL = { January: "Ja", February: "F", March: "Mr", April: "Ap", May: "My", June: "Jn", July: "Jl", August: "Au", September: "S", October: "O", November: "N", December: "D" };

export function styleLabel(full, style, minimalMap) {
  if (style === "minimal") return minimalMap[full] || full.slice(0, 2);
  if (style === "short") return full.slice(0, 3);
  return full;
}

// locales contract (docs/widgets.md#locales-strings): English keeps the
// hardcoded arrays above (so styleLabel's minimal map, keyed by the
// English full name, still resolves); any other locale asks Intl for
// the real localised name instead of a hand-rolled weekday/month table.
export function localizedFull(date, unit, locale) {
  const lang = (locale || "en").split("-")[0];
  if (lang === "en") {
    return unit === "weekday" ? WEEKDAY_FULL[(date.getDay() + 6) % 7] : MONTH_FULL[date.getMonth()];
  }
  return new Intl.DateTimeFormat(locale, { [unit]: "long" }).format(date);
}

export function minimalMapFor(unit, locale) {
  const lang = (locale || "en").split("-")[0];
  if (lang !== "en") return {};
  return unit === "weekday" ? DOW_MINIMAL : MONTH_MINIMAL;
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

// A timed (non-all-day) event's end can land on a different local
// calendar day than its start (an overnight event, or a genuinely
// multi-day timed block like a trip). Subtracting raw hour-of-day
// across two different dates is meaningless — it previously produced
// negative/near-zero heights and corrupted computeRange's auto-fit
// window for the whole visible week. Treat "ends on a later day" as
// "runs to the bottom of this day" instead. Used for the week-wide
// auto-fit range only, where a coarse "runs to midnight" is enough —
// see clampToDay for the per-day-copy rendering split.
function eventEndHour(ev, s) {
  const e = parseTime(ev.end);
  if (e == null) return s != null ? s + 1 : null;
  if (s == null) return e;
  const sameDay = new Date(ev.start).toDateString() === new Date(ev.end).toDateString();
  return sameDay ? e : 24;
}

function localDateKey(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// The server repeats a multi-day timed event's original start/end on
// every covered day's bucket (so each day's own copy carries the true
// event times for the tooltip). Rendering a middle/end-day copy at the
// *original* start hour is wrong — e.g. a Sun 16:00 → Fri 10:00 trip
// would redraw a 16:00-start block on Wednesday too. Clamp per day:
// only the actual start day keeps the real start hour (else 00:00),
// and only the actual end day keeps the real end hour (else 24:00).
export function clampToDay(ev, dayDate) {
  const s = parseTime(ev.start);
  if (s == null) return null;
  const startKey = localDateKey(new Date(ev.start));
  const endDate = ev.end ? new Date(ev.end) : null;
  const endKey = endDate ? localDateKey(endDate) : startKey;
  const isStartDay = !dayDate || dayDate === startKey;
  const isEndDay = !dayDate || dayDate === endKey;
  const top = isStartDay ? s : 0;
  let bottom = isEndDay ? (endDate ? parseTime(ev.end) : top + 1) : 24;
  if (bottom == null || bottom <= top) bottom = Math.min(24, top + 1);
  return { s: top, e: bottom };
}

// Converts an [s, e) hour-space block into a top/height percentage pair
// against the visible [rangeStart, rangeStart + spanHours] lane, clamping
// each edge independently into [0,100] *before* deriving height. A
// pass-through day of a multi-day event spans the full 0-24h logical day
// (clampToDay), which routinely runs past a narrowed
// day_start_hour/day_end_hour window — deriving height from the unclamped
// span first would let it exceed 100% and, since nothing upstream clips
// overflow, spill the block out past the lane's bottom edge instead of
// stopping at it.
export function pctSpan(s, e, rangeStart, spanHours) {
  const rawTop = ((s - rangeStart) / spanHours) * 100;
  const rawBottom = ((e - rangeStart) / spanHours) * 100;
  const top = Math.max(0, Math.min(rawTop, 100));
  const bottom = Math.max(0, Math.min(rawBottom, 100));
  return { top, height: Math.max(2, bottom - top) };
}

// Groups an all-day event's per-day copies (identical start/end/summary
// on each, per the server-side spread) back into a single {ev, first,
// last} bar spanning the day-column indices it covers.
function collectAllDayBars(days) {
  const byKey = new Map();
  days.forEach((d, i) => {
    (d.events || []).filter((e) => e.all_day).forEach((ev) => {
      const key = `${ev.start}|${ev.end}|${ev.summary}`;
      const bar = byKey.get(key);
      if (bar) bar.last = i;
      else byKey.set(key, { ev, first: i, last: i });
    });
  });
  return [...byKey.values()];
}

// Greedy interval-packing so overlapping bars stack into separate lanes
// instead of colliding when two all-day events cover the same day(s).
function packAllDayLanes(bars) {
  const laneEnds = [];
  return bars
    .sort((a, b) => a.first - b.first)
    .map((bar) => {
      let lane = laneEnds.findIndex((end) => end < bar.first);
      if (lane === -1) { lane = laneEnds.length; laneEnds.push(bar.last); }
      else laneEnds[lane] = bar.last;
      return { ...bar, lane };
    });
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
      const e = eventEndHour(ev, s);
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

function fmtRange(startIso, endIso, labelStyle, locale) {
  if (!startIso || !endIso) return "";
  const [sy, sm, sd] = startIso.split("-").map(Number);
  const [ey, em, ed] = endIso.split("-").map(Number);
  const monthMinimalMap = minimalMapFor("month", locale);
  const startMonth = styleLabel(localizedFull(new Date(sy, sm - 1, sd), "month", locale), labelStyle, monthMinimalMap);
  const endMonth = styleLabel(localizedFull(new Date(ey, em - 1, ed), "month", locale), labelStyle, monthMinimalMap);
  const startBit = `${startMonth} ${sd}`;
  const endBit = (sm === em) ? `${ed}` : `${endMonth} ${ed}`;
  const year = sy === ey ? `${sy}` : `${sy}/${ey}`;
  return `${startBit} → ${endBit} · ${year}`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const opts = ctx?.cell?.options || {};
  const locale = ctx?.locale || "en";
  const t = ctx?.t || ((key, fallback) => fallback ?? key);
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
  const rangeMeta = fmtRange(data.start, data.end, labelStyle, locale);

  const dowMinimalMap = minimalMapFor("weekday", locale);
  const heads = days.map((d) => {
    const [dy, dm, dd] = (d.date || "").split("-").map(Number);
    const dowFull = Number.isFinite(dy) ? localizedFull(new Date(dy, dm - 1, dd), "weekday", locale) : "";
    const name = dowFull ? styleLabel(dowFull, labelStyle, dowMinimalMap) : "";
    const isToday = !!d.is_today;
    const isWeekend = d.weekday === 5 || d.weekday === 6;
    const count = Array.isArray(d.events) ? d.events.length : 0;
    const noun = t(count === 1 ? "event" : "events", count === 1 ? "event" : "events");
    const classes = ["tt-col-head"];
    if (isToday) classes.push("is-today");
    if (isWeekend) classes.push("is-weekend");
    return `
      <div class="${classes.join(" ")}">
        <span class="tt-col-dow">${escapeHtml(name)}</span>
        <span class="tt-col-day">${escapeHtml(String(d.day || ""))}</span>
        ${count > 0 ? `<span class="tt-col-count" title="${count} ${noun}">${count}</span>` : ""}
      </div>`;
  }).join("");

  const now = new Date();
  const nowH = now.getHours() + now.getMinutes() / 60;
  const showNow = nowH >= range.start && nowH <= range.end;
  const nowPct = showNow ? ((nowH - range.start) / span) * 100 : 0;

  // The server spreads a multi-day all-day event across every covered
  // day's bucket (same start/end/summary on each copy) so today's view
  // always includes it. Group those copies back into one bar spanning
  // the covered day columns instead of repeating the label per day.
  const allDayBars = packAllDayLanes(collectAllDayBars(days));
  // grid-column:2/-1 (day columns only, no hour-gutter track) + a plain
  // repeat(N,1fr) here is deliberate: this wrapper is a *nested* grid,
  // sized independently of the outer .tt.is-week grid. An "auto" gutter
  // column here has no content to size itself against and collapses to
  // 0, which widened every day column and shifted every pill left of
  // its real day. Dropping the gutter track and starting at the outer
  // grid's day-column 2 makes this grid's tracks land on the exact same
  // pixels as the header/lane day columns above and below it.
  const alldayRow = allDayBars.length
    ? `<div class="tt-allday-row" style="grid-column:2/-1;display:grid;grid-template-columns:repeat(${days.length}, 1fr);gap:var(--space-1)">${allDayBars.map((bar) => {
        const colour = bar.ev.colour || "var(--accent-4)";
        const tint = `color-mix(in oklab, ${colour} 28%, var(--surface))`;
        return `<span class="tt-allday-pill" style="grid-column:${bar.first + 1} / span ${bar.last - bar.first + 1};grid-row:${bar.lane + 1};border-left-color:${colour};--tt-bg:${tint}">${escapeHtml(bar.ev.summary || "")}</span>`;
      }).join("")}</div>`
    : "";
  const hasAllDay = allDayBars.length > 0;

  const lanes = days.map((d) => {
    const isWeekend = d.weekday === 5 || d.weekday === 6;
    const isToday = !!d.is_today;
    const events = (d.events || []).filter((e) => !e.all_day);
    const blocks = events.map((ev) => {
      const clamped = clampToDay(ev, d.date);
      if (clamped == null) return "";
      const { s, e } = clamped;
      const { top, height } = pctSpan(s, e, range.start, span);
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
            <span class="cal-head-title">${escapeHtml(t("this_week", "THIS WEEK"))}</span>
            <span class="cal-head-meta">${escapeHtml(rangeMeta)}</span>
          </div>
          <div class="cal-head-rule"></div>
        </div>
        <div class="tt-body" style="--tt-hours:${span};flex:1 1 auto;min-height:0;display:flex;flex-direction:column">
          <div class="tt is-week" style="flex:1 1 auto;min-height:0;grid-template-rows:${hasAllDay ? "auto auto 1fr" : "auto 1fr"}">
            <div></div>
            ${heads}
            ${alldayRow}
            <div class="tt-hours">${hourLabels(range)}</div>
            ${lanes}
          </div>
        </div>
      </div>
    </div>`;
}
