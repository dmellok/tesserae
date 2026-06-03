// calendar_week — six visual directions for the current 7-day window.
//
//   w1  Bauhaus Refined   — bold header + accent rule + per-day column
//   w2  Bauhaus Geometric — De Stijl blocks, heavy grid, big day numerals
//   w3  Swiss / Intl      — hairlines, minimal column gaps, today in red
//   w4  Timeline Grid     — shared hour axis across all 7 days
//   w5  Editorial         — serif "The Week", double rule
//   w6  Glanceable Cards  — bordered card per day, today inverted
//
// Like calendar_day, each event's colour comes from its feed; the
// design's 4-category enum (work/personal/focus/important) maps to
// "whatever colour the feed admin chose". Variant is per-cell.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

function eventStartMin(ev) {
  const d = new Date(ev.start);
  if (Number.isNaN(d.getTime())) return null;
  return d.getHours() * 60 + d.getMinutes();
}

function dowShort(weekday) {
  return ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"][weekday] || "";
}

function fmtRange(startISO, endISO) {
  if (!startISO || !endISO) return "";
  const s = new Date(startISO + "T00:00:00");
  const e = new Date(endISO + "T00:00:00");
  const monS = s.toLocaleDateString([], { month: "short" }).toUpperCase();
  const monE = e.toLocaleDateString([], { month: "short" }).toUpperCase();
  if (monS === monE) return `${monS} ${s.getDate()} → ${e.getDate()} · ${s.getFullYear()}`;
  return `${monS} ${s.getDate()} → ${monE} ${e.getDate()} · ${s.getFullYear()}`;
}

function isoWeekNumber(dateISO) {
  if (!dateISO) return null;
  const d = new Date(dateISO + "T00:00:00Z");
  const dn = (d.getUTCDay() + 6) % 7;
  d.setUTCDate(d.getUTCDate() - dn + 3);
  const first = new Date(Date.UTC(d.getUTCFullYear(), 0, 4));
  const diff = (d - first) / 86400000;
  return 1 + Math.round((diff - 3 + ((first.getUTCDay() + 6) % 7)) / 7);
}

function dayBits(day) {
  return {
    date: day.day,
    dow: dowShort(day.weekday),
    today: !!day.is_today,
    weekend: day.weekday === 5 || day.weekday === 6,
    events: (day.events || []).map((e) => ({
      tm: e.all_day ? "ALL DAY" : fmtTime(e.start),
      sm: eventStartMin(e),
      summary: e.summary || "(untitled)",
      colour: e.colour || "var(--c-accent)",
      all_day: !!e.all_day,
    })),
  };
}

// ============================================================
// W1 — BAUHAUS REFINED
// ============================================================
function renderW1(data, days, range) {
  const cols = days
    .map((d) => {
      const events = d.events
        .map(
          (e) => `
          <div class="w1-ev" style="--chip:${escapeHtml(e.colour)}">
            <span class="w1-stripe" aria-hidden="true"></span>
            <div class="w1-ev-body">
              <div class="w1-ev-time">${escapeHtml(e.tm)}</div>
              <div class="w1-ev-title">${escapeHtml(e.summary)}</div>
            </div>
          </div>
        `,
        )
        .join("");
      return `
        <div class="w1-col${d.weekend ? " w1-col--weekend" : ""}${d.today ? " w1-col--today" : ""}">
          <div class="w1-col-head">
            <span class="w1-dow">${d.dow}</span>
            <span class="w1-date">${d.date}</span>
          </div>
          <div class="w1-col-body">${events}</div>
        </div>
      `;
    })
    .join("");
  return `
    <div class="variant variant-w1">
      <header class="w1-header">
        <span class="w1-title">THIS WEEK</span>
        <span class="w1-range">${escapeHtml(range)}</span>
      </header>
      <div class="w1-rule"></div>
      <div class="w1-grid">${cols}</div>
    </div>
  `;
}

// ============================================================
// W2 — BAUHAUS GEOMETRIC
// ============================================================
function renderW2(data, days, range, weekNo) {
  const cols = days
    .map((d) => {
      const events = d.events
        .map(
          (e) => `
          <div class="w2-ev">
            <span class="w2-mark" style="background:${escapeHtml(e.colour)}" aria-hidden="true"></span>
            <div class="w2-ev-body">
              <div class="w2-ev-time">${escapeHtml(e.tm)}</div>
              <div class="w2-ev-title">${escapeHtml(e.summary)}</div>
            </div>
          </div>
        `,
        )
        .join("");
      return `
        <div class="w2-col">
          <div class="w2-col-head${d.today ? " w2-col-head--today" : ""}">
            <span class="w2-dow">${d.dow}</span>
            <span class="w2-date">${d.date}</span>
          </div>
          <div class="w2-col-body">${events}</div>
        </div>
      `;
    })
    .join("");
  return `
    <div class="variant variant-w2">
      <header class="w2-header">
        <div class="w2-week-tile">WEEK ${weekNo ?? ""}</div>
        <div class="w2-band">${escapeHtml(range)}</div>
      </header>
      <div class="w2-grid">${cols}</div>
    </div>
  `;
}

// ============================================================
// W3 — SWISS / INTERNATIONAL
// ============================================================
function renderW3(data, days, range) {
  const cols = days
    .map((d) => {
      const events = d.events
        .map(
          (e) => `
          <div class="w3-ev">
            <div class="w3-ev-time">${escapeHtml(e.tm)}</div>
            <div class="w3-ev-title">${escapeHtml(e.summary)}</div>
          </div>
        `,
        )
        .join("");
      return `
        <div class="w3-col">
          <div class="w3-col-head">
            <span class="w3-date${d.today ? " w3-date--today" : ""}">${d.date}</span>
            <span class="w3-dow">${d.dow}</span>
          </div>
          <div class="w3-col-body">${events}</div>
        </div>
      `;
    })
    .join("");
  return `
    <div class="variant variant-w3">
      <header class="w3-header">
        <span class="w3-eyebrow">Week of</span>
        <span class="w3-range">${escapeHtml(range)}</span>
      </header>
      <div class="w3-rule"></div>
      <div class="w3-grid">${cols}</div>
    </div>
  `;
}

// ============================================================
// W4 — TIMELINE GRID
// ============================================================
function renderW4(data, days, range) {
  const allMins = days.flatMap((d) => d.events.map((e) => e.sm).filter((m) => m != null));
  let startMin = allMins.length ? Math.min(...allMins) : 8 * 60;
  let endMin = allMins.length ? Math.max(...allMins) + 60 : 18 * 60;
  startMin = Math.max(0, Math.floor((startMin - 60) / 60) * 60);
  endMin = Math.min(24 * 60, Math.ceil((endMin + 60) / 60) * 60);
  const span = Math.max(60, endMin - startMin);
  const yPct = (m) => `${((m - startMin) / span) * 100}%`;

  const gridHours = [];
  for (let m = startMin; m <= endMin; m += 120) gridHours.push(m);
  const grid = gridHours
    .map(
      (m) => `
      <div class="w4-hour" style="top:${yPct(m)}">
        <span class="w4-hour-label">${String(Math.floor(m / 60)).padStart(2, "0")}:00</span>
        <div class="w4-hour-line"></div>
      </div>
    `,
    )
    .join("");

  const nowDate = new Date();
  const nowMin = nowDate.getHours() * 60 + nowDate.getMinutes();

  const colHeaders = days
    .map(
      (d) => `
      <div class="w4-col-head${d.today ? " w4-col-head--today" : ""}">
        <span class="w4-dow">${d.dow}</span>
        <span class="w4-date">${d.date}</span>
      </div>
    `,
    )
    .join("");

  const cols = days
    .map((d) => {
      const events = d.events
        .filter((e) => e.sm != null)
        .map(
          (e) => `
          <div class="w4-ev" style="top:${yPct(e.sm)};--chip:${escapeHtml(e.colour)}">
            <div class="w4-ev-title">${escapeHtml(e.summary)}</div>
          </div>
        `,
        )
        .join("");
      const nowLine =
        d.today && nowMin >= startMin && nowMin <= endMin
          ? `
            <div class="w4-now" style="top:${yPct(nowMin)}">
              <span class="w4-now-dot"></span>
              <div class="w4-now-line"></div>
            </div>
          `
          : "";
      return `<div class="w4-col">${events}${nowLine}</div>`;
    })
    .join("");

  return `
    <div class="variant variant-w4">
      <header class="w4-header">
        <span class="w4-title">THIS WEEK</span>
        <span class="w4-range">${escapeHtml(range)}</span>
      </header>
      <div class="w4-day-headers">${colHeaders}</div>
      <div class="w4-rail">
        ${grid}
        <div class="w4-cols">${cols}</div>
      </div>
    </div>
  `;
}

// ============================================================
// W5 — EDITORIAL
// ============================================================
function renderW5(data, days, range) {
  const cols = days
    .map((d) => {
      const events = d.events
        .map(
          (e) => `
          <div class="w5-ev">
            <span class="w5-ev-time">${escapeHtml(e.tm)}</span>
            <span class="w5-ev-title">${escapeHtml(e.summary)}</span>
          </div>
        `,
        )
        .join("");
      return `
        <div class="w5-col">
          <div class="w5-col-head">
            <span class="w5-date${d.today ? " w5-date--today" : ""}">${d.date}</span>
            <span class="w5-dow">${d.dow}</span>
          </div>
          <div class="w5-col-body">${events}</div>
        </div>
      `;
    })
    .join("");
  return `
    <div class="variant variant-w5">
      <header class="w5-header">
        <span class="w5-title">The Week</span>
        <span class="w5-range">${escapeHtml(range)}</span>
      </header>
      <div class="w5-rules">
        <div class="w5-rule w5-rule--thick"></div>
        <div class="w5-rule w5-rule--thin"></div>
      </div>
      <div class="w5-grid">${cols}</div>
    </div>
  `;
}

// ============================================================
// W6 — GLANCEABLE CARDS
// ============================================================
function renderW6(data, days, range, weekNo) {
  const cols = days
    .map((d) => {
      const events = d.events
        .slice(0, 4)
        .map(
          (e) => `
          <div class="w6-ev" style="--chip:${escapeHtml(e.colour)}">
            <span class="w6-mark" aria-hidden="true"></span>
            <span class="w6-ev-title">${escapeHtml(e.summary)}</span>
          </div>
        `,
        )
        .join("");
      return `
        <div class="w6-card${d.today ? " w6-card--today" : d.weekend ? " w6-card--weekend" : ""}">
          <div class="w6-card-head">
            <span class="w6-dow">${d.dow}</span>
            <span class="w6-date">${d.date}</span>
          </div>
          <div class="w6-card-body">${events}</div>
        </div>
      `;
    })
    .join("");
  return `
    <div class="variant variant-w6">
      <header class="w6-header">
        <span class="w6-title">${escapeHtml(range)}</span>
        <span class="w6-meta">WEEK ${weekNo ?? ""}</span>
      </header>
      <div class="w6-grid">${cols}</div>
    </div>
  `;
}

const VARIANTS = {
  w1: renderW1, w2: renderW2, w3: renderW3,
  w4: renderW4, w5: renderW5, w6: renderW6,
};

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = `
      <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
      <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
      <link rel="stylesheet" href="/plugins/calendar_week/client.css">
      <div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>
    `;
    return;
  }
  const size = ctx.cell.size;
  const variant = (ctx.cell.options && ctx.cell.options.variant) || "w1";
  const days = (data.days || []).map(dayBits);
  const range = fmtRange(data.start, data.end);
  const weekNo = isoWeekNumber(data.start);
  const renderer = VARIANTS[variant] || renderW1;
  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/plugins/calendar_week/client.css">
    <div class="root size-${size} variant-host variant-host--${variant}">
      ${renderer(data, days, range, weekNo)}
    </div>
  `;
}
