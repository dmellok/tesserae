// ha_energy, Spectra status archetype with a Chart.js Sankey as
// the centrepiece. Source rails on the left (Solar / Battery
// discharge / Grid import) carry proportional flow ribbons across
// to sink rails on the right (House / Battery charge / Grid export).
// The chartjs-chart-sankey plugin handles layout + colouring; we
// just feed it {from, to, flow} triples plus a colour table keyed
// by rail name so each band picks up its source's accent.
//
// Title bar tracks the time-of-day phase glyph; chips below carry
// battery SoC + today's solar kWh; a comparison sparkline at the
// bottom shows today's series in the flow accent with yesterday's
// as a thin dashed ghost.

import { sankey, tokens } from "../../static/spectra-chart.js";

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtW(v) {
  if (v == null) return "-";
  const n = Number(v);
  if (Number.isNaN(n)) return "-";
  if (Math.abs(n) >= 1000) return `${(n / 1000).toFixed(1)} kW`;
  return `${Math.round(n)} W`;
}

// SVG dual-line sparkline below the Sankey. Yesterday's series
// renders as a thin dashed ghost; today's as a thick filled line in
// the flow accent. "Now" pip rides today's line at the current hour.
function comparisonSparklineSvg({ today, yesterday, nowHour, accent }) {
  const w = 320;
  const h = 56;
  const padX = 6;
  const padY = 6;
  const innerW = w - padX * 2;
  const innerH = h - padY * 2;

  if (!Array.isArray(today) || today.length < 2) return "";

  const allPoints = [...today, ...(Array.isArray(yesterday) ? yesterday : [])];
  const min = Math.min(...allPoints);
  const max = Math.max(...allPoints);
  const range = max - min < 1 ? 1 : max - min;

  function pathFor(series) {
    const step = innerW / Math.max(1, series.length - 1);
    return series.map((v, i) => {
      const x = padX + i * step;
      const y = padY + innerH - ((v - min) / range) * innerH;
      return `${i === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    }).join(" ");
  }

  const todayPath = pathFor(today);
  const yesterdayPath = yesterday && yesterday.length >= 2 ? pathFor(yesterday) : "";
  const todayFillPath = `${todayPath} L ${padX + innerW} ${padY + innerH} L ${padX} ${padY + innerH} Z`;

  let nowPip = "";
  if (Number.isFinite(nowHour) && today.length >= 24) {
    const slot = Math.max(0, Math.min(today.length - 1, Math.round((nowHour / 24) * (today.length - 1))));
    const step = innerW / Math.max(1, today.length - 1);
    const x = padX + slot * step;
    const y = padY + innerH - ((today[slot] - min) / range) * innerH;
    nowPip = `
      <circle cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="3.5"
              fill="${accent}" stroke="var(--surface)" stroke-width="1.5"/>`;
  }

  return `
    <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"
         width="100%" height="100%" aria-hidden="true">
      ${yesterdayPath ? `
        <path d="${yesterdayPath}" fill="none"
              stroke="var(--text-muted)" stroke-width="1.5"
              stroke-dasharray="3 3" opacity="0.55"/>` : ""}
      <path d="${todayFillPath}" fill="${accent}" opacity="0.16"/>
      <path d="${todayPath}" fill="none" stroke="${accent}"
            stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>
      ${nowPip}
    </svg>`;
}

// Phase-of-day glyph for the title bar, same vocabulary as the
// clock_world widget so the family stays consistent.
function sunGlyph(hour) {
  if (!Number.isFinite(hour)) return { icon: "ph-sun", color: "var(--accent-2)" };
  if (hour < 5) return { icon: "ph-moon", color: "var(--accent-5)" };
  if (hour < 7) return { icon: "ph-sun-horizon", color: "var(--accent-2)" };
  if (hour < 17) return { icon: "ph-sun", color: "var(--accent-2)" };
  if (hour < 20) return { icon: "ph-sun-horizon", color: "var(--accent-1)" };
  return { icon: "ph-moon-stars", color: "var(--accent-5)" };
}

// Decompose live power readings into Sankey flows. Sign conventions:
//   solar > 0          , production
//   grid  > 0 import,  < 0 export
//   battery > 0 charge, < 0 discharge
//
// Returns:
//   flows  : list of {from, to, flow} for the Sankey
//   labels : list of source/sink IDs that actually have a band, used
//            for showing per-rail watt totals beside the chart.
function decomposeFlows({ solar, grid, battery, house }) {
  const flows = [];

  // Energy INTO the house. Drawn first; sources contribute up to
  // the house number (rest spills to export/charge below).
  const houseDraw = Math.max(0, house);
  let remaining = houseDraw;
  const solarToHouse = Math.min(remaining, Math.max(0, solar));
  remaining -= solarToHouse;
  const batteryToHouse = battery < 0 ? Math.min(remaining, -battery) : 0;
  remaining -= batteryToHouse;
  const gridToHouse = grid > 0 ? Math.min(remaining, grid) : 0;
  remaining -= gridToHouse;
  if (solarToHouse > 0)
    flows.push({ from: "Solar", to: "House", flow: solarToHouse });
  if (batteryToHouse > 0)
    flows.push({ from: "Battery", to: "House", flow: batteryToHouse });
  if (gridToHouse > 0)
    flows.push({ from: "Grid", to: "House", flow: gridToHouse });

  // Surplus solar → battery charge + grid export.
  const solarSurplus = Math.max(0, solar - solarToHouse);
  let surplusLeft = solarSurplus;
  const batteryCharge = battery > 0 ? Math.min(surplusLeft, battery) : 0;
  surplusLeft -= batteryCharge;
  const gridExport = grid < 0 ? Math.min(surplusLeft, -grid) : 0;
  if (batteryCharge > 0)
    flows.push({ from: "Solar", to: "Charge", flow: batteryCharge });
  if (gridExport > 0)
    flows.push({ from: "Solar", to: "Export", flow: gridExport });

  return flows;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_energy">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Energy</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const place = data.place || data.label || "Energy";
  const flow = data.flow || "mixed";
  const sunPhase = sunGlyph(data.hour);

  const values = {
    solar: Number.isFinite(data.solar_w) ? data.solar_w : 0,
    grid: Number.isFinite(data.grid_w) ? data.grid_w : 0,
    battery: Number.isFinite(data.battery_w) ? data.battery_w : 0,
    house: Number.isFinite(data.house_w) ? data.house_w : 0,
  };
  const soc = data.battery_soc;

  const flows = decomposeFlows(values);
  const accent = flow === "solar" ? "var(--accent-2)"
              : flow === "grid" ? "var(--accent-5)"
              : flow === "battery" ? "var(--accent-3)"
              : "var(--accent-4)";
  const compSparkline = comparisonSparklineSvg({
    today: data.sparkline_today || data.sparkline || [],
    yesterday: data.sparkline_yesterday || [],
    nowHour: data.hour,
    accent,
  });

  // Per-rail labels, what watts are flowing through each named rail.
  // Used for the chip strip beside the chart.
  const railTotals = (() => {
    const sum = {};
    for (const f of flows) {
      sum[f.from] = (sum[f.from] || 0) + f.flow;
      sum[f.to] = (sum[f.to] || 0) + f.flow;
    }
    return sum;
  })();

  const layout = `
    .energy-chips {
      display: flex;
      gap: var(--space-2);
      flex-wrap: wrap;
      align-items: center;
    }
    .energy-soc, .energy-today-chip {
      display: inline-flex;
      align-items: center;
      gap: var(--space-1);
      padding: 1px var(--space-2);
      border-radius: 999px;
      font-weight: var(--fw-bold);
      font-variant-numeric: tabular-nums;
      font-size: var(--fs-caption);
    }
    .energy-soc {
      background: color-mix(in oklab, var(--accent-3) 12%, var(--surface));
      color: var(--accent-3);
    }
    .energy-today-chip {
      background: color-mix(in oklab, var(--accent-2) 12%, var(--surface));
      color: var(--accent-2);
    }
    /* Sankey container, taller still so the 120px nodePadding has
       room to push the three sink rails apart while still leaving
       the bands a sensible thickness. clamp(11em, 38cqh, 20em) gives
       a tall channel that scales with cell size. */
    .energy-sankey {
      position: relative;
      flex: 0 0 auto;
      width: 100%;
      height: clamp(11em, 38cqh, 20em);
    }
    .energy-sankey canvas {
      width: 100% !important;
      height: 100% !important;
    }
    .energy-rail-legend {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
      gap: var(--space-2);
      font-size: var(--fs-caption);
    }
    .energy-rail-key {
      display: flex;
      flex-direction: column;
      gap: 1px;
      padding: var(--space-1) var(--space-2);
      border-radius: var(--radius-1);
      background: color-mix(in oklab, var(--text-primary) 3%, transparent);
    }
    .energy-rail-key-name {
      font-weight: var(--fw-black);
      letter-spacing: var(--ls-label);
      text-transform: uppercase;
      display: inline-flex;
      align-items: center;
      gap: 4px;
    }
    .energy-rail-key-value {
      font-weight: var(--fw-bold);
      font-variant-numeric: tabular-nums;
      color: var(--text-secondary);
    }
    .energy-spark-wrap {
      flex: 0 0 auto;
      height: 5em;
      width: 100%;
      display: flex;
    }
    .energy-spark-legend {
      display: flex;
      gap: var(--space-3);
      font-size: var(--fs-caption);
      color: var(--text-muted);
      font-weight: var(--fw-bold);
      letter-spacing: var(--ls-label);
      text-transform: uppercase;
    }
    .energy-spark-key {
      display: inline-flex;
      align-items: center;
      gap: 4px;
    }
    .energy-spark-key .key-line {
      display: inline-block;
      width: 14px;
      height: 3px;
      border-radius: 2px;
    }
    .energy-spark-key.is-ghost .key-line {
      background: transparent;
      border-radius: 0;
      width: 12px;
      height: 0;
      border-top: 2px dashed var(--text-muted);
      opacity: 0.7;
    }
    @container (max-width: 320px) {
      .energy-spark-legend, .energy-rail-legend { display: none; }
      .energy-sankey { height: 7em; }
    }
  `;

  const chips = [];
  if (soc != null) {
    chips.push(`<span class="energy-soc"><i class="ph-bold ph-battery-charging"></i>${Math.round(Number(soc))}%</span>`);
  }
  if (data.solar_today_kwh != null) {
    chips.push(`<span class="energy-today-chip"><i class="ph-bold ph-sun"></i>${Number(data.solar_today_kwh).toFixed(1)} kWh today</span>`);
  }

  // Build the per-rail legend below the Sankey so users can read the
  // exact watts even when a small band is hard to compare visually.
  const RAIL_META = {
    Solar:   { icon: "ph-sun",                color: "var(--accent-2)" },
    Battery: { icon: "ph-battery-charging",   color: "var(--accent-3)" },
    Grid:    { icon: "ph-lightning",          color: "var(--accent-5)" },
    House:   { icon: "ph-house",              color: "var(--text-primary)" },
    Charge:  { icon: "ph-battery-plus",       color: "var(--accent-3)" },
    Export:  { icon: "ph-arrow-up-right",     color: "var(--accent-5)" },
  };
  const railOrder = ["Solar", "Battery", "Grid", "House", "Charge", "Export"];
  const railLegend = railOrder
    .filter((k) => railTotals[k] > 0)
    .map((k) => {
      const meta = RAIL_META[k];
      return `
        <div class="energy-rail-key" style="border-left:3px solid ${meta.color}">
          <span class="energy-rail-key-name" style="color:${meta.color}"><i class="ph-bold ${meta.icon}"></i>${k}</span>
          <span class="energy-rail-key-value">${escapeHtml(fmtW(railTotals[k]))}</span>
        </div>`;
    }).join("");

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="ha_energy">
      <div class="w-title">
        <i class="ph-bold ${sunPhase.icon}" style="color:${sunPhase.color}"></i>
        <h3>${escapeHtml(place)}</h3>
        <span class="w-title-meta">${escapeHtml(data.time || "")}</span>
      </div>
      <div class="w-body" style="gap:var(--space-2)">
        ${chips.length ? `<div class="energy-chips">${chips.join("")}</div>` : ""}
        <div class="energy-sankey"><canvas></canvas></div>
        ${railLegend ? `<div class="energy-rail-legend">${railLegend}</div>` : ""}
        ${compSparkline ? `
          <div class="energy-spark-legend">
            <span class="energy-spark-key"><span class="key-line" style="background:${accent}"></span>Today</span>
            <span class="energy-spark-key is-ghost"><span class="key-line"></span>Yesterday</span>
          </div>
          <div class="energy-spark-wrap">${compSparkline}</div>` : ""}
      </div>
    </div>`;

  const sankeyCanvas = shadow.querySelector(".energy-sankey canvas");
  const t = tokens(shadow.host);

  // Map rail names → resolved hex/rgb colours for the chart. Chart.js
  // can't read CSS vars from the canvas, so we resolve via the token
  // probe.
  const colors = {
    Solar: t.accent2,
    Battery: t.accent3,
    Grid: t.accent5,
    House: t.textSecondary,
    Charge: t.accent3,
    Export: t.accent5,
  };

  if (flows.length > 0) {
    // Generous nodePadding pushes the sibling sink rails (House /
    // Charge / Export) apart so the gaps between ribbons become the
    // visual story. The library carves `canvas_height - nodePadding
    // * (nodes - 1)` out for the bands themselves; with 120px pad
    // and a ~280px canvas the three right-side ribbons share ~40px
    // of band height between them, sitting in airy negative space.
    sankey(sankeyCanvas, {
      tokens: t,
      flows,
      colors,
      colorMode: "gradient",
      nodePadding: 120,
      labelSize: 12,
    });
  } else {
    // No active flows, paint a muted message in the chart area
    // so the cell doesn't look broken.
    const ctx2d = sankeyCanvas.getContext("2d");
    sankeyCanvas.width = sankeyCanvas.offsetWidth;
    sankeyCanvas.height = sankeyCanvas.offsetHeight;
    ctx2d.fillStyle = t.textMuted;
    ctx2d.font = `700 14px ${t.fontFamily}`;
    ctx2d.textAlign = "center";
    ctx2d.textBaseline = "middle";
    ctx2d.fillText("NO FLOW", sankeyCanvas.width / 2, sankeyCanvas.height / 2);
  }
}
