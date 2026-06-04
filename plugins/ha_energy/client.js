// ha_energy — Spectra status archetype with a Chart.js sparkline of
// the last 24h. Hero = current power flow's signature value (solar
// generation if generating, else house load). Pill names the flow
// (solar / grid / battery / mixed). Status grid breaks out the four
// wattage channels + battery SOC if present; the sparkline at the
// bottom shows the trend in the flow accent.

import { sparkline, tokens } from "../../static/spectra-chart.js";

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const FLOW_ACCENT = {
  solar: "var(--accent-2)",   // ochre — sun energy
  grid: "var(--accent-5)",    // slate blue — utility
  battery: "var(--accent-3)", // moss — stored
  mixed: "var(--accent-4)",   // teal — neutral fallback
};

const FLOW_ICON = {
  solar: "ph-sun",
  grid: "ph-lightning",
  battery: "ph-battery-charging",
  mixed: "ph-shuffle",
};

function fmtW(v) {
  if (v == null) return "—";
  const n = Number(v);
  if (Number.isNaN(n)) return "—";
  if (Math.abs(n) >= 1000) return `${(n / 1000).toFixed(1)} kW`;
  return `${Math.round(n)} W`;
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
  const accent = FLOW_ACCENT[flow] || FLOW_ACCENT.mixed;
  const flowIcon = FLOW_ICON[flow] || FLOW_ICON.mixed;

  const solar = data.solar_w;
  const grid = data.grid_w;
  const battery = data.battery_w;
  const house = data.house_w;
  const soc = data.battery_soc;

  // Hero value: prefer solar generation when there's any; otherwise
  // surface the house load so the widget always shows something
  // meaningful.
  const heroValue = (Number.isFinite(solar) && solar > 0) ? fmtW(solar) : fmtW(house);
  const heroLabel = (Number.isFinite(solar) && solar > 0) ? "Solar now" : "House load";

  const cells = [
    ["Solar", fmtW(solar), FLOW_ACCENT.solar],
    ["Grid", fmtW(grid), FLOW_ACCENT.grid],
    ["Battery", fmtW(battery), FLOW_ACCENT.battery],
    ["House", fmtW(house), "var(--text-secondary)"],
  ];
  if (soc != null) cells.push(["SOC", `${Math.round(Number(soc))}%`, FLOW_ACCENT.battery]);

  const grid_html = cells.map(([label, value, c]) => `
    <div class="status-cell">
      <span class="u-label">${escapeHtml(label)}</span>
      <span class="v" style="color:${c}">${escapeHtml(value)}</span>
    </div>`).join("");

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="ha_energy">
      <div class="w-title">
        <i class="ph-bold ph-lightning" style="color:${accent}"></i>
        <h3>${escapeHtml(place)}</h3>
        <span class="w-title-meta">${escapeHtml(data.time || "")}</span>
      </div>
      <div class="w-body status-body">
        <div class="status-hero">
          <i class="ph-bold ${flowIcon}" style="color:${accent}"></i>
          <div class="lockup">
            <span class="status-state">${escapeHtml(heroValue)}</span>
            <span class="status-sub">${escapeHtml(heroLabel)}</span>
          </div>
        </div>
        <span class="pill" style="background:${accent}">${escapeHtml(flow)}</span>
        <div class="status-grid">${grid_html}</div>
        ${Array.isArray(data.sparkline) && data.sparkline.length >= 2
          ? `<div style="flex:0 0 25%;min-height:1.5em;position:relative"><canvas></canvas></div>`
          : ""}
      </div>
    </div>`;

  if (Array.isArray(data.sparkline) && data.sparkline.length >= 2) {
    const canvas = shadow.querySelector("canvas");
    const t = tokens(shadow.host);
    const tokenName = (flow === "solar" ? "accent2"
                    : flow === "grid" ? "accent5"
                    : flow === "battery" ? "accent3"
                    : "accent4");
    sparkline(canvas, data.sparkline, t[tokenName]);
  }
}
