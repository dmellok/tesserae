// calendar_month — six visual directions for the month grid (M1–M6).
//
// Direction is picked via the per-cell ``variant`` option. Each look is
// inspired by the Bauhaus / Swiss handoff:
//
//   m1  Bauhaus Refined   — display month + accent rule + event bars
//   m2  Bauhaus Geometric — colour-field header + shape dots in cells
//   m3  Swiss / Intl      — hairlines only, today coloured numeral
//   m4  Agenda Split      — mini-grid + upcoming list (md+ only)
//   m5  Editorial         — serif numerals + italic events + double rule
//   m6  Dot Density       — bordered cards + dot per event
//
// Real iCal feeds don't carry the design's 4-category enum, so the per
// event colour comes straight from the feed's configured colour (via
// the ``--chip`` CSS custom property).

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const WEEK_HEADERS = {
  monday: ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"],
  sunday: ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"],
};

const ROMAN = [
  "", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
  "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX",
  "XX", "XXI", "XXII", "XXIII", "XXIV", "XXV", "XXVI", "XXVII",
  "XXVIII", "XXIX", "XXX",
];
function toRoman(n) {
  if (n < 31) return ROMAN[n] || String(n);
  // Just for the year, simple thousands/hundreds break-down.
  const map = [
    ["M", 1000], ["CM", 900], ["D", 500], ["CD", 400],
    ["C", 100], ["XC", 90], ["L", 50], ["XL", 40],
    ["X", 10], ["IX", 9], ["V", 5], ["IV", 4], ["I", 1],
  ];
  let out = "", v = n;
  for (const [sym, val] of map) {
    while (v >= val) { out += sym; v -= val; }
  }
  return out;
}

function fmtTime(iso) {
  if (!iso || !iso.includes("T")) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

function headerBits(data) {
  const name = data.month_name || "";
  const year = data.year || new Date().getFullYear();
  return {
    monthName: name,
    monthUpper: name.toUpperCase(),
    year,
    yearShort: `’${String(year).slice(-2)}`,
    yearRoman: toRoman(year),
  };
}

// ============================================================
// M1 — BAUHAUS REFINED
// ============================================================
function renderM1(data, h, headers, days) {
  const head = headers.map((w, i) => `<div class="m1-wh${i < 6 ? " m1-wh--sep" : ""}">${w}</div>`).join("");
  const cells = days.map((d, i) => {
    const today = d.is_today;
    const out = !d.in_month;
    const evs = (d.events || []).slice(0, 3).map((e) => `
      <div class="m1-ev" style="--chip:${escapeHtml(e.colour || "var(--c-accent)")}">
        <span class="m1-ev-stripe" aria-hidden="true"></span>
        <span class="m1-ev-title">${escapeHtml(e.summary || "")}</span>
      </div>
    `).join("");
    const extra = d.extra > 0 ? `<div class="m1-more">+${d.extra}</div>` : "";
    const colSep = (i % 7) < 6 ? " m1-cell--rsep" : "";
    const rowSep = i < 35 ? " m1-cell--bsep" : "";
    return `
      <div class="m1-cell${colSep}${rowSep}${today ? " m1-cell--today" : ""}${out ? " m1-cell--out" : ""}">
        <span class="m1-num">${d.day}</span>
        <div class="m1-events">${evs}${extra}</div>
      </div>
    `;
  }).join("");
  return `
    <div class="variant variant-m1">
      <header class="m1-header">
        <span class="m1-title">${escapeHtml(h.monthUpper)} <span class="m1-year">${h.year}</span></span>
        <span class="m1-meta">WEEK STARTS ${headers[0]}</span>
      </header>
      <div class="m1-accent" aria-hidden="true"></div>
      <div class="m1-weekhead">${head}</div>
      <div class="m1-grid">${cells}</div>
    </div>
  `;
}

// ============================================================
// M2 — BAUHAUS GEOMETRIC
// ============================================================
function renderM2(data, h, headers, days) {
  const head = headers.map((w) => `<div class="m2-wh">${w}</div>`).join("");
  const cells = days.map((d) => {
    const today = d.is_today;
    const out = !d.in_month;
    const dots = (d.events || []).map((e) => `<span class="m2-dot" style="background:${escapeHtml(e.colour || "var(--c-accent)")}"></span>`).join("");
    return `
      <div class="m2-cell${today ? " m2-cell--today" : ""}${out ? " m2-cell--out" : ""}">
        <span class="m2-num">${d.day}</span>
        <div class="m2-dots">${dots}</div>
      </div>
    `;
  }).join("");
  return `
    <div class="variant variant-m2">
      <header class="m2-header">
        <div class="m2-month-block">${escapeHtml(h.monthUpper)}</div>
        <div class="m2-year-block">${h.year}</div>
      </header>
      <div class="m2-weekhead">${head}</div>
      <div class="m2-grid">${cells}</div>
    </div>
  `;
}

// ============================================================
// M3 — SWISS / INTERNATIONAL
// ============================================================
function renderM3(data, h, headers, days) {
  const head = headers.map((w) => `<div class="m3-wh">${w[0]}${w[1] ? w[1].toLowerCase() : ""}${w[2] ? w[2].toLowerCase() : ""}</div>`).join("");
  const cells = days.map((d) => {
    const today = d.is_today;
    const out = !d.in_month;
    const evs = (d.events || []).slice(0, 2).map((e) => `
      <div class="m3-ev">
        <span class="m3-ev-dot" style="background:${escapeHtml(e.colour || "var(--c-accent)")}"></span>
        <span class="m3-ev-title">${escapeHtml(e.summary || "")}</span>
      </div>
    `).join("");
    const extra = d.extra > 0 ? `<div class="m3-more">+${d.extra} more</div>` : "";
    return `
      <div class="m3-cell${today ? " m3-cell--today" : ""}${out ? " m3-cell--out" : ""}">
        <span class="m3-num">${d.day}</span>
        <div class="m3-events">${evs}${extra}</div>
      </div>
    `;
  }).join("");
  return `
    <div class="variant variant-m3">
      <header class="m3-header">
        <span class="m3-title">${escapeHtml(h.monthName)}</span>
        <span class="m3-year">${h.year}</span>
      </header>
      <div class="m3-rule"></div>
      <div class="m3-weekhead">${head}</div>
      <div class="m3-grid">${cells}</div>
    </div>
  `;
}

// ============================================================
// M4 — AGENDA SPLIT
// ============================================================
function renderM4(data, h, headers, days) {
  // mini grid
  const head = headers.map((w) => `<div class="m4-wh">${w[0]}</div>`).join("");
  const cells = days.map((d) => {
    const today = d.is_today;
    const out = !d.in_month;
    const dots = (d.events || []).slice(0, 3).map((e) => `<span class="m4-dot" style="background:${today ? "var(--c-bg)" : escapeHtml(e.colour || "var(--c-accent)")}"></span>`).join("");
    return `
      <div class="m4-mini${today ? " m4-mini--today" : ""}${out ? " m4-mini--out" : ""}">
        <span class="m4-num">${d.day}</span>
        <div class="m4-mini-dots">${dots}</div>
      </div>
    `;
  }).join("");
  // upcoming list — current month days with events, today onwards
  const todayKey = (() => {
    const t = days.find((d) => d.is_today);
    return t ? t.date : null;
  })();
  const upcoming = days
    .filter((d) => d.in_month && (d.events || []).length && (!todayKey || d.date >= todayKey))
    .slice(0, 6);
  const dayShort = (iso) => {
    const dt = new Date(iso + "T00:00:00");
    return Number.isNaN(dt.getTime()) ? "" : dt.toLocaleDateString([], { weekday: "short" }).toUpperCase();
  };
  const list = upcoming.map((d) => {
    const evs = (d.events || []).slice(0, 2).map((e) => `
      <div class="m4-row" style="--chip:${escapeHtml(e.colour || "var(--c-accent)")}">
        <span class="m4-row-stripe" aria-hidden="true"></span>
        <span class="m4-row-time">${escapeHtml(e.all_day ? "ALL" : fmtTime(e.start))}</span>
        <span class="m4-row-title">${escapeHtml(e.summary || "")}</span>
      </div>
    `).join("");
    return `
      <div class="m4-day">
        <div class="m4-day-head">
          <span class="m4-day-num${d.is_today ? " m4-day-num--today" : ""}">${d.day}</span>
          <span class="m4-day-dow">${dayShort(d.date)}</span>
        </div>
        ${evs}
      </div>
    `;
  }).join("");
  return `
    <div class="variant variant-m4">
      <aside class="m4-left">
        <div class="m4-title">${escapeHtml(h.monthUpper)} ${h.yearShort}</div>
        <div class="m4-weekhead">${head}</div>
        <div class="m4-grid">${cells}</div>
      </aside>
      <section class="m4-right">
        <div class="m4-eyebrow">UPCOMING</div>
        <div class="m4-list">${list || '<div class="m4-empty">No events this month.</div>'}</div>
      </section>
    </div>
  `;
}

// ============================================================
// M5 — EDITORIAL
// ============================================================
function renderM5(data, h, headers, days) {
  const head = headers.map((w) => `<div class="m5-wh">${w}</div>`).join("");
  const cells = days.map((d, i) => {
    const today = d.is_today;
    const out = !d.in_month;
    const evs = (d.events || []).slice(0, 2).map((e) => `
      <div class="m5-ev">${escapeHtml(e.summary || "")}</div>
    `).join("");
    const extra = d.extra > 0 ? `<div class="m5-more">+${d.extra}</div>` : "";
    const colSep = (i % 7) < 6 ? " m5-cell--rsep" : "";
    return `
      <div class="m5-cell${colSep}${today ? " m5-cell--today" : ""}${out ? " m5-cell--out" : ""}">
        <span class="m5-num">${d.day}</span>
        <div class="m5-events">${evs}${extra}</div>
      </div>
    `;
  }).join("");
  return `
    <div class="variant variant-m5">
      <header class="m5-header">
        <span class="m5-title">${escapeHtml(h.monthName)}</span>
        <span class="m5-roman">${h.yearRoman}</span>
      </header>
      <div class="m5-rules">
        <div class="m5-rule m5-rule--thick"></div>
        <div class="m5-rule m5-rule--thin"></div>
      </div>
      <div class="m5-weekhead">${head}</div>
      <div class="m5-grid">${cells}</div>
    </div>
  `;
}

// ============================================================
// M6 — DOT DENSITY
// ============================================================
function renderM6(data, h, headers, days) {
  const head = headers.map((w) => `<div class="m6-wh">${w}</div>`).join("");
  const cells = days.map((d) => {
    const today = d.is_today;
    const out = !d.in_month;
    const dots = (d.events || []).map((e) => `<span class="m6-dot" style="background:${escapeHtml(e.colour || "var(--c-accent)")}"></span>`).join("");
    return `
      <div class="m6-card${today ? " m6-card--today" : ""}${out ? " m6-card--out" : ""}">
        <span class="m6-num">${d.day}</span>
        <div class="m6-dots">${dots}</div>
      </div>
    `;
  }).join("");
  return `
    <div class="variant variant-m6">
      <header class="m6-header">
        <span class="m6-title">${escapeHtml(h.monthUpper)} ${h.year}</span>
      </header>
      <div class="m6-weekhead">${head}</div>
      <div class="m6-grid">${cells}</div>
    </div>
  `;
}

const VARIANTS = {
  m1: renderM1, m2: renderM2, m3: renderM3,
  m4: renderM4, m5: renderM5, m6: renderM6,
};

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = `
      <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
      <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
      <link rel="stylesheet" href="/plugins/calendar_month/client.css">
      <div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>
    `;
    return;
  }
  const size = ctx.cell.size;
  const variant = (ctx.cell.options && ctx.cell.options.variant) || "m1";
  const headers = WEEK_HEADERS[data.week_start] || WEEK_HEADERS.monday;
  const days = Array.isArray(data.days) ? data.days : [];
  const h = headerBits(data);
  const renderer = VARIANTS[variant] || renderM1;

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/plugins/calendar_month/client.css">
    <div class="root size-${size} variant-host variant-host--${variant}">
      ${renderer(data, h, headers, days)}
    </div>
  `;
}
