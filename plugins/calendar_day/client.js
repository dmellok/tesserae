// calendar_day — Spectra timetable archetype. Vertical hour axis on
// the left, events plotted as positioned blocks in the lane at their
// start time with height proportional to duration. All-day events
// stack as pills in a strip above the lane. Feed colours drive the
// left bar; the body of each block uses --surface so the colour
// reads as a tag rather than an overpowering fill.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const DEFAULT_RANGE = { start: 6, end: 23 };

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

// Compute the hour window that covers every timed event, clamped to a
// reasonable awake-hours range so a single 03:00 event doesn't drag the
// whole grid open.
function computeRange(timed) {
  if (!timed.length) return DEFAULT_RANGE;
  let lo = DEFAULT_RANGE.start;
  let hi = DEFAULT_RANGE.end;
  for (const ev of timed) {
    const s = parseTime(ev.start);
    if (s != null) lo = Math.min(lo, Math.floor(s));
    const e = parseTime(ev.end) ?? (s != null ? s + 1 : null);
    if (e != null) hi = Math.max(hi, Math.ceil(e));
  }
  // Cap so we never show more than 24 hours of slack.
  return { start: Math.max(0, lo), end: Math.min(24, hi) };
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="calendar_day">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Today</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const events = Array.isArray(data.events) ? data.events : [];
  const allDay = events.filter((e) => e.all_day);
  const timed = events.filter((e) => !e.all_day);

  const range = computeRange(timed);
  const span = Math.max(1, range.end - range.start);

  // Build hour labels (one per hour, plus the closing edge).
  const hourLabels = [];
  for (let h = range.start; h <= range.end; h++) {
    hourLabels.push(`<span>${String(h).padStart(2, "0")}</span>`);
  }

  // Position each event by % of the visible range.
  const eventBlocks = timed.map((ev) => {
    const s = parseTime(ev.start);
    if (s == null) return "";
    const e = parseTime(ev.end) ?? s + 1;
    const top = Math.max(0, ((s - range.start) / span) * 100);
    const height = Math.max(2, ((e - s) / span) * 100);
    const colour = ev.colour || "var(--accent-4)";
    const time = `${fmtHm(ev.start)}${ev.end ? `–${fmtHm(ev.end)}` : ""}`;
    return `
      <div class="tt-event" style="top:${top.toFixed(2)}%;height:${height.toFixed(2)}%;border-left-color:${colour}">
        <span class="tt-time">${escapeHtml(time)}</span>
        <span class="tt-name">${escapeHtml(ev.summary || "")}</span>
      </div>`;
  }).join("");

  const allDayStrip = allDay.length
    ? `<div class="tt-allday">${allDay.map((ev) => {
        const colour = ev.colour || "var(--accent-4)";
        return `<span class="tt-allday-pill" style="border-left-color:${colour}">${escapeHtml(ev.summary || "")}</span>`;
      }).join("")}</div>`
    : "";

  const emptyHint = events.length === 0
    ? `<div style="position:absolute;inset:0;display:grid;place-items:center"><p class="u-muted">No events today.</p></div>`
    : "";

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="calendar_day">
      <div class="w-title">
        <i class="ph-bold ph-calendar-check" style="color:var(--accent-3)"></i>
        <h3>Today</h3>
        <span class="w-title-meta">${events.length} EVENT${events.length === 1 ? "" : "S"}</span>
      </div>
      <div class="w-body tt-body" style="--tt-hours:${span}">
        ${allDayStrip}
        <div class="tt">
          <div class="tt-hours">${hourLabels.join("")}</div>
          <div class="tt-lane is-banded">
            ${eventBlocks}
            ${emptyHint}
          </div>
        </div>
      </div>
    </div>`;
}
