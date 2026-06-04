// f1_next — Spectra status archetype with an inline circuit
// outline. Hero shows a countdown to the race lights-out; sub line
// names the circuit + locality; the status-grid stacks the session
// times for the weekend.

import { getCircuit, trackSvg } from "../f1_core/static/circuits.js";

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const SESSION_LABEL = {
  fp1: "FP1", fp2: "FP2", fp3: "FP3",
  sprint: "Sprint", qualifying: "Quali",
};
// Phosphor icon per session type so the status grid reads like an
// F1 timing board rather than five identical text cells. Practice
// = stopwatch (timing laps), sprint = lightning (short flat-out
// race), qualifying = target (chasing pole), race = checkered flag
// (used elsewhere, not in this grid).
const SESSION_ICON = {
  fp1: "ph-stopwatch", fp2: "ph-stopwatch", fp3: "ph-stopwatch",
  sprint: "ph-lightning", qualifying: "ph-target",
};

function combineDt(date, time) {
  if (!date) return null;
  const iso = time ? `${date}T${time.endsWith("Z") ? time : time + "Z"}` : `${date}T00:00:00Z`;
  const t = new Date(iso).getTime();
  return Number.isFinite(t) ? t : null;
}

function fmtCountdown(ms) {
  if (!Number.isFinite(ms) || ms <= 0) return "LIVE";
  const days = Math.floor(ms / 86400000);
  const hours = Math.floor((ms % 86400000) / 3600000);
  const mins = Math.floor((ms % 3600000) / 60000);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${mins}m`;
  return `${mins}m`;
}

function fmtSessionTime(date, time) {
  if (!date) return "";
  if (!time) return date.slice(5);
  return `${date.slice(5)} ${time.slice(0, 5)}`;
}

export default async function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const opts = ctx?.cell?.options || {};
  const showSeconds = opts.show_seconds === true;
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="f1_next">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Next Race</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const sessions = data.sessions || {};
  const raceDt = combineDt(data.date, data.time);
  const now = Date.now();
  const countdown = raceDt ? fmtCountdown(raceDt - now) : "—";

  // Optionally include MM:SS precision when show_seconds is on for the
  // last hour. Falls back to the regular countdown otherwise.
  let countdownLabel = countdown;
  if (showSeconds && raceDt && raceDt - now < 3600000 && raceDt - now > 0) {
    const remain = Math.floor((raceDt - now) / 1000);
    countdownLabel = `${Math.floor(remain / 60)}:${String(remain % 60).padStart(2, "0")}`;
  }

  const subBits = [data.circuitName, data.locality].filter(Boolean).join(" · ");

  let track = null;
  try { track = await getCircuit(data.circuitId); } catch { track = null; }

  const sessionCells = ["fp1", "fp2", "fp3", "sprint", "qualifying"].map((key) => {
    const s = sessions[key];
    if (!s) return "";
    const icon = SESSION_ICON[key] || "ph-clock";
    return `
      <div class="status-cell">
        <span class="u-label" style="display:inline-flex;align-items:center;gap:0.3em">
          <i class="ph-bold ${icon}" style="color:var(--text-muted);font-size:1.1em;line-height:1"></i>
          ${SESSION_LABEL[key]}
        </span>
        <span class="v" style="font-size:var(--fs-body);font-weight:var(--fw-bold)">${escapeHtml(fmtSessionTime(s.date, s.time))}</span>
      </div>`;
  }).filter(Boolean).join("");

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="f1_next">
      <div class="w-title">
        <i class="ph-bold ph-flag" style="color:var(--accent-1)"></i>
        <h3>${escapeHtml(data.raceName || "Next Race")}</h3>
        ${data.round ? `<span class="w-title-meta">R${data.round}</span>` : ""}
      </div>
      <div class="w-body f1-body">
        <div class="f1-data">
          <div class="status-hero">
            <i class="ph-bold ph-clock-countdown" style="color:var(--accent-1)"></i>
            <div class="lockup">
              <span class="status-state">${escapeHtml(countdownLabel)}</span>
              <span class="status-sub">${data.locality ? `<i class="ph-bold ph-map-pin" style="font-size:.85em;color:var(--text-muted);margin-right:.25em;vertical-align:-.05em"></i>` : ""}${escapeHtml(subBits)}</span>
            </div>
          </div>
          ${data.country ? `<span class="pill" style="background:var(--accent-1)">${escapeHtml(data.country)}</span>` : ""}
          ${sessionCells ? `<div class="status-grid">${sessionCells}</div>` : ""}
        </div>
        ${track ? `<div class="f1-track" style="color:var(--accent-1)">${trackSvg(track, { stroke: "var(--accent-1)" })}</div>` : ""}
      </div>
    </div>`;
}
