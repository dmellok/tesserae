// f1_last_race — Bauhaus podium card.
//
// Layout (top to bottom):
//   1. Inverted header bar (mark + LAST RACE + round badge)
//   2. Three podium rows (P1 accent, P2 surface2, P3 accent3)
//   3. Circuit silhouette band (accent2 backdrop)
//
// Each podium row: big position number, driver family name, team,
// time/gap, optional "FL" fastest-lap badge.

import { getCircuit } from "../f1_core/static/circuits.js";

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function renderError(msg) {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/plugins/f1_last_race/client.css">
    <div class="root error">
      <i class="ph ph-warning-circle" aria-hidden="true"></i>
      <span>${escapeHtml(msg)}</span>
    </div>
  `;
}

function circuitSvg(circuit) {
  if (!circuit) return "";
  return `
    <svg class="fl-circuit-svg" viewBox="${circuit.viewBox}" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
      <path d="${circuit.d}" fill="none" stroke="currentColor" stroke-width="14" stroke-linejoin="round" stroke-linecap="round" />
    </svg>
  `;
}

function podiumRow(r, posClass) {
  if (!r) return "";
  const fl = r.fastest ? `<span class="fl-fl" title="Fastest lap">FL</span>` : "";
  return `
    <div class="fl-row ${posClass}">
      <span class="fl-pos">${escapeHtml(r.position || "")}</span>
      <span class="fl-name">
        <span class="fl-family">${escapeHtml(r.family || "")}</span>
        <span class="fl-team">${escapeHtml(r.constructor || "")}</span>
      </span>
      <span class="fl-time">${escapeHtml(r.time || r.status || "—")}</span>
      ${fl}
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
  const podium = Array.isArray(data.podium) ? data.podium : [];
  if (!podium.length) {
    shadow.innerHTML = renderError("no results yet");
    return;
  }

  // xs: only the winner; sm: top 3, no circuit; md/lg: top 3 + circuit.
  const visiblePodium = size === "xs" ? podium.slice(0, 1) : podium.slice(0, 3);
  const showCircuit = size === "md" || size === "lg";
  const circuit = showCircuit && data.circuitId ? await getCircuit(data.circuitId) : null;

  const posClasses = ["fl-row--p1", "fl-row--p2", "fl-row--p3"];
  const podiumHtml = visiblePodium
    .map((r, i) => podiumRow(r, posClasses[i] || ""))
    .join("");

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/f1_last_race/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="wb-title">${escapeHtml(data.raceName || "Last Race")}</span>
        <span class="wb-bar-meta">${data.round ? `R${escapeHtml(data.round)} · ${escapeHtml(data.season || "")}` : ""}</span>
      </header>
      <section class="fl-podium">
        ${podiumHtml}
      </section>
      ${showCircuit ? `
      <section class="fl-circuit" aria-label="${escapeHtml(data.circuitName || "")}">
        ${circuit ? circuitSvg(circuit) : `<span class="fl-circuit-fallback">${escapeHtml(data.locality || data.circuitName || "")}</span>`}
      </section>` : ""}
    </div>
  `;
}
