// calendar_week — Spectra timetable, seven columns. Display header
// shows the date range ("JUN 1 → 7 · 2026"), columns show DOW + day
// number with today tinted accent-1. Same auto-fit hour axis as the
// day view so a tightly-clustered work week reads at the right scale.

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

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
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

  const heads = days.map((d, i) => {
    const name = dowNames[i] || "";
    const isToday = !!d.is_today;
    return `
      <div class="tt-col-head ${isToday ? "is-today" : ""}">
        <span>${escapeHtml(name)}</span>
        <span class="day-num">${escapeHtml(String(d.day || ""))}</span>
      </div>`;
  }).join("");

  const lanes = days.map((d) => {
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
    return `<div class="tt-lane has-rule">${blocks}</div>`;
  }).join("");

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="calendar_week">
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
