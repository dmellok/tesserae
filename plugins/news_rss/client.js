// news_rss — N headlines from one RSS / Atom feed.
//
// Four directions pickable per-cell via `variant`, mirroring the rest
// of the family (weather, HA, news, todo):
//
//   r1  Refined    Dark Bauhaus header + accent lede block + tinted
//                  numbered list. Time stamps as mono pills.
//   g2  Geometric  De Stijl colour blocks — every headline is a tile,
//                  rank in mono, title in Archivo Black; accent fills.
//   s3  Swiss      Hairline header + tabular rows: 01 · title · ago.
//   d4  Data       Stat block (item count + freshest age) over a
//                  compact list with elapsed-time bars.

import { WX } from "../weather_core/static/wx-common.js";

const escapeHtml = (s) => WX.escapeHtml(s);
const DEFAULT_FONT = "var(--wx-grotesk)";

function nowTime() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

function agoSeconds(iso) {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (!isFinite(t)) return null;
  return Math.max(0, Math.floor((Date.now() - t) / 1000));
}

function ago(iso) {
  const s = agoSeconds(iso);
  if (s == null) return "";
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  if (s < 604800) return `${Math.floor(s / 86400)}d`;
  return `${Math.floor(s / 604800)}w`;
}

function styleBlock() {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus-wx.css">
    <link rel="stylesheet" href="/plugins/news_rss/client.css">
  `;
}

function emptyState() {
  return `
    <div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;color:var(--wx-ink-60);font-family:var(--wx-grotesk)">
      ${WX.icon("rss", { size: 28, color: "var(--wx-ink-60)" })}
      <span style="font-size:12px;letter-spacing:.06em;font-weight:600;text-transform:uppercase">No headlines</span>
    </div>
  `;
}

function headerLabel(data, opts) {
  if (opts.show_source !== false && data.feed_title) return data.feed_title;
  return "Headlines";
}

// ===========================================================
// R1 — REFINED (dark header + accent lede + tinted list)
// ===========================================================
function renderR1(data, items, opts) {
  const label = headerLabel(data, opts);
  const headerRight = `${items.length} HEADLINES · ${escapeHtml(nowTime())}`;
  const lede = items[0];
  const rest = items.slice(1);

  const ledeBlock = lede
    ? `
      <article style="display:grid;grid-template-columns:auto 1fr;gap:18px;align-items:center;padding:16px 18px;background:var(--c-accent);color:var(--wx-red-fg);border-bottom:3px solid var(--wx-ink)">
        <div class="wx-tnum" style="font-family:var(--wx-black);font-size:64px;line-height:.85;letter-spacing:-.04em">01</div>
        <div style="display:grid;gap:8px;min-width:0">
          <h3 style="margin:0;font-family:var(--wx-grotesk);font-size:20px;font-weight:800;line-height:1.18;letter-spacing:-.01em;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden">${escapeHtml(lede.title)}</h3>
          ${lede.published
            ? `<div style="display:flex;align-items:center;gap:6px;font-family:var(--wx-mono);font-size:11px;font-weight:700;letter-spacing:.04em">${WX.icon("clock", { size: 12, color: "currentColor" })}<span class="wx-tnum">${escapeHtml(ago(lede.published))} ago</span></div>`
            : ""}
        </div>
      </article>
    `
    : "";

  const rows = rest
    .map((it, i) => {
      const n = String(i + 2).padStart(2, "0");
      return `
        <article style="display:grid;grid-template-columns:auto 1fr auto;gap:14px;padding:8px 16px;border-bottom:1px solid color-mix(in oklab, var(--wx-ink) 10%, transparent);align-items:center">
          <span class="wx-tnum" style="font-family:var(--wx-black);font-size:18px;color:var(--c-accent);letter-spacing:-.02em;min-width:1.6em;text-align:right">${n}</span>
          <div style="font-family:var(--wx-grotesk);font-size:13px;font-weight:700;line-height:1.25;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;min-width:0">${escapeHtml(it.title)}</div>
          ${it.published
            ? `<span class="wx-tnum" style="font-family:var(--wx-mono);font-size:10.5px;font-weight:700;letter-spacing:.06em;color:var(--wx-ink-60)">${escapeHtml(ago(it.published))}</span>`
            : `<span></span>`}
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
function renderG2(data, items, opts) {
  const label = headerLabel(data, opts);
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
          <div style="display:flex;justify-content:space-between;align-items:baseline">
            <span class="wx-tnum" style="font-family:var(--wx-black);font-size:24px;line-height:.85;letter-spacing:-.04em">${String(i + 1).padStart(2, "0")}</span>
            ${it.published
              ? `<span class="wx-tnum" style="font-family:var(--wx-mono);font-size:10px;letter-spacing:.06em;font-weight:700">${escapeHtml(ago(it.published))}</span>`
              : ""}
          </div>
          <div style="font-family:var(--wx-black);font-size:13px;line-height:1.15;font-weight:900;display:-webkit-box;-webkit-line-clamp:5;-webkit-box-orient:vertical;overflow:hidden;letter-spacing:-.005em">${escapeHtml(it.title)}</div>
        </div>
      `;
    })
    .join("");

  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-geo);display:flex;flex-direction:column;background:var(--wx-ink);gap:3px">
      <div style="background:var(--wx-ink);color:var(--wx-paper);padding:8px 14px;display:flex;justify-content:space-between;align-items:baseline;font-family:var(--wx-mono);font-size:12px;letter-spacing:.06em;font-weight:700;border-bottom:3px solid var(--c-accent)">
        <span>${escapeHtml(label.toUpperCase())}</span>
        <span style="opacity:.85;font-weight:400">${items.length} ITEMS</span>
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
function renderS3(data, items, opts) {
  const label = headerLabel(data, opts);
  const rows = items
    .map((it, i) => {
      return `
        <div style="display:grid;grid-template-columns:24px 1fr auto;align-items:baseline;gap:14px;padding:7px 0;${i < items.length - 1 ? "border-bottom:1px solid color-mix(in oklab, var(--wx-ink) 16%, transparent);" : ""}">
          <span class="wx-tnum" style="font-size:10.5px;font-weight:500;color:var(--wx-ink-60);letter-spacing:.06em">${String(i + 1).padStart(2, "0")}</span>
          <span style="font-size:13.5px;font-weight:500;color:var(--wx-ink);min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(it.title)}</span>
          ${it.published
            ? `<span class="wx-tnum" style="font-family:var(--wx-mono);font-size:10.5px;font-weight:700;letter-spacing:.06em;color:var(--c-accent)">${escapeHtml(ago(it.published))}</span>`
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
        <span style="font-size:10.5px;letter-spacing:.16em;color:var(--wx-ink-60)">${items.length} items · ${escapeHtml(nowTime())}</span>
      </div>
      <div style="height:2px;background:var(--c-accent);margin:10px 0 4px"></div>
      <div style="flex:1;display:flex;flex-direction:column;overflow:hidden">
        ${items.length === 0 ? emptyState() : rows}
      </div>
    </div>
  `;
}

// ===========================================================
// D4 — DATA (stat block + recency-bar list)
// ===========================================================
function renderD4(data, items, opts) {
  const label = headerLabel(data, opts);
  // Recency bar: full bar = freshest (smallest age), empty = oldest.
  const ages = items.map((it) => agoSeconds(it.published)).filter((v) => v != null);
  const oldest = ages.length ? Math.max(...ages) : 1;
  const freshest = ages.length ? Math.min(...ages) : 0;

  const top = items[0];
  const topAge = top ? ago(top.published) : "—";

  const bars = items
    .slice(0, 8)
    .map((it, i) => {
      const s = agoSeconds(it.published);
      // Invert so newest reads as the fullest bar.
      const f = s == null ? 0 : 1 - (s - freshest) / Math.max(1, oldest - freshest);
      const w = (Math.max(0.05, f) * 100).toFixed(1);
      const isTop = i === 0;
      return `
        <div style="display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:10px;padding:4px 14px;${i < Math.min(items.length, 8) - 1 ? "border-bottom:1px solid color-mix(in oklab, var(--wx-ink) 10%, transparent);" : ""}">
          <span class="wx-tnum" style="font-family:var(--wx-mono);font-size:10.5px;font-weight:700;color:var(--wx-ink-60);letter-spacing:.04em;min-width:1.6em">${String(i + 1).padStart(2, "0")}</span>
          <div style="display:grid;gap:3px;min-width:0">
            <span style="font-family:var(--wx-grotesk);font-size:11.5px;font-weight:600;color:var(--wx-ink);min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(it.title)}</span>
            <div style="height:5px;background:var(--wx-paper-3)">
              <div style="width:${w}%;height:100%;background:${isTop ? "var(--c-accent)" : "var(--wx-blue)"}"></div>
            </div>
          </div>
          <span class="wx-tnum" style="font-family:var(--wx-black);font-size:12px;color:${isTop ? "var(--c-accent)" : "var(--wx-ink)"};min-width:42px;text-align:right">${escapeHtml(ago(it.published))}</span>
        </div>
      `;
    })
    .join("");

  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};display:flex;flex-direction:column;background:var(--wx-paper)">
      <div style="background:var(--wx-ink);color:var(--wx-paper);padding:10px 16px;display:flex;justify-content:space-between;align-items:baseline;border-bottom:3px solid var(--c-accent)">
        <span style="font-family:var(--wx-mono);font-size:11px;letter-spacing:.1em;font-weight:700;text-transform:uppercase">${escapeHtml(label)}</span>
        <span style="font-family:var(--wx-mono);font-size:10px;letter-spacing:.1em;opacity:.85">${items.length} ITEMS</span>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:2px;background:var(--wx-ink)">
        <div style="background:var(--wx-paper);padding:12px 16px">
          <div style="font-family:var(--wx-mono);font-size:9.5px;letter-spacing:.12em;font-weight:700;color:var(--wx-ink-60);text-transform:uppercase">FRESHEST</div>
          <div class="wx-tnum" style="font-family:var(--wx-black);font-size:42px;line-height:1;color:var(--c-accent)">${escapeHtml(topAge)}</div>
        </div>
        <div style="background:var(--wx-paper);padding:12px 16px">
          <div style="font-family:var(--wx-mono);font-size:9.5px;letter-spacing:.12em;font-weight:700;color:var(--wx-ink-60);text-transform:uppercase">UPDATED</div>
          <div class="wx-tnum" style="font-family:var(--wx-black);font-size:42px;line-height:1;color:var(--wx-blue)">${escapeHtml(nowTime())}</div>
        </div>
      </div>
      <div style="flex:1;background:var(--wx-paper);border-top:2px solid var(--wx-ink);overflow:hidden;padding:6px 0">
        ${items.length === 0 ? emptyState() : bars}
      </div>
    </div>
  `;
}

// ===========================================================
// LEGACY — quiet paper list, hairline rules, no solid accent panels.
// ===========================================================
function renderLegacy(data, items, opts) {
  const label = headerLabel(data, opts);
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};display:flex;flex-direction:column;background:var(--wx-paper)">
      ${WX.darkHeader({ title: label, accent: "ink", right: `${items.length} HEADLINES · ${escapeHtml(nowTime())}` })}
      <div style="flex:1;overflow:hidden;border-top:2px solid var(--wx-ink)">
        ${items.length === 0 ? emptyState() : items.map((it, i) => {
          const n = String(i + 1).padStart(2, "0");
          return `
            <article style="display:grid;grid-template-columns:auto 1fr auto;gap:14px;padding:8px 16px;border-bottom:1px solid color-mix(in oklab, var(--wx-ink) 10%, transparent);align-items:center">
              <span class="wx-tnum" style="font-family:var(--wx-black);font-size:18px;color:var(--wx-ink-60);letter-spacing:-.02em;min-width:1.6em;text-align:right">${n}</span>
              <div style="font-family:var(--wx-grotesk);font-size:13px;font-weight:700;line-height:1.25;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;min-width:0">${escapeHtml(it.title)}</div>
              ${it.published
                ? `<span class="wx-tnum" style="font-family:var(--wx-mono);font-size:10.5px;font-weight:700;letter-spacing:.06em;color:var(--wx-ink-60)">${escapeHtml(ago(it.published))}</span>`
                : `<span></span>`}
            </article>
          `;
        }).join("")}
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
  shadow.innerHTML = renderer(data, items, opts, size);
}
