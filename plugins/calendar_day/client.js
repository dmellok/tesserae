// calendar_day — Spectra timetable. Big display header (WEEKDAY +
// day number, with the month/year as right-aligned meta + a thin
// accent rule), sparse 2-hour axis, events as positioned blocks
// auto-fit to the day's actual range with one hour of padding.
//
// The y-position of a block already encodes when the event happens,
// so the title is the primary content; the time range sits below it
// in monospace-feeling tabular numbers when there's room.

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

function parseTime(iso) {
  if (typeof iso !== "string" || !iso.includes("T")) return null;
  const [h, m] = iso.split("T")[1].slice(0, 5).split(":").map(Number);
  if (Number.isNaN(h) || Number.isNaN(m)) return null;
  return h + m / 60;
}

function fmtHm(iso) {
  if (typeof iso !== "string" || !iso.includes("T")) return "—";
  return iso.split("T")[1].slice(0, 5);
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

// Render the left-axis labels at every even hour (08:00, 10:00, ...).
// Fewer labels = calmer chart; the 2-hour spacing matches the
// reference design's rhythm.
function hourLabels(range) {
  const out = [];
  for (let h = range.start; h <= range.end; h++) {
    if (h % 2 === 0) out.push(`<span>${String(h).padStart(2, "0")}:00</span>`);
    else out.push(`<span style="opacity:0"></span>`);
  }
  return out.join("");
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

  // Parse today's date — composer hydrates data.date as YYYY-MM-DD.
  const isoDate = data.date || new Date().toISOString().slice(0, 10);
  const [y, m, d] = isoDate.split("-").map(Number);
  const localDate = new Date(y, m - 1, d);
  const dayNum = String(d);
  const monthName = (MONTH_FULL[m - 1] || "").toUpperCase();
  // Date#getDay returns 0 = Sunday; shift to ISO weekday (0 = Monday).
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
    return `
      <div class="tt-event" style="top:${top.toFixed(2)}%;height:${height.toFixed(2)}%;border-left-color:${colour};--tt-bg:${tint}" title="${escapeHtml(time)} ${escapeHtml(ev.summary || "")}">
        <span class="tt-name">${escapeHtml(ev.summary || "")}</span>
        ${showSub ? `<span class="tt-sub">${escapeHtml(time)}</span>` : ""}
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

  // "Now" line — only render if the local time falls inside the
  // visible hour range AND the page is showing today's date (the
  // composer hydrates data.date as YYYY-MM-DD).
  const now = new Date();
  const todayIso = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  const nowH = now.getHours() + now.getMinutes() / 60;
  const showNow = isoDate === todayIso && nowH >= range.start && nowH <= range.end;
  const nowPct = showNow ? ((nowH - range.start) / span) * 100 : 0;
  const nowLine = showNow
    ? `<div class="tt-now" style="top:${nowPct.toFixed(2)}%"></div>`
    : "";

  shadow.innerHTML = `
    ${css}
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
