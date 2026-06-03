// sky_moon — Sun & Moon card.
//
// Ships five visual directions, picked per-cell via the ``variant``
// option:
//   legacy   The original moon-only Bauhaus card (preserved as-was).
//   r1       Refined — charcoal header + sun arc + moon disc + a
//            four-cell sun/moon rise/set strip. Primary direction.
//   g2       Geometric — De Stijl colour blocks (sunrise/sunset/
//            moon tiles + a meta strip).
//   s3       Swiss — hairline header, whitespace, light numerals,
//            sun arc + moon disc.
//   d4       Data — sun arc foregrounded via ``WX.sunArc`` + a big
//            moon-phase disc with illum / age meta.
//
// All five render from the same ``ctx.data`` shape that server.py
// returns. Legacy paints from the flat moon-only fields
// (``phase_name``, ``illumination``, ``moonrise``, …); the new
// directions paint from the structured ``sun`` + ``moon`` blocks.
// Both shapes always present, so a cell can flip variants without
// re-fetching.

import { WX } from "../weather_core/static/wx-common.js";

const DEFAULT_FONT = "var(--wx-grotesk)";

function escapeHtml(s) { return WX.escapeHtml(s); }

function timeStr(iso) {
  if (!iso) return "—";
  const m = String(iso).match(/T(\d{2}):(\d{2})/);
  return m ? `${m[1]}:${m[2]}` : "—";
}

function shortDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const weekday = d.toLocaleDateString([], { weekday: "short" });
  const day = d.getDate();
  const month = d.toLocaleDateString([], { month: "short" });
  return `${weekday} ${day} ${month}`;
}

function nowTime() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

// SVG path for the lit portion of the moon. Centred at (0, 0), radius r.
//
// Convention: in the northern hemisphere the lit hemisphere is on the
// RIGHT when waxing; in the southern hemisphere it's on the LEFT. The
// terminator (line between lit and dark) is half of an ellipse whose
// horizontal semi-axis collapses to zero at first/last quarter and
// reaches full-radius at new and full. The sweep flag on the inner arc
// flips at quarter so the terminator stays on the correct side of the
// disc as phase progresses.
function moonLitPath(fraction, r, southern) {
  const phaseAngle = fraction * 2 * Math.PI;
  const k = Math.cos(phaseAngle); // 1 at new, 0 at quarter, -1 at full
  const rx = Math.abs(r * k);
  const waxing = fraction < 0.5;
  const litRight = waxing !== southern;
  const outerSweep = litRight ? 1 : 0;
  const terminatorSweep = k > 0 ? outerSweep : 1 - outerSweep;
  return `M 0 ${-r}
          A ${r} ${r} 0 0 ${outerSweep} 0 ${r}
          A ${rx.toFixed(2)} ${r} 0 0 ${terminatorSweep} 0 ${-r}
          Z`;
}

// The "design handoff" moon disc — paints the lit fraction with a
// clipped offset circle (the simple, basic-geometry approach noted in
// the README's data-viz section). Less astronomically faithful than
// ``moonLitPath`` above, but it's what the design specifies for the
// compact disc on r1/g2/s3/d4.
function moonDiscSvg({ size = 96, illum = 0, rim = "var(--wx-ink)", lit = "var(--wx-yellow)", dark = "var(--wx-paper-3)" } = {}) {
  const R = size / 2 - 2;
  const cx = size / 2;
  const k = Math.max(0, Math.min(100, Number(illum) || 0)) / 100;
  const dx = 2 * R * k;
  const id = `mp${Math.round(k * 1000)}-${size}-${Math.floor(Math.random() * 1e6)}`;
  return `
    <svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" aria-hidden="true">
      <defs><clipPath id="${id}"><circle cx="${cx}" cy="${cx}" r="${R}" /></clipPath></defs>
      <circle cx="${cx}" cy="${cx}" r="${R}" fill="${lit}" />
      <circle cx="${cx + dx}" cy="${cx}" r="${R}" fill="${dark}" clip-path="url(#${id})" />
      <circle cx="${cx}" cy="${cx}" r="${R}" fill="none" stroke="${rim}" stroke-width="2.5" />
    </svg>
  `;
}

function styleBlock() {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus-wx.css">
    <link rel="stylesheet" href="/plugins/sky_moon/client.css">
  `;
}

// ===========================================================
// LEGACY — the original moon-only Bauhaus card (preserved as-was)
// ===========================================================
function renderLegacy(data, size) {
  // Hemisphere convention: server passes a negative latitude when the
  // viewer is below the equator, in which case the lit hemisphere
  // mirrors. (Defaults to northern view when lat is unknown.)
  const southern = (data.lat ?? 0) < 0;
  const litPath = moonLitPath(data.fraction || 0, 50, southern);

  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/sky_moon/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="mn-title">${escapeHtml(data.label || "Moon")}</span>
        <i class="ph-bold ph-moon-stars wb-bar-icon"></i>
      </header>

      <section class="mn-hero">
        <div class="mn-disc" aria-hidden="true">
          <svg viewBox="-55 -55 110 110" preserveAspectRatio="xMidYMid meet" class="mn-svg">
            <circle cx="0" cy="0" r="50" class="mn-shadow" />
            <path d="${litPath}" class="mn-lit" />
            <circle cx="0" cy="0" r="50" class="mn-rim" />
          </svg>
        </div>
        <div class="mn-text">
          <div class="mn-name">${escapeHtml(data.phase_name || "—")}</div>
          <div class="mn-illum">
            <span class="mn-illum-v">${data.illumination != null ? Math.round(data.illumination) : "—"}</span>
            <span class="mn-illum-u">%</span>
            <span class="mn-illum-lbl">lit</span>
          </div>
          <div class="mn-age">
            <i class="ph-bold ph-clock-countdown"></i>
            <span>Day ${data.age_days ?? "—"} of cycle</span>
          </div>
        </div>
      </section>

      <section class="mn-stats">
        <div class="mn-stat mn-stat--accent">
          <i class="ph-bold ph-circle-dashed mn-stat-icon"></i>
          <span class="mn-stat-label">Next new</span>
          <span class="mn-stat-value">${escapeHtml(shortDate(data.next_new))}</span>
        </div>
        <div class="mn-stat mn-stat--surface">
          <i class="ph-bold ph-circle-half mn-stat-icon"></i>
          <span class="mn-stat-label">First qtr</span>
          <span class="mn-stat-value">${escapeHtml(shortDate(data.next_first_quarter))}</span>
        </div>
        <div class="mn-stat mn-stat--accent2">
          <i class="ph-bold ph-circle mn-stat-icon"></i>
          <span class="mn-stat-label">Next full</span>
          <span class="mn-stat-value">${escapeHtml(shortDate(data.next_full))}</span>
        </div>
        <div class="mn-stat mn-stat--accent3">
          <i class="ph-bold ph-arrow-up mn-stat-icon"></i>
          <span class="mn-stat-label">Moonrise</span>
          <span class="mn-stat-value">${escapeHtml(timeStr(data.moonrise))}</span>
        </div>
      </section>
    </div>
  `;
}

// ===========================================================
// Helpers for the four new directions
// ===========================================================
function sunHas(data) {
  const s = data.sun || {};
  return s.riseMin != null && s.setMin != null && s.nowMin != null;
}

function sunArc(data, color, w, h) {
  const s = data.sun || {};
  return WX.sunArc({ rise: s.riseMin, set: s.setMin, now: s.nowMin, color, width: w, height: h });
}

function moonBlock(data) {
  return data.moon || {};
}

// ===========================================================
// R1 — REFINED — charcoal header + sun arc + moon disc + rise/set strip.
// ===========================================================
function renderR1(data) {
  const moon = moonBlock(data);
  const sun = data.sun || {};
  const cells = [
    ["sunrise", "SUNRISE", sun.rise || "—", "yellow"],
    ["sunset", "SUNSET", sun.set || "—", "ink"],
    ["moon", "MOONRISE", moon.rise || "—", "blue"],
    ["moon", "MOONSET", moon.set || "—", "ink"],
  ];
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};display:flex;flex-direction:column">
      ${WX.darkHeader({ title: (data.place || data.label || "—") + " · SUN & MOON", accent: "yellow", right: data.time || nowTime() })}
      <div style="flex:1;display:flex;align-items:center;padding:14px 24px;gap:20px;min-height:0">
        <div style="flex:1;min-width:0;display:flex;flex-direction:column;align-items:center;gap:6px">
          ${sunHas(data) ? sunArc(data, WX.col("yellow"), 320, 130).replace("<svg ", '<svg style="max-width:100%;height:auto" ') : ""}
          <span style="font-family:var(--wx-mono);font-size:12px;letter-spacing:.06em;color:var(--wx-ink-60)">DAY LENGTH ${escapeHtml(sun.dayLength || "—")} · NOON ${escapeHtml(sun.solarNoon || "—")}</span>
        </div>
        <div style="box-sizing:border-box;width:220px;flex-shrink:0;display:flex;align-items:center;gap:14px;border-left:1px solid var(--c-line);padding-left:22px">
          ${moonDiscSvg({ size: 92, illum: moon.illum })}
          <div>
            <div style="font-weight:800;font-size:16px;line-height:1.1">${escapeHtml(moon.phase || "—")}</div>
            <div style="font-family:var(--wx-mono);font-size:12px;color:var(--wx-ink-60);margin-top:4px">${moon.illum ?? "—"}% LIT</div>
            <div style="font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink-60);margin-top:2px">${escapeHtml(moon.next || "")}</div>
          </div>
        </div>
      </div>
      <div style="display:flex;border-top:2px solid var(--wx-ink)">
        ${cells.map(([ic, l, v, a], i) => `
          <div style="flex:1;display:flex;flex-direction:column;justify-content:center;gap:5px;padding:11px 16px;border-right:${i < 3 ? "1px solid var(--c-line)" : "none"}">
            <span style="display:flex;align-items:center;gap:7px;font-family:var(--wx-mono);font-size:11.5px;letter-spacing:.08em;color:var(--wx-ink-60)">
              ${WX.icon(ic, { size: 14, color: WX.col(a) })}${escapeHtml(l)}
            </span>
            <span class="wx-tnum" style="font-family:var(--wx-black);font-size:20px">${escapeHtml(v)}</span>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

// ===========================================================
// G2 — GEOMETRIC (De Stijl colour blocks)
// ===========================================================
function renderG2(data) {
  const moon = moonBlock(data);
  const sun = data.sun || {};
  const meta = [
    ["DAY LENGTH", sun.dayLength || "—"],
    ["SOLAR NOON", sun.solarNoon || "—"],
    ["MOONRISE", moon.rise || "—"],
    ["MOONSET", moon.set || "—"],
  ];
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-geo);display:flex;flex-direction:column;background:var(--wx-ink);gap:4px">
      <div style="flex:1;display:flex;gap:4px">
        <div style="flex:1;background:${WX.col("yellow")};color:var(--wx-ink);padding:16px 22px;display:flex;flex-direction:column;justify-content:space-between">
          <div style="display:flex;justify-content:space-between;align-items:flex-start">
            <span style="font-family:var(--wx-mono);font-size:11px;font-weight:700">SUNRISE</span>
            ${WX.icon("sunrise", { size: 22, color: "var(--wx-ink)" })}
          </div>
          <span class="wx-tnum" style="font-family:var(--wx-black);font-size:46px;line-height:.85">${escapeHtml(sun.rise || "—")}</span>
        </div>
        <div style="flex:1;background:var(--wx-ink);color:var(--wx-paper);padding:16px 22px;display:flex;flex-direction:column;justify-content:space-between">
          <div style="display:flex;justify-content:space-between;align-items:flex-start">
            <span style="font-family:var(--wx-mono);font-size:11px;font-weight:700">SUNSET</span>
            ${WX.icon("sunset", { size: 22, color: "var(--wx-paper)" })}
          </div>
          <span class="wx-tnum" style="font-family:var(--wx-black);font-size:46px;line-height:.85">${escapeHtml(sun.set || "—")}</span>
        </div>
        <div style="width:38%;flex-shrink:0;background:${WX.col("blue")};color:${WX.inkOn("blue")};padding:16px 22px;display:flex;flex-direction:column;justify-content:space-between">
          <span style="font-family:var(--wx-mono);font-size:11px;font-weight:700">${escapeHtml((moon.phase || "MOON").toUpperCase())}</span>
          <div style="display:flex;align-items:center;gap:14px">
            ${moonDiscSvg({ size: 72, illum: moon.illum, rim: WX.inkOn("blue") })}
            <span class="wx-tnum" style="font-family:var(--wx-black);font-size:40px;line-height:.85">${moon.illum ?? "—"}<span style="font-size:16px">%</span></span>
          </div>
        </div>
      </div>
      <div style="display:flex;gap:4px">
        ${meta.map(([l, v]) => `
          <div style="flex:1;background:var(--wx-paper);color:var(--wx-ink);padding:9px 16px;display:flex;justify-content:space-between;align-items:center;font-family:var(--wx-mono);font-size:12px;font-weight:700">
            <span style="color:var(--wx-ink-60)">${escapeHtml(l)}</span><span class="wx-tnum">${escapeHtml(v)}</span>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

// ===========================================================
// S3 — SWISS (hairline header, whitespace, light numerals)
// ===========================================================
function renderS3(data) {
  const moon = moonBlock(data);
  const sun = data.sun || {};
  const meta = [
    ["Day length", sun.dayLength || "—"],
    ["Solar noon", sun.solarNoon || "—"],
    ["Moonrise", moon.rise || "—"],
    ["Moonset", moon.set || "—"],
  ];
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-swiss);padding:20px 26px;display:flex;flex-direction:column">
      <div style="display:flex;justify-content:space-between;align-items:baseline">
        <span style="font-size:12px;letter-spacing:.2em;font-weight:700;text-transform:uppercase">Sun &amp; Moon</span>
        <span style="font-size:12px;letter-spacing:.16em;color:var(--wx-ink-60)">${escapeHtml(data.place || data.label || "")} · ${escapeHtml(data.time || nowTime())}</span>
      </div>
      <div style="height:2px;background:var(--wx-ink);margin:12px 0"></div>
      <div style="flex:1;display:flex;align-items:center;gap:24px">
        <div style="flex:1;display:flex;justify-content:center">${sunHas(data) ? sunArc(data, "var(--wx-ink)", 380, 140) : ""}</div>
        <div style="display:flex;align-items:center;gap:14px;border-left:1px solid var(--c-line);padding-left:22px">
          ${moonDiscSvg({ size: 84, illum: moon.illum })}
          <div>
            <div style="font-size:15px;font-weight:500">${escapeHtml(moon.phase || "—")}</div>
            <div style="font-size:11.5px;color:var(--wx-ink-60);margin-top:3px">${moon.illum ?? "—"}% illuminated</div>
          </div>
        </div>
      </div>
      <div style="display:flex;gap:28px;margin-top:8px">
        ${meta.map(([k, v]) => `
          <div style="display:flex;flex-direction:column;gap:4px">
            <span style="font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--wx-ink-60)">${escapeHtml(k)}</span>
            <span class="wx-tnum" style="font-size:22px;font-weight:300">${escapeHtml(v)}</span>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

// ===========================================================
// D4 — DATA (sun arc foregrounded + big moon-phase disc)
// ===========================================================
function renderD4(data) {
  const moon = moonBlock(data);
  const sun = data.sun || {};
  const stats = [
    ["sunrise", "RISE", sun.rise || "—"],
    ["noon", "NOON", sun.solarNoon || "—"],
    ["sunset", "SET", sun.set || "—"],
    ["daylength", "LENGTH", sun.dayLength || "—"],
  ];
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};padding:14px 22px;display:flex;flex-direction:column">
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px">
        <span style="font-family:var(--wx-black);font-size:17px;letter-spacing:.03em">SUN &amp; MOON · ${escapeHtml((data.place || data.label || "").toUpperCase())}</span>
        <span style="font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink-60)">${escapeHtml(data.time || nowTime())}</span>
      </div>
      <div style="display:flex;gap:24px;flex:1;min-height:0">
        <div style="flex:1;min-width:0;display:flex;flex-direction:column;justify-content:center">
          ${sunHas(data) ? `<div style="display:flex;justify-content:center;max-width:100%;overflow:hidden">${sunArc(data, WX.col("yellow"), 420, 150).replace("<svg ", '<svg style="max-width:100%;height:auto" ')}</div>` : ""}
          <div style="display:flex;justify-content:space-around;margin-top:6px">
            ${stats.map(([ic, l, v]) => `
              <div style="display:flex;flex-direction:column;align-items:center;gap:3px">
                ${WX.icon(ic, { size: 16, color: WX.col("yellow") })}
                <span style="font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink-60);letter-spacing:.06em">${escapeHtml(l)}</span>
                <span class="wx-tnum" style="font-family:var(--wx-black);font-size:15px">${escapeHtml(v)}</span>
              </div>
            `).join("")}
          </div>
        </div>
        <div style="box-sizing:border-box;width:230px;flex-shrink:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;border-left:1px solid var(--c-line);padding-left:22px">
          ${moonDiscSvg({ size: 120, illum: moon.illum })}
          <span style="font-family:var(--wx-black);font-size:17px">${escapeHtml(moon.phase || "—")}</span>
          <div style="display:flex;gap:14px;font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink-60)">
            <span>${moon.illum ?? "—"}% LIT</span><span>DAY ${moon.age ?? "—"}</span>
          </div>
        </div>
      </div>
    </div>
  `;
}

// ===========================================================
// Variant dispatch
// ===========================================================
const VARIANTS = {
  legacy: renderLegacy,
  r1: renderR1,
  g2: renderG2,
  s3: renderS3,
  d4: renderD4,
};

function renderError(msg) {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/plugins/sky_moon/client.css">
    <div class="root error">
      <i class="ph ph-warning-circle" aria-hidden="true"></i>
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
  const size = ctx.cell.size;
  const variant = ctx.cell.options.variant || "legacy";
  const renderer = VARIANTS[variant] || renderLegacy;
  shadow.innerHTML = renderer(data, size);
}
