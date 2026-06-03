// weather_hourly — next-24-hours card.
//
// Ships five visual directions, picked per-cell via the ``variant``
// option:
//   legacy   The original Bauhaus card we shipped pre-handoff —
//            paints from data.points via Chart.js. Window length
//            honours the ``hours`` option (12 / 24 / 48).
//   r1       Refined — charcoal header + hour-icon strip +
//            temperature area chart + rain probability strip + a
//            high/low/now chip row. The "primary" direction.
//   g2       Geometric — De Stijl colour blocks, Archivo Black
//            numerals, blocky chip row.
//   s3       Swiss — hairline header, light numerals, whitespace.
//   d4       Data — gridlines behind the temperature trace, mono
//            meta header.
//
// All five render from the same ``ctx.data`` shape that server.py
// returns. Legacy reads ``points`` + ``max`` / ``min`` / ``current``;
// the new directions read the fixed 24-slot arrays ``temps``,
// ``rain``, ``hoursArr``, ``axis`` plus ``hi`` / ``lo`` / ``now`` /
// ``tMin`` / ``tMax``. Both shapes are always present, so a cell
// can flip variants without re-fetching.

import { WX } from "../weather_core/static/wx-common.js";

const DEFAULT_FONT = "var(--wx-grotesk)";

function escapeHtml(s) { return WX.escapeHtml(s); }
function fmtTemp(v) { return v == null ? "—" : Math.round(v) + "°"; }
function nowTime() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

// ===========================================================
// LEGACY — original Chart.js card (preserved as-was)
// ===========================================================
function loadChart() {
  if (window.Chart) return Promise.resolve(window.Chart);
  if (window.__tesseraeChartJs) return window.__tesseraeChartJs;
  window.__tesseraeChartJs = new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = (window.TESSERAE_URL_PREFIX || "") + "/static/vendor/chart.umd.min.js";
    s.async = true;
    s.onload = () => resolve(window.Chart);
    s.onerror = () => reject(new Error("failed to load chart.js"));
    document.head.appendChild(s);
  });
  return window.__tesseraeChartJs;
}

const WMO_ICON = {
  0:  { day: "sun",             night: "moon" },
  1:  { day: "sun",             night: "moon" },
  2:  { day: "cloud-sun",       night: "cloud-moon" },
  3:  { day: "cloud",           night: "cloud" },
  45: { day: "cloud-fog",       night: "cloud-fog" },
  48: { day: "cloud-fog",       night: "cloud-fog" },
  51: { day: "cloud-rain",      night: "cloud-rain" },
  53: { day: "cloud-rain",      night: "cloud-rain" },
  55: { day: "cloud-rain",      night: "cloud-rain" },
  56: { day: "snowflake",       night: "snowflake" },
  57: { day: "snowflake",       night: "snowflake" },
  61: { day: "cloud-rain",      night: "cloud-rain" },
  63: { day: "cloud-rain",      night: "cloud-rain" },
  65: { day: "cloud-rain",      night: "cloud-rain" },
  66: { day: "snowflake",       night: "snowflake" },
  67: { day: "snowflake",       night: "snowflake" },
  71: { day: "snowflake",       night: "snowflake" },
  73: { day: "snowflake",       night: "snowflake" },
  75: { day: "snowflake",       night: "snowflake" },
  77: { day: "snowflake",       night: "snowflake" },
  80: { day: "cloud-rain",      night: "cloud-rain" },
  81: { day: "cloud-rain",      night: "cloud-rain" },
  82: { day: "cloud-rain",      night: "cloud-rain" },
  85: { day: "snowflake",       night: "snowflake" },
  86: { day: "snowflake",       night: "snowflake" },
  95: { day: "cloud-lightning", night: "cloud-lightning" },
  96: { day: "cloud-lightning", night: "cloud-lightning" },
  99: { day: "cloud-lightning", night: "cloud-lightning" },
};

function iconForPoint(p) {
  const entry = WMO_ICON[p.code];
  if (!entry) return "cloud";
  return p.is_day !== false ? entry.day : entry.night;
}

function hexToRgba(hex, alpha) {
  if (typeof hex !== "string" || !/^#[0-9a-f]{6}$/i.test(hex)) return `rgba(0,0,0,${alpha})`;
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function labelEvery(points, size) {
  const target = size === "sm" ? 4 : size === "md" ? 6 : 8;
  return Math.max(1, Math.ceil(points.length / target));
}

function sampleIndexes(total, want) {
  if (total <= want) return Array.from({ length: total }, (_, i) => i);
  const out = [];
  for (let i = 0; i < want; i++) {
    out.push(Math.round((i / (want - 1)) * (total - 1)));
  }
  return out;
}

function renderConditionStrip(points, size) {
  const want = size === "md" ? 8 : 12;
  const idxs = sampleIndexes(points.length, want);
  return idxs
    .map((i) => {
      const p = points[i];
      const icon = iconForPoint(p);
      return `
        <div class="wh-cond-cell">
          <i class="ph-bold ph-${icon}" aria-hidden="true"></i>
          <span class="wh-cond-hour">${p.hour}</span>
        </div>
      `;
    })
    .join("");
}

function renderHourlyList(points) {
  const idxs = sampleIndexes(points.length, 12);
  return idxs
    .map((i) => {
      const p = points[i];
      const icon = iconForPoint(p);
      const rainPct = p.rain == null ? 0 : Math.max(0, Math.min(100, p.rain));
      return `
        <div class="wh-hour">
          <i class="ph-bold ph-${icon} wh-hour-icon" aria-hidden="true"></i>
          <span class="wh-hour-label">${p.hour}:00</span>
          <span class="wh-hour-temp">${Math.round(p.temp)}°</span>
          <div class="wh-hour-rain" aria-hidden="true">
            <span class="wh-hour-rain-fill" style="width: ${rainPct}%"></span>
          </div>
        </div>
      `;
    })
    .join("");
}

function renderRainBars(points) {
  return points
    .map((p) => {
      const pct = p.rain == null ? 0 : Math.max(0, Math.min(100, p.rain));
      const wet = pct >= 30;
      return `<span class="wh-rain-bar${wet ? " is-wet" : ""}" style="--rain: ${pct}%" title="${pct}% at ${p.hour}:00"></span>`;
    })
    .join("");
}

function legacyHtml(data, size) {
  const points = Array.isArray(data.points) ? data.points : [];
  const showStrip = size === "md" || size === "lg";
  const showRain = size === "md" || size === "lg";
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/weather_hourly/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="wh-title">${data.label ? escapeHtml(data.label) + " · " : ""}Next ${data.hours || 24} hr</span>
        <span class="wh-time">${nowTime()}</span>
      </header>
      ${showStrip ? `
      <section class="wh-cond-strip" aria-label="Hourly conditions">
        ${renderConditionStrip(points, size)}
      </section>` : ""}
      <section class="wh-chart">
        <canvas class="chart"></canvas>
      </section>
      <section class="wh-hours" aria-label="Hourly breakdown">
        ${renderHourlyList(points)}
      </section>
      ${showRain ? `
      <section class="wh-rain">
        <span class="wh-rain-label">Rain</span>
        <div class="wh-rain-bars">
          ${renderRainBars(points)}
        </div>
      </section>` : ""}
      <section class="wh-chips">
        <div class="wh-chip wh-chip--high">
          <span class="wh-chip-label">High</span>
          <span class="wh-chip-value">${fmtTemp(data.max)}</span>
        </div>
        <div class="wh-chip wh-chip--low">
          <span class="wh-chip-label">Low</span>
          <span class="wh-chip-value">${fmtTemp(data.min)}</span>
        </div>
        ${data.current != null ? `
        <div class="wh-chip wh-chip--current">
          <span class="wh-chip-label">Now</span>
          <span class="wh-chip-value">${fmtTemp(data.current)}</span>
        </div>` : ""}
      </section>
    </div>
  `;
}

async function paintLegacyChart(shadow, ctx, data, size) {
  let Chart;
  try {
    Chart = await loadChart();
  } catch (err) {
    shadow.innerHTML = renderError(err.message || "chart.js load failed");
    return;
  }
  const canvas = shadow.querySelector(".chart");
  if (!canvas) return;
  const points = Array.isArray(data.points) ? data.points : [];
  const t = ctx.theme;
  const step = labelEvery(points, size);
  const labels = points.map((p, i) => (i % step === 0 ? `${p.hour}:00` : ""));
  const temps = points.map((p) => p.temp);

  const fontFamily =
    ctx.font?.family || 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
  const baseFont = {
    family: fontFamily,
    size: size === "lg" ? 13 : 11,
    weight: "700",
  };

  new Chart(canvas.getContext("2d"), {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          data: temps,
          borderColor: t.accent,
          backgroundColor: hexToRgba(t.accent, 0.16),
          borderWidth: 3,
          tension: 0.35,
          fill: true,
          pointRadius: 0,
          pointHoverRadius: 0,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: {
        x: {
          grid: { display: false },
          border: { display: false },
          ticks: {
            color: t.fgSoft,
            font: baseFont,
            autoSkip: false,
            maxRotation: 0,
            callback(_value, index) { return labels[index] || ""; },
          },
        },
        y: {
          grid: { display: false },
          border: { display: false },
          ticks: {
            color: t.fgSoft,
            font: baseFont,
            callback: (v) => `${Math.round(v)}°`,
            maxTicksLimit: 4,
          },
        },
      },
      layout: { padding: { top: 8, right: 12, bottom: 0, left: 0 } },
    },
  });
}

// ===========================================================
// Shared helpers for the four new directions
// ===========================================================
function styleBlock() {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus-wx.css">
    <link rel="stylesheet" href="/plugins/weather_hourly/client.css">
  `;
}

// Inline SVG temperature area chart. Mirrors WX_UI.Area in the
// handoff: a smooth-ish line over a tinted fill, drawn into a
// preserveAspectRatio="none" viewBox so the chart stretches to its
// container; the stroke uses vector-effect non-scaling-stroke so it
// stays crisp at any width.
function areaChart({ series, min, max, w = 620, h = 170, pad = 8, color, fill = "" } = {}) {
  if (!Array.isArray(series) || series.length === 0) return "";
  const span = (max - min) || 1;
  const n = series.length;
  const X = (i) => (i / (n - 1)) * w;
  const Y = (v) => h - pad - ((v - min) / span) * (h - pad * 2);
  let line = "";
  series.forEach((v, i) => {
    line += (i ? "L" : "M") + X(i).toFixed(1) + " " + Y(v).toFixed(1) + " ";
  });
  const area =
    `M0 ${h} L0 ${Y(series[0]).toFixed(1)} ` +
    series.map((v, i) => `L${X(i).toFixed(1)} ${Y(v).toFixed(1)}`).join(" ") +
    ` L${w} ${h} Z`;
  return `
    <svg width="100%" height="100%" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="display:block">
      ${fill ? `<path d="${area}" fill="${fill}" stroke="none" />` : ""}
      <path d="${line.trim()}" fill="none" stroke="${color}" stroke-width="2.5" stroke-linejoin="round" vector-effect="non-scaling-stroke" />
    </svg>
  `;
}

// Inline rain-probability strip. Each hour gets a flex-1 column;
// the column fills to ``max(p, 6)%`` of the strip height so even
// dry hours show a hairline; wet hours (>= threshold) render in
// the accent colour rather than the muted bg.
function rainStrip({ rain, color = "var(--wx-red)", height = 16, threshold = 25 } = {}) {
  if (!Array.isArray(rain) || rain.length === 0) return "";
  const cells = rain
    .map((p) => {
      const pct = Number.isFinite(p) ? Math.max(0, Math.min(100, p)) : 0;
      const fillH = Math.max(pct, 6);
      const bg = pct >= threshold ? color : "var(--c-line)";
      return `<div style="flex:1;height:${fillH}%;min-height:3px;background:${bg}"></div>`;
    })
    .join("");
  return `<div style="display:flex;align-items:flex-end;gap:2px;height:${height}px;width:100%">${cells}</div>`;
}

// Per-hour icon row used by every new variant. The handoff sample
// shows ~12 hour glyphs across a 24-hour window — we downsample to
// keep the row uncrowded.
function hourRow(data, { small = false } = {}) {
  const arr = Array.isArray(data.hoursArr) ? data.hoursArr : [];
  if (!arr.length) return "";
  const want = Math.min(12, arr.length);
  const idxs = [];
  for (let i = 0; i < want; i++) {
    idxs.push(Math.round((i / Math.max(1, want - 1)) * (arr.length - 1)));
  }
  const iconSize = small ? 22 : 26;
  return `
    <div style="display:flex;justify-content:space-between;padding:0 4px">
      ${idxs.map((i) => {
        const h = arr[i];
        return `
          <div style="display:flex;flex-direction:column;align-items:center;gap:3px">
            ${WX.icon(h.icon || "cloud", { size: iconSize, color: "var(--wx-ink)" })}
            <span style="font-family:var(--wx-mono);font-size:10px;color:var(--wx-ink-60)">${escapeHtml(h.t || "")}</span>
          </div>
        `;
      }).join("")}
    </div>
  `;
}

// Mini y-axis labels — evenly-spaced between tMax and tMin. The
// handoff draws four ticks; we honour that, rounding to whole
// degrees so the column stays narrow.
function yAxisLabels(data, count = 4) {
  const hi = Number(data.tMax);
  const lo = Number(data.tMin);
  if (!Number.isFinite(hi) || !Number.isFinite(lo)) return "";
  const labels = [];
  for (let i = 0; i < count; i++) {
    const v = hi - ((hi - lo) * i) / (count - 1);
    labels.push(Math.round(v));
  }
  return `
    <div class="wx-tnum" style="display:flex;flex-direction:column;justify-content:space-between;font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink-60)">
      ${labels.map((l) => `<span>${l}°</span>`).join("")}
    </div>
  `;
}

function axisRow(data, { font = "var(--wx-mono)", size = 10 } = {}) {
  const axis = Array.isArray(data.axis) ? data.axis : [];
  if (!axis.length) return "";
  return `
    <div style="display:flex;justify-content:space-between;font-family:${font};font-size:${size}px;color:var(--wx-ink-60)">
      ${axis.map((a) => `<span>${escapeHtml(a)}</span>`).join("")}
    </div>
  `;
}

// ===========================================================
// R1 — REFINED
// Charcoal header + hour-icon strip + area chart + rain strip +
// high/low/now chip row.
// ===========================================================
function renderR1(data) {
  const place = data.place || data.label || "—";
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};display:flex;flex-direction:column">
      ${WX.darkHeader({ title: `${place} · NEXT 24 HR`, accent: "blue", right: data.time || nowTime() })}
      <div style="padding:12px 20px 0">${hourRow(data)}</div>
      <div style="flex:1;display:flex;padding:10px 20px 6px;gap:10px;min-height:0">
        ${yAxisLabels(data)}
        <div style="flex:1;position:relative">
          ${areaChart({ series: data.temps, min: data.tMin, max: data.tMax, w: 620, h: 170, color: WX.col("red"), fill: "var(--wx-red-t)" })}
        </div>
      </div>
      <div style="padding:0 20px">${axisRow(data)}</div>
      <div style="display:flex;align-items:center;gap:12px;padding:10px 20px 6px">
        <span style="font-family:var(--wx-mono);font-size:11px;font-weight:700;letter-spacing:.08em;color:var(--wx-ink-60);width:40px">RAIN</span>
        <div style="flex:1">${rainStrip({ rain: data.rain, color: WX.col("red") })}</div>
      </div>
      <div style="display:flex;border-top:2px solid var(--wx-ink)">
        ${[["HIGH", data.hi, "ink"], ["LOW", data.lo, "blue"], ["NOW", data.now, "red"]].map(([l, v, a], i) => `
          <div style="flex:1;padding:9px 18px;border-right:${i < 2 ? "1px solid var(--c-line)" : "none"};display:flex;align-items:baseline;gap:10px">
            <span style="font-family:var(--wx-mono);font-size:11px;letter-spacing:.08em;color:var(--wx-ink-60)">${l}</span>
            <span class="wx-tnum" style="font-family:var(--wx-black);font-size:24px;color:${WX.col(a)}">${fmtTemp(v)}</span>
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
  const place = data.place || data.label || "—";
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-geo);display:flex;flex-direction:column;background:var(--wx-ink);gap:4px">
      <div style="background:var(--wx-ink);color:var(--wx-paper);padding:8px 16px;display:flex;align-items:center;gap:10px">
        <span style="width:13px;height:13px;background:${WX.col("blue")};flex-shrink:0"></span>
        <span style="font-family:var(--wx-black);font-size:15px">${escapeHtml(place.toUpperCase())} · 24 HR</span>
      </div>
      <div style="background:var(--wx-paper);padding:10px 18px">${hourRow(data, { small: true })}</div>
      <div style="flex:1;background:var(--wx-paper);display:flex;padding:8px 18px;gap:10px;min-height:0">
        ${yAxisLabels(data)}
        <div style="flex:1;position:relative">
          ${areaChart({ series: data.temps, min: data.tMin, max: data.tMax, w: 620, h: 150, color: WX.col("red"), fill: "var(--wx-red-t)" })}
        </div>
      </div>
      <div style="background:${WX.col("red")};padding:8px 18px;display:flex;align-items:center;gap:12px">
        <span style="font-family:var(--wx-black);font-size:13px;color:${WX.inkOn("blue")}">RAIN</span>
        <div style="flex:1">${rainStrip({ rain: data.rain, color: "var(--wx-ink)", threshold: 25 })}</div>
      </div>
      <div style="display:flex;gap:4px">
        ${[["HIGH", data.hi, "ink"], ["LOW", data.lo, "blue"], ["NOW", data.now, "yellow"]].map(([l, v, a]) => `
          <div style="flex:1;background:${WX.col(a)};color:${WX.inkOn(a)};padding:8px 18px;display:flex;justify-content:space-between;align-items:baseline">
            <span style="font-family:var(--wx-mono);font-size:12px;font-weight:700">${l}</span>
            <span class="wx-tnum" style="font-family:var(--wx-black);font-size:26px">${fmtTemp(v)}</span>
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
  const place = data.place || data.label || "";
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-swiss);padding:18px 24px;display:flex;flex-direction:column">
      <div style="display:flex;justify-content:space-between;align-items:baseline">
        <span style="font-size:12px;letter-spacing:.2em;font-weight:700;text-transform:uppercase">Next 24 Hours</span>
        <span style="font-size:10.5px;letter-spacing:.16em;color:var(--wx-ink-60)">${escapeHtml(place)}</span>
      </div>
      <div style="height:2px;background:var(--wx-ink);margin:12px 0"></div>
      <div style="flex:1;display:flex;gap:10px;min-height:0">
        ${yAxisLabels(data)}
        <div style="flex:1;position:relative">
          ${areaChart({ series: data.temps, min: data.tMin, max: data.tMax, w: 620, h: 200, color: "var(--wx-ink)" })}
        </div>
      </div>
      <div style="border-top:1px solid var(--c-line);padding-top:6px;margin-top:6px">${axisRow(data)}</div>
      <div style="display:flex;gap:36px;margin-top:12px">
        ${[["High", data.hi], ["Low", data.lo], ["Now", data.now]].map(([l, v]) => `
          <div style="display:flex;align-items:baseline;gap:8px">
            <span class="wx-tnum" style="font-size:26px;font-weight:300">${fmtTemp(v)}</span>
            <span style="font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--wx-ink-60)">${l}</span>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

// ===========================================================
// D4 — DATA (gridlines behind the trace, mono meta header)
// ===========================================================
function renderD4(data) {
  const place = data.place || data.label || "";
  const w = 620;
  const h = 170;
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};padding:14px 20px;display:flex;flex-direction:column">
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px">
        <span style="font-family:var(--wx-black);font-size:17px;letter-spacing:.03em">NEXT 24 HR · ${escapeHtml(place.toUpperCase())}</span>
        <span style="font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink-60)">NOW ${fmtTemp(data.now)} · H ${fmtTemp(data.hi)} · L ${fmtTemp(data.lo)}</span>
      </div>
      <div style="padding:0 0 6px">${hourRow(data, { small: true })}</div>
      <div style="flex:1;display:flex;gap:10px;min-height:0">
        ${yAxisLabels(data)}
        <div style="flex:1;position:relative">
          <svg width="100%" height="100%" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="position:absolute;inset:0">
            <line x1="0" y1="${(h * 0.33).toFixed(1)}" x2="${w}" y2="${(h * 0.33).toFixed(1)}" stroke="var(--c-line)" stroke-width="1" vector-effect="non-scaling-stroke" />
            <line x1="0" y1="${(h * 0.66).toFixed(1)}" x2="${w}" y2="${(h * 0.66).toFixed(1)}" stroke="var(--c-line)" stroke-width="1" vector-effect="non-scaling-stroke" />
          </svg>
          ${areaChart({ series: data.temps, min: data.tMin, max: data.tMax, w, h, color: WX.col("red"), fill: "var(--wx-red-t)" })}
        </div>
      </div>
      <div style="padding:4px 0 0 40px">${axisRow(data, { font: "var(--wx-mono)", size: 9.5 })}</div>
      <div style="display:flex;align-items:center;gap:12px;margin-top:8px">
        <span style="font-family:var(--wx-mono);font-size:10.5px;font-weight:700;color:var(--wx-ink-60);letter-spacing:.06em;width:40px">RAIN %</span>
        <div style="flex:1">${rainStrip({ rain: data.rain, color: WX.col("red"), height: 20 })}</div>
      </div>
    </div>
  `;
}

// ===========================================================
// Variant dispatch
// ===========================================================
const VARIANTS = {
  legacy: null,   // handled separately because it needs async Chart.js
  r1: renderR1,
  g2: renderG2,
  s3: renderS3,
  d4: renderD4,
};

function renderError(msg) {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/plugins/weather_hourly/client.css">
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
  const points = Array.isArray(data.points) ? data.points : [];
  const size = ctx.cell.size;
  const variant = ctx.cell.options.variant || "legacy";

  if (variant !== "legacy" && VARIANTS[variant]) {
    if (!Array.isArray(data.temps) || data.temps.length === 0) {
      shadow.innerHTML = renderError("no hourly data");
      return;
    }
    shadow.innerHTML = VARIANTS[variant](data);
    return;
  }

  // Legacy path — same Chart.js render we shipped pre-handoff.
  if (!points.length) {
    shadow.innerHTML = renderError("no hourly data");
    return;
  }
  shadow.innerHTML = legacyHtml(data, size);
  await paintLegacyChart(shadow, ctx, data, size);
}
