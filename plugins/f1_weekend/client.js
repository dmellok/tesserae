// f1_weekend — Bauhaus race-weekend session schedule.
//
// Layout:
//   1. Inverted header bar (mark + race name + round badge)
//   2. Session table (FP1/FP2/FP3 or sprint variants + Qual + Race)
//      Race row gets the accent block; others alternate surface tones.
//   3. Circuit silhouette band (accent2 backdrop) on md/lg

import { getCircuit } from "../f1_core/static/circuits.js";

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function renderError(msg) {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/plugins/f1_weekend/client.css">
    <div class="root error">
      <i class="ph ph-warning-circle" aria-hidden="true"></i>
      <span>${escapeHtml(msg)}</span>
    </div>
  `;
}

function sessionMoment(s) {
  if (!s || !s.date) return null;
  const iso = s.time ? `${s.date}T${s.time}` : `${s.date}T12:00:00Z`;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

function fmtDay(d) {
  if (!d) return "—";
  return `${DAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]}`;
}
function fmtTime(d) {
  if (!d) return "—";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

function circuitSvg(circuit) {
  if (!circuit) return "";
  return `
    <svg class="fw-circuit-svg" viewBox="${circuit.viewBox}" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
      <path d="${circuit.d}" fill="none" stroke="currentColor" stroke-width="14" stroke-linejoin="round" stroke-linecap="round" />
    </svg>
  `;
}

function sessionRow(s, isRace) {
  const d = sessionMoment(s);
  return `
    <div class="fw-row ${isRace ? "fw-row--race" : ""}">
      <span class="fw-row-label">${escapeHtml(s.label)}</span>
      <span class="fw-row-day">${escapeHtml(fmtDay(d))}</span>
      <span class="fw-row-time">${escapeHtml(fmtTime(d))}</span>
    </div>
  `;
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = renderError(data.error);
    return;
  }
  const sessions = Array.isArray(data.sessions) ? data.sessions : [];
  if (!sessions.length) {
    shadow.innerHTML = renderError("no sessions scheduled");
    return;
  }

  const size = ctx.cell.size;
  const showCircuit = size === "md" || size === "lg";
  const circuit = showCircuit && data.circuitId ? await getCircuit(data.circuitId) : null;

  const rowsHtml = sessions.map((s) => sessionRow(s, s.label === "RACE")).join("");

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/f1_weekend/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="wb-title">${escapeHtml(data.raceName || "Race Weekend")}</span>
        <span class="wb-bar-meta">${data.round ? `R${escapeHtml(data.round)} · ${escapeHtml(data.season || "")}` : ""}</span>
      </header>
      <section class="fw-rows">${rowsHtml}</section>
      ${showCircuit ? `
      <section class="fw-circuit" aria-label="${escapeHtml(data.circuitName || "")}">
        ${circuit ? circuitSvg(circuit) : `<span class="fw-circuit-fallback">${escapeHtml(data.locality || data.circuitName || "")}</span>`}
      </section>` : ""}
    </div>
  `;
}
