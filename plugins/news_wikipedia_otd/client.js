// news_wikipedia_otd — Wikipedia "On this day".
//
// Four directions pickable per-cell via `variant`, mirroring the rest
// of the family (weather, HA, news, todo):
//
//   r1  Refined    Dark Bauhaus header + accent lede block (year as
//                  hero numeral, text below) + tinted year-rule list.
//   g2  Geometric  De Stijl colour blocks — each event is a tile,
//                  year in Archivo Black, text in mono; accent fills.
//   s3  Swiss      Hairline header + tabular rows: year · text · page.
//   d4  Data       Timeline visualization — events plotted along a
//                  bar from earliest to today, with a compact list.

import { WX } from "../weather_core/static/wx-common.js";

const escapeHtml = (s) => WX.escapeHtml(s);
const DEFAULT_FONT = "var(--wx-grotesk)";

const TYPE_LABEL = {
  events: "Events",
  births: "Births",
  deaths: "Deaths",
  holidays: "Holidays",
};
const TYPE_ICON = {
  events: "calendar-blank",
  births: "baby",
  deaths: "flower",
  holidays: "confetti",
};

function nowTime() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

function styleBlock() {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus-wx.css">
    <link rel="stylesheet" href="/plugins/news_wikipedia_otd/client.css">
  `;
}

function emptyState() {
  return `
    <div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;color:var(--wx-ink-60);font-family:var(--wx-grotesk)">
      ${WX.icon("book-open", { size: 28, color: "var(--wx-ink-60)" })}
      <span style="font-size:12px;letter-spacing:.06em;font-weight:600;text-transform:uppercase">Nothing notable</span>
    </div>
  `;
}

function fmtYear(y) {
  if (y == null || y === "") return "—";
  const n = Number(y);
  if (Number.isFinite(n) && n < 0) return `${Math.abs(n)} BC`;
  return String(y);
}

function feedLabel(data) {
  return `${data.date || ""} · ${TYPE_LABEL[data.kind] || "On this day"}`;
}

// ===========================================================
// R1 — REFINED (accent lede with hero year + year-rule list)
// ===========================================================
function renderR1(data, items) {
  const label = feedLabel(data);
  const headerRight = `${items.length} ENTRIES · ${escapeHtml(nowTime())}`;
  const lede = items[0];
  const rest = items.slice(1);

  const ledeBlock = lede
    ? `
      <article style="display:grid;grid-template-columns:auto 1fr;gap:20px;align-items:center;padding:16px 18px;background:var(--c-accent);color:var(--wx-red-fg);border-bottom:3px solid var(--wx-ink)">
        <div class="wx-tnum" style="font-family:var(--wx-black);font-size:54px;line-height:.85;letter-spacing:-.04em;min-width:120px">${escapeHtml(fmtYear(lede.year))}</div>
        <div style="display:grid;gap:6px;min-width:0">
          <p style="margin:0;font-family:var(--wx-grotesk);font-size:14px;font-weight:600;line-height:1.35;display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden">${escapeHtml(lede.text)}</p>
          ${lede.page
            ? `<span style="font-family:var(--wx-mono);font-size:10.5px;font-weight:700;letter-spacing:.06em;opacity:.88">${escapeHtml(lede.page)}</span>`
            : ""}
        </div>
      </article>
    `
    : "";

  const rows = rest
    .map((it, i) => {
      return `
        <article style="display:grid;grid-template-columns:auto 1fr;gap:14px;padding:8px 16px;border-bottom:1px solid color-mix(in oklab, var(--wx-ink) 10%, transparent);align-items:start">
          <span class="wx-tnum" style="font-family:var(--wx-black);font-size:18px;color:var(--c-accent);letter-spacing:-.02em;min-width:3.6em;text-align:right">${escapeHtml(fmtYear(it.year))}</span>
          <div style="display:grid;gap:2px;min-width:0">
            <span style="font-family:var(--wx-grotesk);font-size:12.5px;font-weight:500;line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">${escapeHtml(it.text)}</span>
            ${it.page
              ? `<span style="font-family:var(--wx-mono);font-size:10px;font-weight:700;letter-spacing:.04em;color:var(--wx-ink-60)">${escapeHtml(it.page)}</span>`
              : ""}
          </div>
        </article>
      `;
    })
    .join("");

  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};display:flex;flex-direction:column;background:var(--wx-paper)">
      ${WX.darkHeader({ title: label, accent: "red", right: headerRight })}
      ${ledeBlock}
      <div style="flex:1;background:var(--wx-paper);overflow:hidden">
        ${items.length === 0 ? emptyState() : rows || ""}
      </div>
    </div>
  `;
}

// ===========================================================
// G2 — GEOMETRIC (colour-block grid, De Stijl)
// ===========================================================
function renderG2(data, items) {
  const label = feedLabel(data);
  const PALETTE = [
    { bg: "var(--c-accent)", fg: "var(--wx-red-fg)" },
    { bg: "var(--wx-blue)",  fg: "var(--wx-blue-fg)" },
    { bg: "var(--wx-yellow)", fg: "var(--wx-yellow-fg)" },
    { bg: "var(--wx-ink)",   fg: "var(--wx-paper)" },
    { bg: "var(--c-accent)", fg: "var(--wx-red-fg)" },
    { bg: "var(--wx-green)", fg: "var(--wx-green-fg)" },
  ];
  const cols = items.length <= 4 ? 2 : items.length <= 9 ? 3 : 4;

  const tiles = items
    .map((it, i) => {
      const pal = PALETTE[i % PALETTE.length];
      return `
        <div style="background:${pal.bg};color:${pal.fg};padding:12px 14px;display:flex;flex-direction:column;justify-content:space-between;gap:10px;min-width:0">
          <span class="wx-tnum" style="font-family:var(--wx-black);font-size:32px;line-height:.85;letter-spacing:-.04em">${escapeHtml(fmtYear(it.year))}</span>
          <div style="font-family:var(--wx-grotesk);font-size:11.5px;line-height:1.25;font-weight:600;display:-webkit-box;-webkit-line-clamp:5;-webkit-box-orient:vertical;overflow:hidden">${escapeHtml(it.text)}</div>
          ${it.page
            ? `<div style="font-family:var(--wx-mono);font-size:9.5px;font-weight:700;letter-spacing:.06em;opacity:.92;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(it.page)}</div>`
            : ""}
        </div>
      `;
    })
    .join("");

  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-geo);display:flex;flex-direction:column;background:var(--wx-ink);gap:3px">
      <div style="background:var(--wx-ink);color:var(--wx-paper);padding:8px 14px;display:flex;justify-content:space-between;align-items:baseline;font-family:var(--wx-mono);font-size:12px;letter-spacing:.06em;font-weight:700;border-bottom:3px solid var(--c-accent)">
        <span>${escapeHtml(label.toUpperCase())}</span>
        <span style="opacity:.85;font-weight:400">${items.length} ENTRIES</span>
      </div>
      ${items.length === 0
        ? `<div style="flex:1;background:var(--wx-paper);display:flex">${emptyState()}</div>`
        : `<div style="flex:1;display:grid;grid-template-columns:repeat(${cols},1fr);grid-auto-rows:1fr;gap:3px">${tiles}</div>`}
    </div>
  `;
}

// ===========================================================
// S3 — SWISS (hairline header, tabular rows)
// ===========================================================
function renderS3(data, items) {
  const label = feedLabel(data);
  const rows = items
    .map((it, i) => {
      return `
        <div style="display:grid;grid-template-columns:60px 1fr auto;align-items:baseline;gap:14px;padding:7px 0;${i < items.length - 1 ? "border-bottom:1px solid color-mix(in oklab, var(--wx-ink) 16%, transparent);" : ""}">
          <span class="wx-tnum" style="font-size:11px;font-weight:700;color:var(--c-accent);letter-spacing:.06em">${escapeHtml(fmtYear(it.year))}</span>
          <span style="font-size:13px;font-weight:500;color:var(--wx-ink);min-width:0;line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">${escapeHtml(it.text)}</span>
          ${it.page
            ? `<span class="wx-tnum" style="font-family:var(--wx-mono);font-size:10px;font-weight:700;letter-spacing:.06em;color:var(--wx-ink-60);max-width:140px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(it.page)}</span>`
            : `<span></span>`}
        </div>
      `;
    })
    .join("");

  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-swiss);padding:18px 24px;display:flex;flex-direction:column">
      <div style="display:flex;justify-content:space-between;align-items:baseline">
        <span style="font-size:12px;letter-spacing:.2em;font-weight:700;text-transform:uppercase;color:var(--c-accent)">${escapeHtml(label)}</span>
        <span style="font-size:10.5px;letter-spacing:.16em;color:var(--wx-ink-60)">${items.length} entries</span>
      </div>
      <div style="height:2px;background:var(--c-accent);margin:10px 0 4px"></div>
      <div style="flex:1;display:flex;flex-direction:column;overflow:hidden">
        ${items.length === 0 ? emptyState() : rows}
      </div>
    </div>
  `;
}

// ===========================================================
// D4 — DATA (timeline visualization + compact list)
// ===========================================================
function renderD4(data, items) {
  const label = feedLabel(data);
  const thisYear = new Date().getFullYear();
  // Plot timeline from earliest event year → today. Events with no
  // year (or non-numeric) get dropped from the visualization.
  const yrs = items.map((it) => Number(it.year)).filter((n) => Number.isFinite(n));
  const earliest = yrs.length ? Math.min(...yrs) : thisYear - 100;
  const span = Math.max(1, thisYear - earliest);
  const oldest = items.find((it) => Number(it.year) === earliest);
  const newest = items.find((it) => Number(it.year) === Math.max(...yrs)) || items[0];

  const ticks = items
    .map((it) => {
      const y = Number(it.year);
      if (!Number.isFinite(y)) return "";
      const pct = (((y - earliest) / span) * 100).toFixed(1);
      return `<div style="position:absolute;left:${pct}%;top:0;bottom:0;width:2px;background:var(--c-accent);transform:translateX(-1px)"></div>`;
    })
    .join("");

  const rows = items
    .slice(0, 6)
    .map((it, i) => {
      const isTop = i === 0;
      return `
        <div style="display:grid;grid-template-columns:auto 1fr;align-items:start;gap:12px;padding:5px 14px;${i < Math.min(items.length, 6) - 1 ? "border-bottom:1px solid color-mix(in oklab, var(--wx-ink) 10%, transparent);" : ""}">
          <span class="wx-tnum" style="font-family:var(--wx-black);font-size:14px;color:${isTop ? "var(--c-accent)" : "var(--wx-ink)"};min-width:3.5em;text-align:right;letter-spacing:-.02em">${escapeHtml(fmtYear(it.year))}</span>
          <span style="font-family:var(--wx-grotesk);font-size:11.5px;font-weight:500;color:var(--wx-ink);min-width:0;line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">${escapeHtml(it.text)}</span>
        </div>
      `;
    })
    .join("");

  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};display:flex;flex-direction:column;background:var(--wx-paper)">
      <div style="background:var(--wx-ink);color:var(--wx-paper);padding:10px 16px;display:flex;justify-content:space-between;align-items:baseline;border-bottom:3px solid var(--c-accent)">
        <span style="font-family:var(--wx-mono);font-size:11px;letter-spacing:.1em;font-weight:700;text-transform:uppercase">${escapeHtml(label)}</span>
        <span style="font-family:var(--wx-mono);font-size:10px;letter-spacing:.1em;opacity:.85">${items.length} ENTRIES</span>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:2px;background:var(--wx-ink)">
        <div style="background:var(--wx-paper);padding:12px 16px">
          <div style="font-family:var(--wx-mono);font-size:9.5px;letter-spacing:.12em;font-weight:700;color:var(--wx-ink-60);text-transform:uppercase">EARLIEST</div>
          <div class="wx-tnum" style="font-family:var(--wx-black);font-size:36px;line-height:1;color:var(--c-accent)">${escapeHtml(oldest ? fmtYear(oldest.year) : "—")}</div>
        </div>
        <div style="background:var(--wx-paper);padding:12px 16px">
          <div style="font-family:var(--wx-mono);font-size:9.5px;letter-spacing:.12em;font-weight:700;color:var(--wx-ink-60);text-transform:uppercase">SPAN</div>
          <div class="wx-tnum" style="font-family:var(--wx-black);font-size:36px;line-height:1;color:var(--wx-blue)">${span}y</div>
        </div>
      </div>
      <div style="position:relative;height:24px;background:var(--wx-paper-3);margin:0 14px;border-left:2px solid var(--wx-ink);border-right:2px solid var(--wx-ink)">
        ${ticks}
        <div style="position:absolute;left:0;right:0;bottom:-14px;display:flex;justify-content:space-between;font-family:var(--wx-mono);font-size:9px;letter-spacing:.06em;font-weight:700;color:var(--wx-ink-60)">
          <span class="wx-tnum">${escapeHtml(fmtYear(earliest))}</span>
          <span class="wx-tnum">${thisYear}</span>
        </div>
      </div>
      <div style="flex:1;background:var(--wx-paper);border-top:2px solid var(--wx-ink);margin-top:22px;overflow:hidden;padding:6px 0">
        ${items.length === 0 ? emptyState() : rows}
      </div>
    </div>
  `;
}

// ===========================================================
// LEGACY — quiet paper list, hairline rules, no solid accent panels.
// ===========================================================
function renderLegacy(data, items) {
  const label = feedLabel(data);
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};display:flex;flex-direction:column;background:var(--wx-paper)">
      ${WX.darkHeader({ title: label, accent: "ink", right: `${items.length} ENTRIES` })}
      <div style="flex:1;overflow:hidden;border-top:2px solid var(--wx-ink)">
        ${items.length === 0 ? emptyState() : items.map((it) => `
          <article style="display:grid;grid-template-columns:auto 1fr;gap:14px;padding:8px 16px;border-bottom:1px solid color-mix(in oklab, var(--wx-ink) 10%, transparent);align-items:start">
            <span class="wx-tnum" style="font-family:var(--wx-black);font-size:18px;color:var(--wx-ink-60);letter-spacing:-.02em;min-width:3.6em;text-align:right">${escapeHtml(fmtYear(it.year))}</span>
            <div style="display:grid;gap:2px;min-width:0">
              <span style="font-family:var(--wx-grotesk);font-size:12.5px;font-weight:500;line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">${escapeHtml(it.text)}</span>
              ${it.page
                ? `<span style="font-family:var(--wx-mono);font-size:10px;font-weight:700;letter-spacing:.04em;color:var(--wx-ink-60)">${escapeHtml(it.page)}</span>`
                : ""}
            </div>
          </article>
        `).join("")}
      </div>
    </div>
  `;
}

const VARIANTS = { r1: renderR1, g2: renderG2, s3: renderS3, d4: renderD4, legacy: renderLegacy };

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  const opts = ctx.cell.options || {};
  const size = ctx.cell.size;

  if (data.error) {
    shadow.innerHTML = `${styleBlock()}
      <div class="root error size-${size}">
        <i class="ph-bold ph-warning-circle" aria-hidden="true"></i>
        <span>${escapeHtml(data.error)}</span>
      </div>`;
    return;
  }

  const items = Array.isArray(data.items) ? data.items : [];
  const variant = (opts.variant || "r1").toLowerCase();
  const renderer = VARIANTS[variant] || renderR1;
  shadow.innerHTML = renderer(data, items, size);
}
