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

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
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

// Auto-fit the hour axis to the actual events with one hour of padding
// each side. Default to a 9 → 18 business window when there are no
// timed events. Clamped to [0, 24].
function computeRange(timed) {
  if (!timed.length) return { start: 9, end: 18 };
  let lo = 24, hi = 0;
  for (const ev of timed) {
    const s = parseTime(ev.start);
    if (s != null) lo = Math.min(lo, s);
    const e = parseTime(ev.end) ?? (s != null ? s + 1 : null);
    if (e != null) hi = Math.max(hi, e);
  }
  return {
    start: Math.max(0, Math.floor(lo) - 1),
    end: Math.min(24, Math.ceil(hi) + 1),
  };
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
        const e = parseTime(ev.end) ?? (s != null ? s + 1 : null);
        if (s == null || e == null) return acc;
        return s < hEnd && e > hStart ? acc + 1 : acc;
      }, 0);
    })
  );
  for (let h = range.start; h < range.end; h++) {
    const count = timed.reduce((acc, ev) => {
      const s = parseTime(ev.start);
      const e = parseTime(ev.end) ?? (s != null ? s + 1 : null);
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

  const isoDate = data.date || new Date().toISOString().slice(0, 10);
  const [y, m, d] = isoDate.split("-").map(Number);
  const localDate = new Date(y, m - 1, d);
  const dayNum = String(d);
  const monthName = (MONTH_FULL[m - 1] || "").toUpperCase();
  const weekday = WEEKDAY_FULL[(localDate.getDay() + 6) % 7].toUpperCase();

  const events = Array.isArray(data.events) ? data.events : [];
  const allDay = events.filter((e) => e.all_day);
  const timed = events.filter((e) => !e.all_day);

  const range = computeRange(timed);
  const span = Math.max(1, range.end - range.start);

  const eventBlocks = timed.map((ev) => {
    const s = parseTime(ev.start);
    if (s == null) return "";
    const e = parseTime(ev.end) ?? s + 1;
    const top = Math.max(0, ((s - range.start) / span) * 100);
    const height = Math.max(2, ((e - s) / span) * 100);
    const colour = ev.colour || "var(--accent-4)";
    const tint = `color-mix(in oklab, ${colour} 28%, var(--surface))`;
    const time = `${fmtHm(ev.start)}${ev.end ? `–${fmtHm(ev.end)}` : ""}`;
    const showSub = height > 5;
    const location = ev.location || "";
    const showLocation = location && height > 8;
    return `
      <div class="tt-event" style="top:${top.toFixed(2)}%;height:${height.toFixed(2)}%;border-left-color:${colour};--tt-bg:${tint}" title="${escapeHtml(time)} ${escapeHtml(ev.summary || "")}${location ? ` · ${escapeHtml(location)}` : ""}">
        <span class="tt-name">${escapeHtml(ev.summary || "")}</span>
        ${showSub ? `<span class="tt-sub">${escapeHtml(time)}</span>` : ""}
        ${showLocation ? `<span class="tt-loc"><i class="ph-bold ph-map-pin"></i>${escapeHtml(location)}</span>` : ""}
      </div>`;
  }).join("");

  const allDayStrip = allDay.length
    ? `<div class="tt-allday">${allDay.map((ev) => {
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

  const density = timed.length ? densityStrip(range, timed) : "";

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
      font-size: var(--fs-caption);
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
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="calendar_day">
      <div class="w-body" style="gap:var(--space-3)">
        <div class="cal-head">
          <div class="cal-head-row">
            <span class="cal-head-title">${escapeHtml(weekday)} <span class="num">${escapeHtml(dayNum)}</span></span>
            <span class="cal-head-meta">${escapeHtml(monthName)} ${escapeHtml(String(y))}</span>
          </div>
          <div class="cal-head-rule"></div>
        </div>
        ${allDayStrip}
        ${density}
        <div class="tt-body" style="--tt-hours:${span};flex:1 1 auto;min-height:0;display:flex;flex-direction:column">
          <div class="tt" style="flex:1 1 auto;min-height:0">
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
