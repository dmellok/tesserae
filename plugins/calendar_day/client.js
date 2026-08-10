// calendar_day, Spectra timetable. Big display header (WEEKDAY +
// day number, with the month/year as right-aligned meta + a thin
// accent rule), sparse 2-hour axis, events as positioned blocks
// auto-fit to the day's actual range with one hour of padding.
//
// Visual pass additions:
//   - Time-of-day icons in the time gutter at the four canonical
//     transitions (sunrise / midday / sunset / midnight) so the
//     timetable reads as "this is morning / this is evening" before
//     numbers parse.
//   - Event-density strip above the timetable: one segment per hour
//     of the visible range tinted by overlapping-event count. Quick
//     "where is the day stacked" scan.
//   - Location pin + text next to events that carry a location.

const MONTH_FULL = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];
const WEEKDAY_FULL = [
  "Monday", "Tuesday", "Wednesday", "Thursday",
  "Friday", "Saturday", "Sunday",
];

// date_label_style cell option: "full" (default, current behaviour),
// "short" (3-letter), or "minimal" (1-2 chars, just enough to stay
// unambiguous across the 7 weekdays / 12 months).
const DOW_MINIMAL = { Monday: "M", Tuesday: "Tu", Wednesday: "W", Thursday: "Th", Friday: "F", Saturday: "Sa", Sunday: "Su" };
const MONTH_MINIMAL = { January: "Ja", February: "F", March: "Mr", April: "Ap", May: "My", June: "Jn", July: "Jl", August: "Au", September: "S", October: "O", November: "N", December: "D" };

export function styleLabel(full, style, minimalMap) {
  if (style === "minimal") return (minimalMap[full] || full.slice(0, 2)).toUpperCase();
  if (style === "short") return full.slice(0, 3).toUpperCase();
  return full.toUpperCase();
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Clamps a cell-option slider value, defaulting on missing/non-numeric
// input. Ported from server.py's _coerce_scale — server.py used to clamp
// event_title_scale / event_time_scale before sending them down; now the
// raw slider value comes straight through in ctx.cell.options and this
// does the clamping client-side instead.
export function clampScale(raw, def, lo, hi) {
  const v = raw === null || raw === undefined || raw === "" ? def : Number(raw);
  return Number.isFinite(v) ? Math.max(lo, Math.min(hi, v)) : def;
}

// Cell options live at ctx.cell.options (see docs/widgets.md's ctx shape),
// not top-level ctx.options. Named + exported so a regression back to the
// wrong path is a one-line, testable fact instead of silently no-op-ing
// every slider (the bug this shipped with for a while).
export function readOptions(ctx) {
  return ctx?.cell?.options ?? {};
}

// Parse the ISO timestamp through Date so the hour is in the
// renderer's LOCAL timezone, not whatever offset is baked into the
// ISO string. calendar_core normalises events to UTC ISO; the
// previous string-slicing path was treating "14:00 Melbourne → 04:00
// UTC" as "render at hour 4", landing every event 10 hours from its
// true local time on UTC+10 hosts.
function parseTime(iso) {
  if (typeof iso !== "string") return null;
  const d = new Date(iso);
  if (!Number.isFinite(d.getTime())) return null;
  return d.getHours() + d.getMinutes() / 60;
}

function fmtHm(iso) {
  if (typeof iso !== "string") return "-";
  const d = new Date(iso);
  if (!Number.isFinite(d.getTime())) return "-";
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

// A timed (non-all-day) event's end can land on a different local
// calendar day than its start (an overnight event, or a genuinely
// multi-day timed block like a trip). Subtracting raw hour-of-day
// across two different dates is meaningless — it previously produced
// negative/near-zero heights and corrupted computeRange's auto-fit
// window. Treat "ends on a later day" as "runs to the bottom of this
// day" instead.
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

// calendar_core includes an event in "today"'s list whenever it overlaps
// today, even if it started on an earlier day or ends on a later one (an
// overnight block, a multi-day trip). Rendering it at the event's
// *original* start hour is wrong on any day that isn't its real start
// day — e.g. a trip that started yesterday would redraw at yesterday's
// start hour today too. Clamp to the day actually being rendered: only
// the real start day keeps the real start hour (else 00:00), and only
// the real end day keeps the real end hour (else 24:00).
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
// multi-day event on a day other than its own start/end runs the full
// 0-24h logical day (clampToDay), which routinely runs past a narrowed
// day_start_hour/day_end_hour window — deriving height from the
// unclamped span first would let it exceed 100% and, since nothing
// upstream clips overflow, spill the block out past the lane's bottom
// edge instead of stopping at it.
export function pctSpan(s, e, rangeStart, spanHours) {
  const rawTop = ((s - rangeStart) / spanHours) * 100;
  const rawBottom = ((e - rangeStart) / spanHours) * 100;
  const top = Math.max(0, Math.min(rawTop, 100));
  const bottom = Math.max(0, Math.min(rawBottom, 100));
  return { top, height: Math.max(2, bottom - top) };
}

// Auto-fit the hour axis to the actual events with one hour of padding
// each side. Default to a 9 → 18 business window when there are no
// timed events. Clamped to [0, 24].
//
// day_start_hour / day_end_hour cell options override either edge
// (-1 = keep auto-fitting that edge); set both to 0/24 to always show
// the whole day regardless of events.
export function computeRange(timed, startOverride = -1, endOverride = -1) {
  let range;
  if (!timed.length) {
    range = { start: 9, end: 18 };
  } else {
    let lo = 24, hi = 0;
    for (const ev of timed) {
      const s = parseTime(ev.start);
      if (s != null) lo = Math.min(lo, s);
      const e = eventEndHour(ev, s);
      if (e != null) hi = Math.max(hi, e);
    }
    range = {
      start: Math.max(0, Math.floor(lo) - 1),
      end: Math.min(24, Math.ceil(hi) + 1),
    };
  }
  if (startOverride >= 0) range.start = Math.min(24, startOverride);
  if (endOverride >= 0) range.end = Math.min(24, endOverride);
  if (range.end <= range.start) range.end = Math.min(24, range.start + 1);
  return range;
}

// Time-of-day icon for canonical solar transitions. Returns an icon
// definition (Phosphor name + accent token) or null. Picks the same
// h value the timetable's hour gutter renders (integer hour, 0-24).
function todIcon(h) {
  if (h === 0 || h === 24) return { ph: "ph-moon", accent: "var(--text-muted)" };
  if (h === 6) return { ph: "ph-sun-horizon", accent: "var(--accent-2)" };
  if (h === 12) return { ph: "ph-sun", accent: "var(--accent-2)" };
  if (h === 18) return { ph: "ph-sun-horizon", accent: "var(--accent-1)" };
  return null;
}

// Render the left-axis labels at every even hour (08:00, 10:00, ...).
// Canonical sun-cycle hours (00, 06, 12, 18) swap the text label for
// a Phosphor glyph (moon / sunrise / sun / sunset) so the eye reads
// time-of-day without parsing numbers.
function hourLabels(range) {
  const out = [];
  for (let h = range.start; h <= range.end; h++) {
    const icon = todIcon(h);
    if (icon) {
      out.push(
        `<span class="tt-hours-icon"><i class="ph-bold ${icon.ph}" style="color:${icon.accent}"></i></span>`
      );
    } else if (h % 2 === 0) {
      out.push(`<span>${String(h).padStart(2, "0")}:00</span>`);
    } else {
      out.push(`<span style="opacity:0"></span>`);
    }
  }
  return out.join("");
}

// Event-density bar: one cell per hour of the visible range, tinted
// by how many events overlap that hour. 0 events = empty surface-
// sunken; 1+ events scale through accent-4 (teal) at increasing
// opacity. Quick scan of "where the day is busy". Half-open intervals
// [hour, hour+1).
function densityStrip(range, timed) {
  const cells = [];
  const maxCount = Math.max(
    1,
    ...Array.from({ length: range.end - range.start }, (_, i) => {
      const hStart = range.start + i;
      const hEnd = hStart + 1;
      return timed.reduce((acc, ev) => {
        const s = parseTime(ev.start);
        const e = eventEndHour(ev, s);
        if (s == null || e == null) return acc;
        return s < hEnd && e > hStart ? acc + 1 : acc;
      }, 0);
    })
  );
  for (let h = range.start; h < range.end; h++) {
    const count = timed.reduce((acc, ev) => {
      const s = parseTime(ev.start);
      const e = eventEndHour(ev, s);
      if (s == null || e == null) return acc;
      return s < h + 1 && e > h ? acc + 1 : acc;
    }, 0);
    const alpha = count === 0 ? 0 : 0.25 + 0.55 * (count / maxCount);
    const bg = count === 0
      ? "var(--surface-sunken)"
      : `color-mix(in oklab, var(--accent-4) ${(alpha * 100).toFixed(0)}%, var(--surface))`;
    cells.push(
      `<span class="tt-density-cell" style="background:${bg}" title="${count} event${count === 1 ? "" : "s"} at ${String(h).padStart(2, "0")}:00"></span>`
    );
  }
  return `<div class="tt-density" aria-hidden="true">${cells.join("")}</div>`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="calendar_day">
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const options = readOptions(ctx);
  const labelStyle = ["full", "short", "minimal"].includes(options.date_label_style) ? options.date_label_style : "full";

  const isoDate = data.date || new Date().toISOString().slice(0, 10);
  const [y, m, d] = isoDate.split("-").map(Number);
  const localDate = new Date(y, m - 1, d);
  const dayNum = String(d);
  const monthName = styleLabel(MONTH_FULL[m - 1] || "", labelStyle, MONTH_MINIMAL);
  const weekday = styleLabel(WEEKDAY_FULL[(localDate.getDay() + 6) % 7], labelStyle, DOW_MINIMAL);

  const events = Array.isArray(data.events) ? data.events : [];
  const allDay = events.filter((e) => e.all_day);
  const timed = events.filter((e) => !e.all_day);

  const startHour = clampScale(options.day_start_hour, -1, -1, 24);
  const endHour = clampScale(options.day_end_hour, -1, -1, 24);
  const range = computeRange(timed, startHour, endHour);
  const span = Math.max(1, range.end - range.start);
  const showLocations = options.show_location !== false;

  const eventBlocks = timed.map((ev) => {
    const clamped = clampToDay(ev, isoDate);
    if (clamped == null) return "";
    const { s, e } = clamped;
    const { top, height } = pctSpan(s, e, range.start, span);
    const colour = ev.colour || "var(--accent-4)";
    const tint = `color-mix(in oklab, ${colour} 28%, var(--surface))`;
    const time = `${fmtHm(ev.start)}${ev.end ? `–${fmtHm(ev.end)}` : ""}`;
    const showSub = height > 5;
    const location = ev.location || "";
    const showLocation = showLocations && location && height > 8;
    return `
      <div class="tt-event" style="top:${top.toFixed(2)}%;height:${height.toFixed(2)}%;border-left-color:${colour};--tt-bg:${tint}" title="${escapeHtml(time)} ${escapeHtml(ev.summary || "")}${location ? ` · ${escapeHtml(location)}` : ""}">
        <span class="tt-name">${escapeHtml(ev.summary || "")}</span>
        ${showSub ? `<span class="tt-sub">${escapeHtml(time)}</span>` : ""}
        ${showLocation ? `<span class="tt-loc"><i class="ph-bold ph-map-pin"></i>${escapeHtml(location)}</span>` : ""}
      </div>`;
  }).join("");

  // Placed as a grid item of .tt itself (grid-column:2/-1) rather than
  // a full-width sibling above it: a sibling starts at the widget's
  // left edge, offset from the lane below it by the hour gutter's
  // width, so pills didn't line up with the events they sit above.
  // Sharing .tt's own auto/1fr column tracks guarantees the same
  // pixels as the lane, no nested-grid track-matching needed since
  // there's only one lane column in day view.
  const allDayStrip = allDay.length
    ? `<div class="tt-allday" style="grid-column:2/-1">${allDay.map((ev) => {
        const colour = ev.colour || "var(--accent-4)";
        const tint = `color-mix(in oklab, ${colour} 28%, var(--surface))`;
        return `<span class="tt-allday-pill" style="border-left-color:${colour};--tt-bg:${tint}">${escapeHtml(ev.summary || "")}</span>`;
      }).join("")}</div>`
    : "";

  const emptyHint = events.length === 0
    ? `<div style="position:absolute;inset:0;display:grid;place-items:center"><p class="u-muted">No events today.</p></div>`
    : "";

  const now = new Date();
  const todayIso = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  const nowH = now.getHours() + now.getMinutes() / 60;
  const showNow = isoDate === todayIso && nowH >= range.start && nowH <= range.end;
  const nowPct = showNow ? ((nowH - range.start) / span) * 100 : 0;
  const nowLine = showNow
    ? `<div class="tt-now" style="top:${nowPct.toFixed(2)}%"></div>`
    : "";

  const showDensity = options.show_density !== false;
  const density = (showDensity && timed.length) ? densityStrip(range, timed) : "";

  const titleScale = clampScale(options.event_title_scale, 1.0, 0.01, 10.0);
  const timeScale = clampScale(options.event_time_scale, 1.0, 0.01, 10.0);
  const locScale = clampScale(options.event_location_scale, 1.0, 0.01, 10.0);
  const rowPad = clampScale(options.event_row_padding_em, 0.0, 0.0, 3.0);
  const headerScale = clampScale(options.header_scale, 1.0, 0.01, 10.0);
  const axisScale = clampScale(options.axis_label_scale, 1.0, 0.01, 10.0);
  const styleAttr = `--tt-title-scale:${titleScale};--tt-time-scale:${timeScale};--tt-loc-scale:${locScale};--tt-row-pad:${rowPad}em;--tt-header-scale:${headerScale};--tt-axis-scale:${axisScale};`;

  // Inline layout for the visual-pass additions (density strip, sun-
  // cycle icons in the hour gutter, location pin row inside events).
  const layout = `
    /* Time-of-day icon swap in the hour gutter, keeps the existing
       column layout (one row per hour) and just replaces the text
       label with a glyph at the canonical solar transitions. */
    .tt-hours-icon {
      display: inline-flex;
      align-items: center;
      justify-content: flex-end;
      font-size: 1.05em;
      line-height: 1;
    }
    .tt-hours-icon .ph-bold { line-height: 1; }

    /* Event-density strip, slim row of per-hour cells above the
       lane. Sits in the timetable's column-1 / row-1 slot above the
       hour gutter so its cell widths align with the lane width. */
    .tt-density {
      display: grid;
      grid-template-columns: auto 1fr;
      gap: 0;
      align-items: stretch;
      margin-bottom: var(--space-1);
    }
    .tt-density {
      display: flex;
      gap: var(--stroke-1);
      width: 100%;
      height: var(--stroke-4, 6px);
      padding-left: 0;
      align-self: stretch;
    }
    .tt-density-cell {
      flex: 1 1 0;
      display: block;
    }

    /* Location row inside event blocks, pin glyph + location text,
       truncates with ellipsis when the block is narrow. */
    .tt-event .tt-loc {
      display: inline-flex;
      align-items: center;
      gap: 0.25em;
      font-size: calc(var(--fs-caption) * var(--tt-loc-scale, 1));
      font-weight: var(--fw-bold);
      color: var(--text-muted);
      letter-spacing: var(--ls-label);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 100%;
    }
    .tt-event .tt-loc .ph-bold {
      font-size: 0.95em;
      color: var(--text-muted);
      flex: 0 0 auto;
    }

    /* xs / sm: drop the density strip + location row to keep the
       lane uncluttered on tight cells. */
    @container (max-width: 360px) {
      .tt-density { display: none; }
      .tt-event .tt-loc { display: none; }
    }

    /* User-tunable event text sizing (event_title_scale /
       event_time_scale / event_location_scale cell options),
       multiplies the base spectra-widgets.css font-size so 1.0
       matches the previous fixed size exactly. */
    .tt-event .tt-name { font-size: calc(var(--fs-body) * var(--tt-title-scale, 1)); }
    .tt-event .tt-sub { font-size: calc(var(--fs-caption) * var(--tt-time-scale, 1)); }
    /* spectra-widgets.css's shared .tt-allday-pill has a fixed font-size
       that event_title_scale never reached; scope the override here so
       all-day events resize with the same slider as timed events. */
    .tt-allday-pill { font-size: calc(var(--fs-caption) * var(--tt-title-scale, 1)); }
    /* The shared .tt-allday is a wrapping flex *row*, so pills sized to
       their own text and stopped well short of the lane's right edge —
       an all-day event read as a small tag rather than a bar covering
       the day it applies to. Day view has exactly one lane, so stack
       pills vertically and let each stretch it end to end.
       calendar_week/-three-day don't need this: their own
       .tt-allday-row grid already spans each pill across its day
       columns via an explicit grid-column span. */
    .tt-allday { flex-direction: column; align-items: stretch; }
    /* event_row_padding_em adds on top of the shared spectra-widgets.css
       padding, so 0 (default) reproduces the previous fixed spacing. */
    .tt-event { padding: calc(var(--space-1) + var(--tt-row-pad, 0em)) var(--space-2); }

    /* header_scale: the "WEEKDAY 12" / "MONTH YYYY" head row.
       axis_label_scale: the 08:00/10:00.../sun-icon hour gutter labels.
       Both override shared spectra-widgets.css fixed sizes, scoped to
       this widget instance only. */
    .cal-head-title { font-size: calc(1em * var(--tt-header-scale, 1)); }
    .cal-head-meta { font-size: calc(1em * var(--tt-header-scale, 1)); }
    .tt-hours { font-size: calc(var(--fs-caption) * var(--tt-axis-scale, 1)); }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="calendar_day" style="${styleAttr}">
      <div class="w-body" style="gap:var(--space-3)">
        <div class="cal-head">
          <div class="cal-head-row">
            <span class="cal-head-title">${escapeHtml(weekday)} <span class="num">${escapeHtml(dayNum)}</span></span>
            <span class="cal-head-meta">${escapeHtml(monthName)} ${escapeHtml(String(y))}</span>
          </div>
          <div class="cal-head-rule"></div>
        </div>
        ${density}
        <div class="tt-body" style="--tt-hours:${span};flex:1 1 auto;min-height:0;display:flex;flex-direction:column">
          <div class="tt" style="flex:1 1 auto;min-height:0;grid-template-rows:${allDay.length ? "auto 1fr" : "1fr"}">
            ${allDayStrip}
            <div class="tt-hours">${hourLabels(range)}</div>
            <div class="tt-lane has-rule">
              ${eventBlocks}
              ${nowLine}
              ${emptyHint}
            </div>
          </div>
        </div>
      </div>
    </div>`;
}
