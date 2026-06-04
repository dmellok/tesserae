// f1_weekend — Spectra list archetype with an inline circuit
// outline above the session rows. Each session (FP1 / FP2 / FP3 /
// Sprint / Qualifying / Race) is a zebra row with the label + day,
// time as right-aligned meta. The race row picks up accent-1 so the
// headline session always reads first.

import { getCircuit, trackSvg } from "../f1_core/static/circuits.js";

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtDayLabel(date) {
  if (typeof date !== "string") return "";
  try {
    const dt = new Date(date + "T00:00:00Z");
    return dt.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
  } catch {
    return date;
  }
}

function fmtTime(time) {
  if (typeof time !== "string" || !time) return "";
  return time.slice(0, 5);
}

export default async function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="f1_weekend">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Weekend</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const sessions = Array.isArray(data.sessions) ? data.sessions : [];
  const subBits = [data.circuitName, data.country].filter(Boolean).join(" · ");

  let track = null;
  try { track = await getCircuit(data.circuitId); } catch { track = null; }

  const rows = sessions.map((s, i) => {
    const isRace = (s.label || "").toLowerCase() === "race";
    const accent = isRace ? "var(--accent-1)" : "var(--text-secondary)";
    const ph = isRace ? "ph-flag-checkered" : "ph-clock";
    return `
      <div class="list-row ${i % 2 ? "is-zebra" : ""}">
        <div class="list-lead">
          <i class="ph-bold ${ph}" style="color:${accent}"></i>
          <span class="list-title" style="${isRace ? `color:${accent};font-weight:var(--fw-black)` : ""}">${escapeHtml(s.label)}<small class="u-muted" style="font-weight:var(--fw-semi);font-size:.7em;margin-left:.4em">${escapeHtml(fmtDayLabel(s.date))}</small></span>
        </div>
        <span class="list-meta" style="color:${accent}">${escapeHtml(fmtTime(s.time))}</span>
      </div>`;
  }).join("");

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="f1_weekend">
      <div class="w-title">
        <i class="ph-bold ph-flag-checkered" style="color:var(--accent-1)"></i>
        <h3>${escapeHtml(data.raceName || "Weekend")}</h3>
        ${data.round ? `<span class="w-title-meta">R${data.round}</span>` : ""}
      </div>
      <div class="w-body f1-body">
        <div class="f1-data list-body" style="gap:var(--space-2)">
          ${subBits ? `<p class="u-muted" style="padding:0 var(--space-3);font-weight:var(--fw-bold)">${escapeHtml(subBits)}</p>` : ""}
          ${rows}
        </div>
        ${track ? `<div class="f1-track" style="color:var(--accent-1)">${trackSvg(track, { stroke: "var(--accent-1)" })}</div>` : ""}
      </div>
    </div>`;
}
