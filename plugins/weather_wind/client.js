// weather_wind — current wind speed/gust/direction + Beaufort with a
// compass dial, wind rose, and 12h gust trace.
//
// Brand-new widget (no legacy variant). Ships four directions:
//   r1  Refined    — charcoal header + compass dial + 6-hour strip
//   g2  Geometric  — colour-blocked panels, big numerals
//   s3  Swiss      — hairline header, light numerals, compass on left
//   d4  Data       — 8-point wind rose + gust trace area chart
//
// Render shape from server.py see the docstring there. Everything
// here is plain DOM via template literals — Tesserae widgets run in a
// shadow root, no React.

import { WX } from "../weather_core/static/wx-common.js";

const DEFAULT_FONT = "var(--wx-grotesk)";

function escapeHtml(s) { return WX.escapeHtml(s); }

// ===========================================================
// Shared chart / dial helpers
// ===========================================================

// Compass dial — circular ring with N/E/S/W markers and the Phosphor
// navigation-arrow icon rotated to the wind's bearing. Wind direction
// in meteorology is the direction the wind blows *from* (so we add
// 180° to get the arrow's "blow-to" rotation, matching the design).
function compass({ bearing, size = 150, color = "var(--wx-blue)", label = null } = {}) {
  const arrowPx = Math.round(size * 0.46);
  const arrowSize = arrowPx;
  return `
    <div style="position:relative;width:${size}px;height:${size}px">
      <div style="position:absolute;inset:0;border:3px solid var(--wx-ink);border-radius:50%"></div>
      ${[
        ["N", "top:4px;left:50%;transform:translateX(-50%)"],
        ["E", "right:6px;top:50%;transform:translateY(-50%)"],
        ["S", "bottom:4px;left:50%;transform:translateX(-50%)"],
        ["W", "left:6px;top:50%;transform:translateY(-50%)"],
      ].map(([letter, pos]) => `
        <span style="position:absolute;font-family:var(--wx-mono);font-size:${(size * 0.085).toFixed(1)}px;font-weight:700;color:var(--wx-ink-60);${pos}">${letter}</span>
      `).join("")}
      <div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;transform:rotate(${bearing + 180}deg)">
        ${WX.icon("arrow", { size: arrowSize, color, weight: "fill" })}
      </div>
      ${label ? `<span style="position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);font-family:var(--wx-black);font-size:${(size * 0.13).toFixed(1)}px;color:var(--wx-ink);pointer-events:none">${escapeHtml(label)}</span>` : ""}
    </div>
  `;
}

// 8-point wind rose. Each spoke length = relative speed.
function windRose({ data, size = 156, color = "var(--wx-blue)" } = {}) {
  const cx = size / 2;
  const R = size / 2 - 14;
  const max = Math.max(1, ...data.map((d) => d.v));
  const spokes = data.map((d, i) => {
    const ang = (i * 45 - 90) * Math.PI / 180;
    const len = (d.v / max) * R;
    const x = cx + Math.cos(ang) * len;
    const y = cx + Math.sin(ang) * len;
    return `<line x1="${cx}" y1="${cx}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" stroke="${color}" stroke-width="7" stroke-linecap="round" />`;
  }).join("");
  const cardinals = ["N", "E", "S", "W"].map((l, i) => {
    const ang = (i * 90 - 90) * Math.PI / 180;
    const x = cx + Math.cos(ang) * (R + 8);
    const y = cx + Math.sin(ang) * (R + 8) + 4;
    return `<text x="${x.toFixed(1)}" y="${y.toFixed(1)}" text-anchor="middle" style="font-family:var(--wx-mono);font-size:11.5px;font-weight:700;fill:var(--wx-ink-60)">${l}</text>`;
  }).join("");
  return `
    <svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" aria-hidden="true">
      <circle cx="${cx}" cy="${cx}" r="${(R * 0.5).toFixed(1)}" fill="none" stroke="var(--c-line)" stroke-width="1" stroke-dasharray="2 4" />
      <circle cx="${cx}" cy="${cx}" r="${R}" fill="none" stroke="var(--c-line)" stroke-width="1" stroke-dasharray="2 4" />
      ${spokes}
      <circle cx="${cx}" cy="${cx}" r="3.5" fill="var(--wx-ink)" />
      ${cardinals}
    </svg>
  `;
}

// Area chart for the 12h gust trace. preserveAspectRatio="none" +
// non-scaling-stroke keeps the line crisp at any container width.
function areaChart({ series, color, fill, width = 560, height = 120 } = {}) {
  if (!series || !series.length) return "";
  const min = Math.min(...series);
  const max = Math.max(...series);
  const span = Math.max(0.1, max - min);
  const w = width;
  const h = height;
  const dx = w / Math.max(1, series.length - 1);
  const pts = series.map((v, i) => {
    const x = i * dx;
    const y = h - ((v - min) / span) * (h - 6) - 3;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const fillPath = `M0,${h} L${pts.join(" L")} L${w},${h} Z`;
  const linePath = `M${pts.join(" L")}`;
  return `
    <svg width="100%" height="100%" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
      <path d="${fillPath}" fill="${fill}" />
      <path d="${linePath}" fill="none" stroke="${color}" stroke-width="2.5" vector-effect="non-scaling-stroke" />
    </svg>
  `;
}

function styleBlock() {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/fill/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus-wx.css">
  `;
}

// ===========================================================
// R1 — REFINED (charcoal header + accent compass panel + hour strip)
// ===========================================================
function renderR1(data) {
  const hours = Array.isArray(data.hours) ? data.hours : [];
  return `
    ${styleBlock()}
    <style>
      .ww-r1-hero { flex:1; display:grid; grid-template-columns:1fr 1.4fr; min-width:0; min-height:0; border-top:3px solid var(--c-accent); }
      .ww-r1-compass { background:var(--c-accent); display:flex; align-items:center; justify-content:center; padding:clamp(10px, 2cqw, 18px); color:var(--wx-red-fg); }
      .ww-r1-stats { background:var(--wx-tint); padding:clamp(10px, 2.4cqw, 18px) clamp(14px, 3cqw, 24px); display:flex; flex-direction:column; justify-content:center; gap:6px; min-width:0; }
      .ww-r1-speed { display:flex; align-items:flex-end; gap:6px; }
      .ww-r1-speed-num { font-family:var(--wx-black); font-size:clamp(40px, 14cqw, 80px); line-height:.8; color:var(--c-accent); }
      .ww-r1-speed-unit { font-family:var(--wx-mono); font-size:clamp(12px, 2.4cqw, 18px); color:var(--wx-ink-60); margin-bottom:clamp(4px, 1.4cqw, 12px); }
      .ww-r1-dir { font-weight:800; font-size:clamp(13px, 2.6cqw, 19px); letter-spacing:.02em; color:var(--c-text); }
      .ww-r1-meta { display:flex; gap:20px; font-family:var(--wx-mono); font-size:clamp(10px, 1.8cqw, 12px); color:var(--wx-ink-60); flex-wrap:wrap; }
      .ww-r1-hours { display:flex; border-top:3px solid var(--c-accent); background:var(--wx-paper); }
      .ww-r1-hour { flex:1; padding:9px 0; display:flex; flex-direction:column; align-items:center; gap:4px; min-width:0; }
      .ww-r1-hour + .ww-r1-hour { border-left:1px solid var(--c-line); }

      @container (max-width: 460px) {
        .ww-r1-hero { grid-template-columns:1fr; }
      }
    </style>
    <div class="wx-art" style="font-family:${DEFAULT_FONT};display:flex;flex-direction:column">
      ${WX.darkHeader({ title: `${data.place || ""} · WIND`, accent: "red", right: data.time || "" })}
      <div class="ww-r1-hero">
        <div class="ww-r1-compass">
          ${compass({ bearing: data.bearing || 0, size: 150, color: "var(--wx-red-fg)" })}
        </div>
        <div class="ww-r1-stats">
          <div class="ww-r1-speed">
            <span class="wx-tnum ww-r1-speed-num">${escapeHtml(String(data.speed))}</span>
            <span class="ww-r1-speed-unit">${escapeHtml(data.unit || "")}</span>
          </div>
          <div class="ww-r1-dir">FROM ${escapeHtml(data.dir || "")} · ${escapeHtml((data.beaufortLabel || "").toUpperCase())}</div>
          <div class="ww-r1-meta">
            <span style="display:flex;align-items:center;gap:6px">${WX.icon("wind", { size: 15, color: "var(--c-accent)" })}GUST <b style="color:var(--c-text)">${escapeHtml(String(data.gust))} ${escapeHtml(data.unit || "")}</b></span>
            <span style="display:flex;align-items:center;gap:6px">${WX.icon("gauge", { size: 15, color: "var(--c-accent)" })}BEAUFORT <b style="color:var(--c-text)">${escapeHtml(String(data.beaufort))}</b></span>
          </div>
        </div>
      </div>
      <div class="ww-r1-hours">
        ${hours.map((h) => `
          <div class="ww-r1-hour">
            <span style="font-family:var(--wx-mono);font-size:11.5px;color:var(--wx-ink-60)">${escapeHtml(h.t || "")}</span>
            <span style="display:inline-flex;transform:rotate(${(h.dir || 0) + 180}deg)">${WX.icon("arrow", { size: 20, color: "var(--c-accent)", weight: "fill" })}</span>
            <span class="wx-tnum" style="font-family:var(--wx-black);font-size:15px;color:var(--c-text)">${escapeHtml(String(h.s))}</span>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

// ===========================================================
// G2 — GEOMETRIC (colour-blocked panels)
// ===========================================================
function renderG2(data) {
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-geo);display:flex;flex-direction:column;background:var(--wx-ink);gap:4px">
      <div style="flex:1;display:flex;gap:4px">
        <div style="flex:1;background:${WX.col("blue")};color:${WX.inkOn("blue")};padding:20px 26px;display:flex;flex-direction:column;justify-content:center">
          <span style="font-family:var(--wx-mono);font-size:12px;font-weight:700;opacity:.85">FROM ${escapeHtml(data.dir || "")}</span>
          <div style="display:flex;align-items:flex-end;gap:6px">
            <span class="wx-tnum" style="font-family:var(--wx-black);font-size:78px;line-height:.8">${escapeHtml(String(data.speed))}</span>
            <span style="font-family:var(--wx-mono);font-size:16px;margin-bottom:12px">${escapeHtml(data.unit || "")}</span>
          </div>
          <span style="font-family:var(--wx-black);font-size:17px;margin-top:4px">${escapeHtml((data.beaufortLabel || "").toUpperCase())}</span>
        </div>
        <div style="width:42%;flex-shrink:0;background:var(--wx-paper);display:flex;align-items:center;justify-content:center">
          ${compass({ bearing: data.bearing || 0, size: 150, color: WX.col("blue") })}
        </div>
      </div>
      <div style="display:flex;gap:4px">
        <div style="flex:1;background:${WX.col("yellow")};color:var(--wx-ink);padding:10px 18px;display:flex;justify-content:space-between;align-items:center">
          <span style="font-family:var(--wx-mono);font-size:12px;font-weight:700;display:flex;align-items:center;gap:8px">${WX.icon("wind", { size: 16, color: "var(--wx-ink)" })}GUST</span>
          <span class="wx-tnum" style="font-family:var(--wx-black);font-size:26px">${escapeHtml(String(data.gust))}<span style="font-size:13px"> ${escapeHtml(data.unit || "")}</span></span>
        </div>
        <div style="flex:1;background:var(--wx-ink);color:var(--wx-paper);padding:10px 18px;display:flex;justify-content:space-between;align-items:center">
          <span style="font-family:var(--wx-mono);font-size:12px;font-weight:700">BEAUFORT</span>
          <span class="wx-tnum" style="font-family:var(--wx-black);font-size:26px">${escapeHtml(String(data.beaufort))}</span>
        </div>
      </div>
    </div>
  `;
}

// ===========================================================
// S3 — SWISS (hairline header, whitespace)
// ===========================================================
function renderS3(data) {
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-swiss);padding:20px 26px;display:flex;flex-direction:column">
      <div style="display:flex;justify-content:space-between;align-items:baseline">
        <span style="font-size:12px;letter-spacing:.2em;font-weight:700;text-transform:uppercase">Wind</span>
        <span style="font-size:12px;letter-spacing:.16em;color:var(--wx-ink-60)">${escapeHtml(data.place || "")} · ${escapeHtml(data.time || "")}</span>
      </div>
      <div style="height:2px;background:var(--wx-ink);margin:12px 0"></div>
      <div style="flex:1;display:flex;align-items:center;gap:26px">
        ${compass({ bearing: data.bearing || 0, size: 132, color: "var(--wx-ink)" })}
        <div style="flex:1">
          <div style="display:flex;align-items:flex-end;gap:8px">
            <span class="wx-tnum" style="font-size:72px;font-weight:300;line-height:.82;letter-spacing:-.02em">${escapeHtml(String(data.speed))}</span>
            <span style="font-size:15px;color:var(--wx-ink-60);margin-bottom:10px">${escapeHtml(data.unit || "")}</span>
          </div>
          <div style="font-size:15px;font-weight:500;margin-top:2px">From ${escapeHtml(data.dir || "")} · ${escapeHtml(data.beaufortLabel || "")}</div>
          <div style="display:flex;gap:28px;margin-top:14px">
            ${[
              ["Gust", `${data.gust} ${data.unit || ""}`, "blue"],
              ["Beaufort", String(data.beaufort), "ink"],
            ].map(([k, v, a]) => `
              <div style="display:flex;flex-direction:column;gap:4px">
                <span style="font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--wx-ink-60);display:flex;align-items:center;gap:6px">
                  <span style="width:6px;height:6px;background:${WX.col(a)};display:inline-block"></span>${k}
                </span>
                <span class="wx-tnum" style="font-size:26px;font-weight:300">${escapeHtml(v)}</span>
              </div>
            `).join("")}
          </div>
        </div>
      </div>
    </div>
  `;
}

// ===========================================================
// D4 — DATA (wind rose + gust trace)
// ===========================================================
function renderD4(data) {
  const series = Array.isArray(data.gustSeries) ? data.gustSeries : [];
  const rose = Array.isArray(data.rose) ? data.rose : [];
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};padding:14px 22px;display:flex;flex-direction:column">
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px">
        <span style="font-family:var(--wx-black);font-size:17px;letter-spacing:.03em">WIND · ${escapeHtml((data.place || "").toUpperCase())}</span>
        <span style="font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink-60)">${escapeHtml(data.time || "")}</span>
      </div>
      <div style="display:flex;gap:24px;flex:1">
        <div style="width:200px;flex-shrink:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px">
          ${windRose({ data: rose, size: 156, color: WX.col("blue") })}
          <span style="font-family:var(--wx-mono);font-size:11.5px;color:var(--wx-ink-60);letter-spacing:.06em">PREVAILING ${escapeHtml(data.dir || "")}</span>
        </div>
        <div style="flex:1;display:flex;flex-direction:column;border-left:1px solid var(--c-line);padding-left:22px">
          <div style="display:flex;align-items:flex-end;gap:8px">
            <span class="wx-tnum" style="font-family:var(--wx-black);font-size:58px;line-height:.82">${escapeHtml(String(data.speed))}</span>
            <span style="font-family:var(--wx-mono);font-size:13px;color:var(--wx-ink-60);margin-bottom:8px">${escapeHtml(data.unit || "")} · GUST ${escapeHtml(String(data.gust))}</span>
          </div>
          <div style="font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink-60);letter-spacing:.04em;margin:2px 0 6px">GUST · NEXT 12H</div>
          <div style="flex:1;min-height:0;position:relative">
            ${areaChart({ series, color: WX.col("blue"), fill: WX.tint("blue") })}
          </div>
          <div style="display:flex;justify-content:space-between;font-family:var(--wx-mono);font-size:11.5px;color:var(--wx-ink-60);margin-top:4px">
            <span>NOW</span><span>+6H</span><span>+12H</span>
          </div>
        </div>
      </div>
    </div>
  `;
}

// ===========================================================
// dispatch
// ===========================================================
const VARIANTS = {
  r1: renderR1,
  g2: renderG2,
  s3: renderS3,
  d4: renderD4,
};

function renderError(msg) {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <div class="root error" style="padding:12px;font-family:system-ui,sans-serif;color:var(--c-danger)">
      <span>${escapeHtml(msg)}</span>
    </div>
  `;
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = renderError(data.error);
    return;
  }
  const variant = ctx.cell.options.variant || "r1";
  const renderer = VARIANTS[variant] || renderR1;
  shadow.innerHTML = renderer(data);
}
