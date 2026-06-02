// f1_next — Bauhaus next-race card.
//
// Layout (top to bottom):
//   1. Inverted header bar (mark + NEXT RACE + round badge)
//   2. Hero split 50/50: circuit silhouette panel | text panel
//   3. Four-up countdown chip strip (days / hours / mins / secs)
//
// Tall cells stack the hero vertically (text on top, circuit below).

import { getCircuit } from "../f1_core/static/circuits.js";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function raceMoment(data) {
  if (!data.date) return null;
  const iso = data.time ? `${data.date}T${data.time}` : `${data.date}T12:00:00Z`;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

function fmtLocal(d) {
  if (!d) return "—";
  const day = DAYS[d.getDay()];
  const date = d.getDate();
  const month = MONTHS[d.getMonth()];
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  return `${day} ${date} ${month} · ${time}`;
}

// Race name comes back as "Australian Grand Prix" — split into a
// strong country line and a subdued "GRAND PRIX" line so the typography
// reads like a poster.
function splitRaceName(raceName) {
  const s = String(raceName || "");
  const gp = s.match(/(.+?)\s+Grand Prix$/i);
  if (gp) return { lead: gp[1], suffix: "Grand Prix" };
  return { lead: s, suffix: "" };
}

function diffParts(targetMs, nowMs) {
  let delta = Math.max(0, Math.floor((targetMs - nowMs) / 1000));
  const days = Math.floor(delta / 86400); delta -= days * 86400;
  const hours = Math.floor(delta / 3600); delta -= hours * 3600;
  const mins = Math.floor(delta / 60);   delta -= mins * 60;
  const secs = delta;
  return { days, hours, mins, secs, live: targetMs <= nowMs };
}

function pad(n) { return String(n).padStart(2, "0"); }

function renderError(msg) {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/plugins/f1_next/client.css">
    <div class="root error">
      <i class="ph ph-warning-circle" aria-hidden="true"></i>
      <span>${escapeHtml(msg)}</span>
    </div>
  `;
}

// SVG markup for the circuit silhouette — fills the panel, stroke uses
// theme.fg so it reads as a heavy Bauhaus line. Returns "" if the
// circuit isn't in the f1_core bundle (graceful fallback).
function circuitSvg(circuit) {
  if (!circuit) return "";
  return `
    <svg class="fn-circuit-svg" viewBox="${circuit.viewBox}" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
      <path d="${circuit.d}" fill="none" stroke="currentColor" stroke-width="14" stroke-linejoin="round" stroke-linecap="round" />
    </svg>
  `;
}

export default async function render(shadow, ctx) {
  // Clear any running countdown from a previous render — the framework
  // can call render() repeatedly on theme/option changes and we don't
  // want stacked intervals.
  if (shadow.__fnTimer) {
    clearInterval(shadow.__fnTimer);
    shadow.__fnTimer = null;
  }

  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = renderError(data.error);
    return;
  }

  const size = ctx.cell.size;
  const showSecs = ctx.cell.options.show_seconds !== false && size !== "xs";
  const target = raceMoment(data);
  const { lead, suffix } = splitRaceName(data.raceName);
  const circuit = data.circuitId ? await getCircuit(data.circuitId) : null;

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/f1_next/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="wb-title">Next Race</span>
        <span class="wb-bar-meta">${data.round ? `R${escapeHtml(data.round)} · ${escapeHtml(data.season || "")}` : ""}</span>
      </header>
      <section class="fn-hero">
        <div class="fn-circuit" aria-hidden="true">
          ${circuit ? circuitSvg(circuit) : `<i class="ph-bold ph-flag-checkered fn-circuit-fallback"></i>`}
        </div>
        <div class="fn-text">
          <div class="fn-race">
            <div class="fn-race-lead">${escapeHtml(lead)}</div>
            ${suffix ? `<div class="fn-race-suffix">${escapeHtml(suffix)}</div>` : ""}
          </div>
          <div class="fn-meta">
            <div class="fn-place">${escapeHtml([data.locality, data.country].filter(Boolean).join(" · "))}</div>
            <div class="fn-when">${fmtLocal(target)}</div>
          </div>
        </div>
      </section>
      <section class="fn-chips" aria-label="Countdown">
        <div class="fn-chip fn-chip--accent"><span class="fn-chip-value" data-c="days">—</span><span class="fn-chip-label">Days</span></div>
        <div class="fn-chip fn-chip--surface"><span class="fn-chip-value" data-c="hours">—</span><span class="fn-chip-label">Hrs</span></div>
        <div class="fn-chip fn-chip--accent2"><span class="fn-chip-value" data-c="mins">—</span><span class="fn-chip-label">Min</span></div>
        ${showSecs ? `<div class="fn-chip fn-chip--accent3"><span class="fn-chip-value" data-c="secs">—</span><span class="fn-chip-label">Sec</span></div>` : ""}
      </section>
    </div>
  `;

  if (!target) return;

  const cells = {
    days:  shadow.querySelector('[data-c="days"]'),
    hours: shadow.querySelector('[data-c="hours"]'),
    mins:  shadow.querySelector('[data-c="mins"]'),
    secs:  shadow.querySelector('[data-c="secs"]'),
  };
  const root = shadow.querySelector(".root");

  const tick = () => {
    const p = diffParts(target.getTime(), Date.now());
    if (cells.days)  cells.days.textContent  = String(p.days);
    if (cells.hours) cells.hours.textContent = pad(p.hours);
    if (cells.mins)  cells.mins.textContent  = pad(p.mins);
    if (cells.secs)  cells.secs.textContent  = pad(p.secs);
    if (root) root.classList.toggle("is-live", p.live);
  };
  tick();
  // Tick every second when showing seconds, every minute otherwise.
  shadow.__fnTimer = setInterval(tick, showSecs ? 1000 : 60000);
}
