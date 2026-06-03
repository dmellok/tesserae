// calendar_day — six visual directions for today's agenda (D1–D6).
//
// Direction is picked via the per-cell ``variant`` option. The 6 looks
// come straight from the Bauhaus / Swiss design handoff:
//
//   d1  Bauhaus Refined   — display date + accent rule + striped rows
//   d2  Bauhaus Geometric — De Stijl colour fields, shape markers
//   d3  Swiss / Intl      — Helvetica-style hairlines, tabular times
//   d4  Timeline Rail     — proportional time axis with NOW line
//   d5  Editorial Almanac — serif numerals, italic NOW
//   d6  Glanceable Cards  — 2×2 grid of bold cards, live = filled
//
// Real iCal feeds don't carry the design's 4-category enum
// (work/personal/focus/important). Each feed has a single configured
// colour, so we use that as the stripe / marker / fill colour directly
// — every variant looks coherent without forcing categorisation.

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

function startMs(e) {
  const d = new Date(e.start);
  return Number.isNaN(d.getTime()) ? null : d.getTime();
}
function endMs(e) {
  const d = new Date(e.end || e.start);
  return Number.isNaN(d.getTime()) ? null : d.getTime();
}

function liveIndex(events, nowMs) {
  return events.findIndex((e) => {
    const s = startMs(e), n = endMs(e);
    return s != null && n != null && nowMs >= s && nowMs < n;
  });
}

function nextUpcomingIndex(events, nowMs, skip) {
  for (let i = 0; i < events.length; i++) {
    if (i === skip) continue;
    const s = startMs(e_(events, i));
    if (s != null && s > nowMs) return i;
  }
  return -1;
}
const e_ = (events, i) => events[i];

// Common header bits computed once per render so each variant just
// pulls what it needs.
function headerBits(data) {
  const nowDate = data.now ? new Date(data.now) : new Date();
  const dom = nowDate.getDate();
  const dow3 = nowDate.toLocaleDateString([], { weekday: "short" }).toUpperCase();
  const dowFull = nowDate.toLocaleDateString([], { weekday: "long" }).toUpperCase();
  const monthLong = nowDate.toLocaleDateString([], { month: "long" });
  const yearN = nowDate.getFullYear();
  const monthYear = `${monthLong.toUpperCase()} ${yearN}`;
  const monShort = nowDate.toLocaleDateString([], { month: "short" }).toUpperCase();
  const hhmm = nowDate.toLocaleTimeString([], {
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
  return { dom, dow3, dowFull, monthLong, monShort, monthYear, yearN, hhmm, nowMs: nowDate.getTime() };
}

// Per-event presentation slice — handles all_day, start/end strings,
// and the colour stripe. Used by every variant.
function evBits(e) {
  return {
    startTxt: e.all_day ? "ALL DAY" : fmtTime(e.start),
    endTxt: e.all_day || !e.end ? "" : fmtTime(e.end),
    colour: e.colour || "var(--c-accent)",
    summary: e.summary || "(untitled)",
    location: e.location || "",
    all_day: !!e.all_day,
  };
}

// ============================================================
// D1 — BAUHAUS REFINED
// ============================================================
function renderD1(data, h, evs, live) {
  const rows = evs
    .map((b, i) => {
      const isLive = i === live;
      return `
        <article class="d1-row${isLive ? " d1-row--live" : ""}" style="--chip:${escapeHtml(b.colour)}">
          <div class="d1-stripe" aria-hidden="true"></div>
          <div class="d1-time">
            <span class="d1-time-start">${escapeHtml(b.startTxt)}</span>
            ${b.endTxt ? `<span class="d1-time-end">${escapeHtml(b.endTxt)}</span>` : ""}
          </div>
          <div class="d1-body">
            <div class="d1-title">${escapeHtml(b.summary)}</div>
            ${b.location ? `<div class="d1-loc">${escapeHtml(b.location)}</div>` : ""}
          </div>
          ${isLive ? '<span class="d1-now-tag">● NOW</span>' : ""}
        </article>
      `;
    })
    .join("");
  return `
    <div class="variant variant-d1">
      <header class="d1-header">
        <div class="d1-date-block">
          <span class="d1-day-num">${h.dom}</span>
          <div class="d1-date-meta">
            <div class="d1-dow">${h.dow3}</div>
            <div class="d1-month">${escapeHtml(h.monthYear)}</div>
          </div>
        </div>
        <div class="d1-meta">
          <div>${evs.length} EVENT${evs.length === 1 ? "" : "S"}</div>
          ${live >= 0 ? `<div class="d1-now">NOW ${escapeHtml(h.hhmm)}</div>` : ""}
        </div>
      </header>
      <div class="d1-rule"></div>
      <section class="d1-list">${rows}</section>
    </div>
  `;
}

// ============================================================
// D2 — BAUHAUS GEOMETRIC (De Stijl)
// ============================================================
function renderD2(data, h, evs, live) {
  const rows = evs
    .map((b, i) => {
      const isLive = i === live;
      return `
        <article class="d2-row${isLive ? " d2-row--live" : ""}" style="--chip:${escapeHtml(b.colour)}">
          <span class="d2-mark" aria-hidden="true"></span>
          <span class="d2-time">${escapeHtml(b.startTxt)}</span>
          <span class="d2-title">${escapeHtml(b.summary)}</span>
          ${isLive ? '<span class="d2-now-dot" aria-hidden="true"></span>' : ""}
        </article>
      `;
    })
    .join("");
  return `
    <div class="variant variant-d2">
      <header class="d2-header">
        <div class="d2-date-tile">
          <span class="d2-day-num">${h.dom}</span>
        </div>
        <div class="d2-title-band">
          <div class="d2-day-full">${h.dowFull}</div>
          <div class="d2-meta">${escapeHtml(h.monthYear)} · ${evs.length} EVENT${evs.length === 1 ? "" : "S"}</div>
        </div>
        <div class="d2-accent-band" aria-hidden="true"></div>
      </header>
      <section class="d2-list">${rows}</section>
    </div>
  `;
}

// ============================================================
// D3 — SWISS / INTERNATIONAL
// ============================================================
function renderD3(data, h, evs, live) {
  const rows = evs
    .map((b, i) => {
      const isLive = i === live;
      return `
        <article class="d3-row${isLive ? " d3-row--live" : ""}" style="--chip:${escapeHtml(b.colour)}">
          <span class="d3-time">${escapeHtml(b.startTxt)}${b.endTxt ? "–" + escapeHtml(b.endTxt) : ""}</span>
          <span class="d3-title">
            ${isLive ? '<span class="d3-now-mark" aria-hidden="true"></span>' : ""}
            ${escapeHtml(b.summary)}
          </span>
          ${b.location ? `<span class="d3-loc">${escapeHtml(b.location)}</span>` : ""}
        </article>
      `;
    })
    .join("");
  return `
    <div class="variant variant-d3">
      <div class="d3-eyebrow">${h.dowFull}</div>
      <div class="d3-titlebar">
        <div class="d3-date">${String(h.dom).padStart(2, "0")} ${h.monthLong.toUpperCase()} ${h.yearN}</div>
        <div class="d3-meta">${evs.length} EVENT${evs.length === 1 ? "" : "S"}</div>
      </div>
      <div class="d3-rule"></div>
      <section class="d3-list">${rows}</section>
    </div>
  `;
}

// ============================================================
// D4 — TIMELINE RAIL
// ============================================================
function renderD4(data, h, evs, live) {
  // Pick a window that covers every event in the day; pad ±1h so the
  // first/last events aren't flush with the edge. Falls back to 08–18
  // when there are no events.
  const mins = evs
    .map((b) => {
      const s = new Date(b.startISO || data.events[evs.indexOf(b)].start).getHours() * 60 +
        new Date(b.startISO || data.events[evs.indexOf(b)].start).getMinutes();
      return s;
    })
    .filter((m) => Number.isFinite(m));
  let startMin = mins.length ? Math.min(...mins) : 8 * 60;
  let endMin = mins.length ? Math.max(...mins) + 60 : 18 * 60;
  startMin = Math.max(0, Math.floor((startMin - 60) / 60) * 60);
  endMin = Math.min(24 * 60, Math.ceil((endMin + 60) / 60) * 60);
  const span = Math.max(60, endMin - startMin);
  const yPct = (m) => `${((m - startMin) / span) * 100}%`;

  // Hour grid lines every 2 hours within the window.
  const gridHours = [];
  for (let m = startMin; m <= endMin; m += 120) gridHours.push(m);
  const grid = gridHours
    .map(
      (m) => `
      <div class="d4-hour" style="top:${yPct(m)}">
        <span class="d4-hour-label">${String(Math.floor(m / 60)).padStart(2, "0")}:00</span>
        <div class="d4-hour-line"></div>
      </div>
    `,
    )
    .join("");

  // Events sized by duration. Default 45min if end is missing.
  const blocks = evs
    .map((b, i) => {
      const raw = data.events[i];
      const sd = new Date(raw.start);
      const ed = new Date(raw.end || raw.start);
      const sMin = sd.getHours() * 60 + sd.getMinutes();
      const eMin =
        raw.end && ed > sd ? ed.getHours() * 60 + ed.getMinutes() : sMin + 45;
      const top = yPct(sMin);
      const heightPct = ((Math.max(eMin - sMin, 20)) / span) * 100;
      const isLive = i === live;
      return `
        <div class="d4-event${isLive ? " d4-event--live" : ""}" style="top:${top};height:${heightPct}%;--chip:${escapeHtml(b.colour)}">
          <div class="d4-event-title">${escapeHtml(b.summary)}</div>
          <div class="d4-event-time">${escapeHtml(b.startTxt)}${b.endTxt ? "–" + escapeHtml(b.endTxt) : ""}</div>
        </div>
      `;
    })
    .join("");

  // NOW line position — only if now is within the window.
  const showNow =
    live >= 0 ||
    (h.nowMs &&
      new Date().getHours() * 60 + new Date().getMinutes() >= startMin &&
      new Date().getHours() * 60 + new Date().getMinutes() <= endMin);
  const nowMin = new Date().getHours() * 60 + new Date().getMinutes();
  const nowLine =
    showNow && nowMin >= startMin && nowMin <= endMin
      ? `
      <div class="d4-now" style="top:${yPct(nowMin)}">
        <span class="d4-now-dot"></span>
        <div class="d4-now-line"></div>
        <span class="d4-now-tag">NOW</span>
      </div>
    `
      : "";

  return `
    <div class="variant variant-d4">
      <header class="d4-header">
        <span class="d4-title">${h.dow3} ${h.dom}</span>
        <span class="d4-meta">${escapeHtml(h.monthYear)}</span>
      </header>
      <div class="d4-rail">
        ${grid}
        ${blocks}
        ${nowLine}
      </div>
    </div>
  `;
}

// ============================================================
// D5 — EDITORIAL / ALMANAC
// ============================================================
function renderD5(data, h, evs, live) {
  const rows = evs
    .map((b, i) => {
      const isLive = i === live;
      return `
        <article class="d5-row${isLive ? " d5-row--live" : ""}" style="--chip:${escapeHtml(b.colour)}">
          <span class="d5-time">${escapeHtml(b.startTxt)}</span>
          <span class="d5-mark" aria-hidden="true"></span>
          <span class="d5-title">${escapeHtml(b.summary)}</span>
          ${isLive ? '<span class="d5-now">NOW</span>' : ""}
        </article>
      `;
    })
    .join("");
  // Count words for the editorial footer.
  const countWords = [
    "ZERO", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN",
    "EIGHT", "NINE", "TEN", "ELEVEN", "TWELVE",
  ];
  const countWord = countWords[evs.length] || `${evs.length}`;
  const noun = evs.length === 1 ? "ENGAGEMENT" : "ENGAGEMENTS";
  return `
    <div class="variant variant-d5">
      <header class="d5-header">
        <div class="d5-meta">${h.dowFull}</div>
        <div class="d5-date-row">
          <span class="d5-day-num">${h.dom}</span>
          <span class="d5-month-italic">${h.monthLong}<br>${h.yearN}</span>
        </div>
      </header>
      <div class="d5-rules">
        <div class="d5-rule d5-rule--thick"></div>
        <div class="d5-rule d5-rule--thin"></div>
      </div>
      <section class="d5-list">${rows}</section>
      <footer class="d5-footer">— ${countWord} ${noun} —</footer>
    </div>
  `;
}

// ============================================================
// D6 — GLANCEABLE CARDS
// ============================================================
function renderD6(data, h, evs, live) {
  let nextIdx = -1;
  for (let i = 0; i < evs.length; i++) {
    if (i === live) continue;
    const raw = data.events[i];
    const s = new Date(raw.start).getTime();
    if (Number.isFinite(s) && s > h.nowMs) { nextIdx = i; break; }
  }
  const cards = evs
    .map((b, i) => {
      const isLive = i === live;
      const isNext = i === nextIdx;
      return `
        <div class="d6-card${isLive ? " d6-card--live" : ""}" style="--chip:${escapeHtml(b.colour)}">
          <div class="d6-card-top">
            <span class="d6-mark" aria-hidden="true"></span>
            ${isLive ? '<span class="d6-tag d6-tag--live">● LIVE</span>' : ""}
            ${!isLive && isNext ? '<span class="d6-tag d6-tag--next">NEXT</span>' : ""}
          </div>
          <div class="d6-card-bottom">
            <div class="d6-time">${escapeHtml(b.startTxt)}</div>
            <div class="d6-title">${escapeHtml(b.summary)}</div>
          </div>
        </div>
      `;
    })
    .join("");
  return `
    <div class="variant variant-d6">
      <header class="d6-header">
        <span class="d6-date">${h.dow3} · ${h.monShort} ${h.dom}</span>
        ${live >= 0 ? `<span class="d6-now-pill">NOW ${escapeHtml(h.hhmm)}</span>` : ""}
      </header>
      <div class="d6-grid">${cards}</div>
    </div>
  `;
}

const VARIANTS = {
  d1: renderD1, d2: renderD2, d3: renderD3,
  d4: renderD4, d5: renderD5, d6: renderD6,
};

function emptyState(h, size, variant) {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/plugins/calendar_day/client.css">
    <div class="root size-${size} variant-host variant-host--${variant}">
      <div class="cd-empty">
        <i class="ph-duotone ph-coffee" aria-hidden="true"></i>
        <div class="cd-empty-primary">Nothing scheduled</div>
        <div class="cd-empty-secondary">Enjoy the breathing room.</div>
      </div>
    </div>
  `;
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = `
      <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
      <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
      <link rel="stylesheet" href="/plugins/calendar_day/client.css">
      <div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>
    `;
    return;
  }
  const size = ctx.cell.size;
  const variant = (ctx.cell.options && ctx.cell.options.variant) || "d1";
  const events = Array.isArray(data.events) ? data.events.slice() : [];
  const h = headerBits(data);

  if (!events.length) {
    shadow.innerHTML = emptyState(h, size, variant);
    return;
  }

  const live = liveIndex(events, h.nowMs);
  const evs = events.map(evBits);
  const renderer = VARIANTS[variant] || renderD1;
  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/plugins/calendar_day/client.css">
    <div class="root size-${size} variant-host variant-host--${variant}">
      ${renderer(data, h, evs, live)}
    </div>
  `;
}
