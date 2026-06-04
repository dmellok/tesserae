// f1_last_race — Spectra status archetype. Inline circuit outline
// across the top (loaded from f1_core's bundled circuits.json),
// then race name as the hero state, circuit + locality as the sub
// line, top-three finishers rendered as a three-cell podium row
// with gold / silver / bronze accents.

import { getCircuit } from "../f1_core/static/circuits.js";

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const PODIUM_ACCENT = ["var(--accent-2)", "var(--text-secondary)", "var(--accent-1)"];

// Inline circuit outline. Returns SVG markup for the track path or
// an empty string if the circuit isn't in the bundled JSON (new
// venues land before the bundle is rebuilt).
function trackSvg(circuit) {
  if (!circuit || !circuit.d) return "";
  return `
    <svg viewBox="${circuit.viewBox}" preserveAspectRatio="xMidYMid meet"
         style="width:100%;height:100%;display:block">
      <path d="${circuit.d}" fill="none" stroke="var(--text-primary)"
            stroke-width="6" stroke-linejoin="round" stroke-linecap="round" />
    </svg>`;
}

export default async function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="f1_last_race">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Last Race</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const podium = Array.isArray(data.podium) ? data.podium.slice(0, 3) : [];
  const country = data.country || "";
  const circuit = data.circuitName || "";
  const locality = data.locality || "";
  const subBits = [circuit, locality].filter(Boolean).join(" · ");

  // Pull the circuit outline (async — bundle is fetched + cached once
  // per browser session by f1_core/static/circuits.js).
  let track = null;
  try { track = await getCircuit(data.circuitId); } catch { track = null; }

  const cells = podium.map((p, i) => {
    const accent = PODIUM_ACCENT[i] || "var(--text-muted)";
    const code = p.code || `${(p.given || "")[0] || ""}${(p.family || "")[0] || ""}`;
    return `
      <div class="status-cell">
        <span class="u-label" style="color:${accent}">P${p.position || i + 1}</span>
        <span class="v" style="color:${accent}">${escapeHtml(code)}<small style="font-size:.55em;color:var(--text-muted);font-weight:var(--fw-bold);margin-left:.3em">${escapeHtml(p.time || "")}</small></span>
      </div>`;
  }).join("");

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="f1_last_race">
      <div class="w-title">
        <i class="ph-bold ph-flag-checkered" style="color:var(--accent-1)"></i>
        <h3>${escapeHtml(data.raceName || "Last Race")}</h3>
        ${data.round ? `<span class="w-title-meta">R${data.round}</span>` : ""}
      </div>
      <div class="w-body status-body">
        ${track ? `<div style="flex:0 0 25%;min-height:2.5em">${trackSvg(track)}</div>` : ""}
        <div class="status-hero">
          <i class="ph-bold ph-trophy" style="color:var(--accent-2)"></i>
          <div class="lockup">
            <span class="status-state">${escapeHtml(podium[0]?.code || "—")}</span>
            <span class="status-sub">${escapeHtml(subBits)}</span>
          </div>
        </div>
        ${country ? `<span class="pill" style="background:var(--accent-1)">${escapeHtml(country)}</span>` : ""}
        ${cells ? `<div class="status-grid" style="grid-template-columns:1fr 1fr 1fr">${cells}</div>` : ""}
      </div>
    </div>`;
}
