// sky_aurora, Spectra status archetype with a half-circle Kp
// gauge as the hero. The gauge arc sweeps 0-9, the needle marks
// the current value, and a colour band ramps from quiet → severe
// across the dial. Below: a band pill + status grid, then the
// 24-hour Kp forecast as a Chart.js sparkline.

import { sparkline, tokens } from "../../static/spectra-chart.js";

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Kp band → accent token. 0-2 quiet (muted), 3-4 unsettled (slate),
// 5-6 minor / moderate storm (ochre / terracotta), 7-9 severe (plum).
function bandAccent(kp) {
  const v = Number(kp);
  if (!Number.isFinite(v)) return "var(--text-secondary)";
  if (v < 3) return "var(--text-muted)";
  if (v < 5) return "var(--accent-5)";
  if (v < 6) return "var(--accent-2)";
  if (v < 7) return "var(--accent-1)";
  return "var(--accent-6)";
}

function tokenKey(kp) {
  const v = Number(kp);
  if (!Number.isFinite(v)) return "textSecondary";
  if (v < 3) return "textMuted";
  if (v < 5) return "accent5";
  if (v < 6) return "accent2";
  if (v < 7) return "accent1";
  return "accent6";
}

// Half-circle Kp gauge, standard dome (∩) shape with the centre
// near the bottom and the arc curving UP through the top of the
// SVG. Kp 0 at lower-left horizon, Kp 9 at lower-right horizon,
// apex at top-centre. Coloured band ramps quiet → severe; a heavy
// needle pivots at the centre and points UP at the current Kp.
function kpGaugeSvg(kp) {
  const W = 240, H = 140;
  const cx = W / 2;
  const cy = H - 18;
  const r = 100;
  const strokeW = 14;

  // Polar with y-axis flipped (positive sin → up in SVG, since SVG
  // y goes down). 180° = left horizon, 0° = right horizon, 90° =
  // top-centre apex. Kp 0 → 180°, Kp 9 → 0°.
  function polar(angleDeg, radius) {
    const rad = (angleDeg * Math.PI) / 180;
    return { x: cx + radius * Math.cos(rad), y: cy - radius * Math.sin(rad) };
  }
  function kpToAngle(v) {
    const clamped = Math.max(0, Math.min(9, v));
    return 180 - (clamped / 9) * 180;
  }

  // 6 coloured band segments: 0-3 muted, 3-4 slate, 4-5 ochre, 5-6
  // terracotta, 6-7 plum, 7-9 plum-deep. Drawn as separate arcs so
  // a thick contiguous band reads like a horizontal Kp legend.
  const BAND_STOPS = [
    [0, 3, "var(--text-muted)"],
    [3, 5, "var(--accent-5)"],
    [5, 6, "var(--accent-2)"],
    [6, 7, "var(--accent-1)"],
    [7, 9, "var(--accent-6)"],
  ];
  // Each band: arc from p1 (lower Kp, left of dial) to p2 (higher
  // Kp, right of dial). sweep-flag=1 so each segment curves UPWARD
  // through the gauge dome rather than sagging DOWN below the chord
  // (which made every segment look individually upside-down before).
  const bandArcs = BAND_STOPS.map(([lo, hi, color]) => {
    const a1 = kpToAngle(lo);
    const a2 = kpToAngle(hi);
    const p1 = polar(a1, r);
    const p2 = polar(a2, r);
    const large = Math.abs(a1 - a2) > 180 ? 1 : 0;
    return `<path d="M ${p1.x.toFixed(1)} ${p1.y.toFixed(1)}
                    A ${r} ${r} 0 ${large} 1 ${p2.x.toFixed(1)} ${p2.y.toFixed(1)}"
            fill="none" stroke="${color}" stroke-width="${strokeW}" stroke-linecap="butt"/>`;
  }).join("");

  // Tick marks at integer Kp values, sit just OUTSIDE the band
  // pointing radially outward. Major ticks at 0 and 9 carry labels.
  const ticks = [];
  for (let v = 0; v <= 9; v++) {
    const a = kpToAngle(v);
    const inner = polar(a, r + strokeW / 2 + 1);
    const outer = polar(a, r + strokeW / 2 + 6);
    ticks.push(`<line x1="${inner.x.toFixed(1)}" y1="${inner.y.toFixed(1)}"
                       x2="${outer.x.toFixed(1)}" y2="${outer.y.toFixed(1)}"
                       stroke="var(--text-muted)" stroke-width="${v === 0 || v === 9 ? 1.6 : 1}"
                       opacity="${v === 0 || v === 9 ? 0.9 : 0.45}"/>`);
    if (v === 0 || v === 9) {
      const lbl = polar(a, r + strokeW + 12);
      ticks.push(`<text x="${lbl.x.toFixed(1)}" y="${(lbl.y + 3).toFixed(1)}"
                  text-anchor="${v === 0 ? "end" : "start"}"
                  font-size="11" font-weight="800" fill="var(--text-secondary)"
                  font-family="var(--font-family)">Kp ${v}</text>`);
    }
  }

  // Needle, pivots at the centre (near the bottom of the SVG) and
  // points UP toward the current Kp's position on the band arc.
  let needle = "";
  const v = Number(kp);
  if (Number.isFinite(v)) {
    const a = kpToAngle(v);
    const tip = polar(a, r + strokeW / 2 - 2);
    const baseL = polar(a + 90, 7);
    const baseR = polar(a - 90, 7);
    needle = `
      <polygon points="${tip.x.toFixed(1)},${tip.y.toFixed(1)}
                       ${baseL.x.toFixed(1)},${baseL.y.toFixed(1)}
                       ${baseR.x.toFixed(1)},${baseR.y.toFixed(1)}"
               fill="var(--text-primary)"/>
      <circle cx="${cx}" cy="${cy}" r="7" fill="var(--text-primary)"/>
      <circle cx="${cx}" cy="${cy}" r="3" fill="var(--surface)"/>`;
  }

  return `
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet"
         width="100%" height="100%" aria-hidden="true">
      ${bandArcs}
      ${ticks.join("")}
      ${needle}
    </svg>`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="sky_aurora">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Aurora</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const kp = data.current_kp;
  const accent = bandAccent(kp);
  const band = data.band_label || "-";
  const forecastBand = data.forecast_band || "";
  const visibleNow = data.visible_now === true;
  const visibleSoon = data.visible_soon === true;

  const forecast = Array.isArray(data.forecast) ? data.forecast.slice(0, 24) : [];
  const series = forecast.map((f) => Number(f.kp ?? f[1] ?? 0)).filter(Number.isFinite);

  const layout = `
    .aurora-gauge {
      flex: 0 0 auto;
      width: 100%;
      max-height: 11em;
      display: flex;
      justify-content: center;
      padding: var(--space-1) 0;
    }
    .aurora-gauge svg {
      width: auto;
      max-width: 100%;
      max-height: 11em;
    }
    .aurora-hero {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: var(--space-2);
    }
    .aurora-hero-value {
      font-size: var(--fs-hero);
      font-weight: var(--fw-black);
      font-variant-numeric: tabular-nums;
      line-height: 1;
      color: ${accent};
    }
    .aurora-hero-sub {
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      letter-spacing: var(--ls-label);
      text-transform: uppercase;
      color: var(--text-muted);
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="sky_aurora">
      <div class="w-title">
        <i class="ph-bold ph-rainbow" style="color:${accent}"></i>
        <h3>Aurora</h3>
        ${visibleNow ? `<span class="w-title-meta" style="color:var(--accent-3)">VISIBLE</span>`
          : visibleSoon ? `<span class="w-title-meta" style="color:var(--accent-2)">SOON</span>`
          : `<span class="w-title-meta">Kp ${escapeHtml(String(kp ?? "-"))}</span>`}
      </div>
      <div class="w-body status-body">
        <div class="aurora-gauge">${kpGaugeSvg(kp)}</div>
        <div class="aurora-hero">
          <span class="aurora-hero-value">${escapeHtml(String(kp ?? "-"))}</span>
          <div style="display:flex;flex-direction:column;line-height:1.1">
            <span class="aurora-hero-sub">Kp index</span>
            <span class="pill" style="background:${accent};align-self:flex-start;margin-top:2px">${escapeHtml(band)}</span>
          </div>
        </div>
        ${forecastBand ? `<div class="status-grid"><div class="status-cell"><span class="u-label">3-day</span><span class="v">${escapeHtml(forecastBand)}</span></div><div class="status-cell"><span class="u-label">Oval</span><span class="v">${escapeHtml(String(data.forecast_oval ?? "-"))}°</span></div></div>` : ""}
        ${series.length >= 2 ? `<div style="flex:1 1 auto;min-height:2em;position:relative"><canvas></canvas></div>` : ""}
      </div>
    </div>`;

  if (series.length >= 2) {
    const canvas = shadow.querySelector("canvas");
    const t = tokens(shadow.host);
    const lineColor = t[tokenKey(kp)] || t.accent5;
    sparkline(canvas, series, lineColor);
  }
}
