// calendar_week — Spectra timetable archetype, seven-column variant.
// One hour-axis on the left, then seven day lanes side-by-side. Events
// plot inside their day's lane at their start time with height by
// duration. Today's column header takes the accent-4 tint.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const DEFAULT_RANGE = { start: 7, end: 22 };
const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const DOW_SUN = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function parseTime(iso) {
  if (typeof iso !== "string" || !iso.includes("T")) return null;
  const [h, m] = iso.split("T")[1].slice(0, 5).split(":").map(Number);
  if (Number.isNaN(h) || Number.isNaN(m)) return null;
  return h + m / 60;
}

function fmtHm(iso) {
  if (typeof iso !== "string" || !iso.includes("T")) return "";
  return iso.split("T")[1].slice(0, 5);
}

function computeRange(days) {
  let lo = DEFAULT_RANGE.start;
  let hi = DEFAULT_RANGE.end;
  for (const d of days) {
    for (const ev of d.events || []) {
      if (ev.all_day) continue;
      const s = parseTime(ev.start);
      if (s != null) lo = Math.min(lo, Math.floor(s));
      const e = parseTime(ev.end) ?? (s != null ? s + 1 : null);
      if (e != null) hi = Math.max(hi, Math.ceil(e));
    }
  }
  return { start: Math.max(0, lo), end: Math.min(24, hi) };
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="calendar_week">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Week</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const days = Array.isArray(data.days) ? data.days : [];
  const range = computeRange(days);
  const span = Math.max(1, range.end - range.start);
  const dowNames = (data.week_start === "sunday") ? DOW_SUN : DOW;

  const hourLabels = [];
  for (let h = range.start; h <= range.end; h++) {
    hourLabels.push(`<span>${String(h).padStart(2, "0")}</span>`);
  }

  // 7 column heads
  const heads = days.slice(0, 7).map((d, i) => {
    const name = dowNames[i] || "";
    const isToday = !!d.is_today;
    return `<div class="tt-col-head ${isToday ? "is-today" : ""}">${escapeHtml(name)} ${escapeHtml(String(d.day || ""))}</div>`;
  }).join("");

  // 7 lanes with event blocks. Week view runs at .85em base size so
  // titles fit a narrow column; the feed-colour tint is applied via
  // --tt-bg so blocks read as coloured slots rather than blending
  // into the sunken banding.
  const lanes = days.slice(0, 7).map((d) => {
    const events = (d.events || []).filter((e) => !e.all_day);
    const blocks = events.map((ev) => {
      const s = parseTime(ev.start);
      if (s == null) return "";
      const e = parseTime(ev.end) ?? s + 1;
      const top = Math.max(0, ((s - range.start) / span) * 100);
      const height = Math.max(2, ((e - s) / span) * 100);
      const colour = ev.colour || "var(--accent-4)";
      const tint = `color-mix(in oklab, ${colour} 28%, var(--surface))`;
      const time = `${fmtHm(ev.start)}${ev.end ? `–${fmtHm(ev.end)}` : ""}`;
      return `
        <div class="tt-event" style="top:${top.toFixed(2)}%;height:${height.toFixed(2)}%;border-left-color:${colour};--tt-bg:${tint};font-size:.85em" title="${escapeHtml(time)} ${escapeHtml(ev.summary || "")}">
          <span class="tt-name">${escapeHtml(ev.summary || "")}</span>
        </div>`;
    }).join("");
    return `<div class="tt-lane is-banded">${blocks}</div>`;
  }).join("");

  const totalEvents = days.reduce((n, d) => n + (d.events || []).length, 0);

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="calendar_week">
      <div class="w-title">
        <i class="ph-bold ph-calendar" style="color:var(--accent-3)"></i>
        <h3>This week</h3>
        <span class="w-title-meta">${totalEvents} EVENT${totalEvents === 1 ? "" : "S"}</span>
      </div>
      <div class="w-body tt-body" style="--tt-hours:${span}">
        <div class="tt is-week" style="grid-template-rows:auto 1fr">
          <div></div>
          ${heads}
          <div class="tt-hours">${hourLabels.join("")}</div>
          ${lanes}
        </div>
      </div>
    </div>`;
}
