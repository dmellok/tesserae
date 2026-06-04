// octoprint_status — live 3D-print monitor for an OctoPrint instance.
//
// server.py normalises the OctoPrint REST API into:
//
//   data.label, data.time
//   data.state   {text, tone}              tone ∈ printing|paused|complete|error|offline|idle
//   data.job     {name, completion, elapsed, remaining, eta} | null
//   data.temps   {tool:{actual,target}, bed:{actual,target}}
//
// Four directions pickable per-cell via `variant`, mirroring the rest
// of the family (weather, HA, calendar):
//
//   r1  Refined    Charcoal header + filename + hero progress bar with
//                  elapsed/remaining/ETA and stacked hotend/bed rows.
//   g2  Geometric  De Stijl colour blocks — a big progress block, a red
//                  hotend block + blue bed block, Archivo Black numerals.
//   s3  Swiss      Hairline header, table of fields (completion / times /
//                  temps), light tabular numerals, tiny accent dots.
//   d4  Data       Progress foregrounded with a big % + bar; a temp
//                  sidebar with actual→target readouts and target ticks.

import { WX } from "../weather_core/static/wx-common.js";

const escapeHtml = (s) => WX.escapeHtml(s);
const DEFAULT_FONT = "var(--wx-grotesk)";

function nowTime() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

// State tone → presentation. Printer state is a genuine *status* signal,
// so the badge colours reach for the semantic --c-* status tokens
// directly (printing = info, paused = warn, complete = ok, error =
// danger) rather than the decorative wx ramp. `wx` is the nearest
// decorative chip name for the dark header's colour mark.
const STATE = {
  printing: { wx: "blue",   c: "var(--c-info)",      icon: "play",         label: "PRINTING" },
  paused:   { wx: "yellow", c: "var(--c-warn)",      icon: "pause",        label: "PAUSED" },
  complete: { wx: "green",  c: "var(--c-ok)",        icon: "check-circle", label: "COMPLETE" },
  error:    { wx: "red",    c: "var(--c-danger)",    icon: "warning",      label: "ERROR" },
  offline:  { wx: "muted",  c: "var(--c-text-mute)", icon: "plugs",        label: "OFFLINE" },
  idle:     { wx: "muted",  c: "var(--c-text-soft)", icon: "printer",      label: "READY" },
};
function stateOf(data) {
  const tone = (data.state && data.state.tone) || "idle";
  const meta = STATE[tone] || STATE.idle;
  const text = (data.state && data.state.text) || meta.label;
  return { tone, meta, text };
}

// Hotend conventionally reads hot/red, bed reads blue — an established
// convention of the data, so we paint them with the wx decorative chips
// (which flow through --c-accent / --c-data-2), not status tokens.
const TOOL_COLOR = "var(--wx-red)";
const BED_COLOR = "var(--wx-blue)";

function fmtPct(n) {
  if (n == null || Number.isNaN(Number(n))) return "—";
  return Math.round(Number(n)) + "%";
}
function fmtTemp(n) {
  if (n == null || Number.isNaN(Number(n))) return "—";
  return Math.round(Number(n)) + "°";
}
function fmtDur(s) {
  if (s == null || !isFinite(s) || s < 0) return "—";
  s = Math.round(s);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m > 0) return `${m}m`;
  return `${s}s`;
}
function tempStr(t) {
  if (!t || t.actual == null) return "—";
  const a = fmtTemp(t.actual);
  return t.target ? `${a} → ${fmtTemp(t.target)}` : a;
}

function styleBlock() {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus-wx.css">
  `;
}

// ===========================================================
// R1 — REFINED (charcoal header → accent hero panel → temp rows)
// ===========================================================
function renderR1(data, size) {
  const { meta, text } = stateOf(data);
  const job = data.job;
  const temps = data.temps || {};
  const headerRight = `${WX.icon(meta.icon, { size: 14, color: "var(--wb-bar-fg)" })}
    <span style="margin-left:6px">${escapeHtml((text || meta.label).toUpperCase())} · ${escapeHtml(data.time || nowTime())}</span>`;

  const tempRow = (label, t, color, icon) => `
    <div style="display:flex;align-items:center;gap:10px">
      ${WX.icon(icon, { size: 18, color })}
      <span style="flex:0 0 30%;font-family:var(--wx-grotesk);font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--wx-ink-60)">${label}</span>
      <div style="flex:1;min-width:24px">
        ${WX.barChart({ value: (t && t.actual) || 0, max: (t && t.target) || (t && t.actual) || 1, color, height: 7 })}
      </div>
      <span class="wx-tnum" style="font-family:var(--wx-black);font-size:14px;min-width:84px;text-align:right;color:var(--c-text)">${escapeHtml(tempStr(t))}</span>
    </div>
  `;

  const hero = job
    ? `
      <div class="op-r1-hero">
        <div class="op-r1-hero-text">
          <div style="display:flex;align-items:center;gap:8px;min-width:0">
            ${WX.icon("cube", { size: 16, color: "var(--c-accent)" })}
            <span style="font-family:var(--wx-grotesk);font-size:clamp(11px, 2.4cqw, 14px);font-weight:600;color:var(--c-text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0">${escapeHtml(job.name)}</span>
          </div>
          <div style="height:10px;background:color-mix(in oklab, var(--c-text) 12%, transparent);margin-top:8px">
            <div style="width:${Math.max(0, Math.min(100, Number(job.completion) || 0)).toFixed(1)}%;height:100%;background:var(--c-accent)"></div>
          </div>
          <div style="display:flex;justify-content:space-between;margin-top:6px;font-family:var(--wx-mono);font-size:10.5px;letter-spacing:.04em;color:var(--wx-ink-60)">
            <span>ELAPSED ${escapeHtml(fmtDur(job.elapsed))}</span>
            <span>LEFT ${escapeHtml(fmtDur(job.remaining))}</span>
            <span>ETA ${escapeHtml(job.eta || "—")}</span>
          </div>
        </div>
        <div class="op-r1-hero-pct">
          <span class="wx-tnum" style="font-family:var(--wx-black);font-size:clamp(28px, 8cqw, 56px);line-height:.85">${escapeHtml(fmtPct(job.completion))}</span>
        </div>
      </div>`
    : `
      <div class="op-r1-idle">
        <div class="op-r1-idle-icon">${WX.icon(meta.icon, { size: 48, color: "var(--wx-red-fg)" })}</div>
        <div class="op-r1-idle-text">
          <span style="font-family:var(--wx-black);font-size:clamp(14px, 3cqw, 20px);letter-spacing:.04em;color:var(--c-text)">${escapeHtml(text || meta.label)}</span>
          <span style="font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink-60)">NO ACTIVE JOB</span>
        </div>
      </div>`;

  return `
    ${styleBlock()}
    <style>
      .op-r1-hero { display:grid; grid-template-columns:1.4fr 1fr; min-height:0; border-top:3px solid var(--c-accent); }
      .op-r1-hero-text { background:var(--wx-tint); padding:clamp(10px, 2cqw, 16px) clamp(12px, 2.4cqw, 18px); display:flex; flex-direction:column; justify-content:center; min-width:0; }
      .op-r1-hero-pct { background:var(--c-accent); color:var(--wx-red-fg); display:flex; align-items:center; justify-content:center; padding:clamp(10px, 2cqw, 16px); }
      .op-r1-idle { display:grid; grid-template-columns:auto 1fr; align-items:center; gap:14px; padding:clamp(12px, 2.6cqw, 22px); border-top:3px solid var(--c-accent); background:var(--wx-tint); }
      .op-r1-idle-icon { background:var(--c-accent); color:var(--wx-red-fg); width:clamp(48px, 12cqw, 72px); height:clamp(48px, 12cqw, 72px); display:flex; align-items:center; justify-content:center; }
      .op-r1-idle-text { display:flex; flex-direction:column; gap:4px; }
      .op-r1-temps { margin-top:auto; display:flex; flex-direction:column; gap:9px; padding:14px 16px; border-top:3px solid var(--c-accent); background:var(--wx-paper); }

      @container (max-width: 360px) {
        .op-r1-hero { grid-template-columns:1fr; }
      }
    </style>
    <div class="wx-art size-${size}" style="font-family:${DEFAULT_FONT};display:flex;flex-direction:column;min-height:0">
      ${WX.darkHeader({ title: data.label || "OCTOPRINT", accent: "red", right: headerRight })}
      ${hero}
      <div class="op-r1-temps">
        ${tempRow("Hotend", temps.tool, TOOL_COLOR, "thermometer-hot")}
        ${tempRow("Bed", temps.bed, BED_COLOR, "thermometer-simple")}
      </div>
    </div>
  `;
}

// ===========================================================
// G2 — GEOMETRIC (De Stijl colour blocks, Archivo Black)
// ===========================================================
function renderG2(data, size) {
  const { meta, text } = stateOf(data);
  const job = data.job;
  const temps = data.temps || {};
  const tempBlock = (label, t, color) => `
    <div style="background:${color};color:var(--wx-paper);padding:10px 12px;display:flex;flex-direction:column;justify-content:space-between;min-width:0">
      <span style="font-family:var(--wx-mono);font-size:10px;font-weight:700;letter-spacing:.08em">${label}</span>
      <div>
        <span class="wx-tnum" style="font-family:var(--wx-black);font-size:28px;line-height:.85;display:block">${escapeHtml(fmtTemp(t && t.actual))}</span>
        <span style="font-family:var(--wx-mono);font-size:9.5px;opacity:.85">${(t && t.target) ? "/ " + fmtTemp(t.target) : "OFF"}</span>
      </div>
    </div>
  `;

  const progressBlock = `
    <div style="background:${meta.c};color:var(--wx-paper);padding:12px 14px;display:flex;flex-direction:column;justify-content:space-between;min-width:0">
      <div style="display:flex;align-items:center;gap:7px;font-family:var(--wx-mono);font-size:10.5px;font-weight:700;letter-spacing:.08em">
        ${WX.icon(meta.icon, { size: 14, color: "var(--wx-paper)" })}
        <span>${escapeHtml(text || meta.label)}</span>
      </div>
      <span class="wx-tnum" style="font-family:var(--wx-black);font-size:clamp(40px,15cqw,76px);line-height:.8">${escapeHtml(job ? fmtPct(job.completion) : "—")}</span>
      <span style="font-family:var(--wx-mono);font-size:10px;letter-spacing:.04em;opacity:.9">${job ? "LEFT " + escapeHtml(fmtDur(job.remaining)) + " · ETA " + escapeHtml(job.eta || "—") : "NO ACTIVE JOB"}</span>
    </div>
  `;

  return `
    ${styleBlock()}
    <div class="wx-art size-${size}" style="font-family:var(--wx-geo);display:flex;flex-direction:column;background:var(--wx-ink);gap:3px">
      <div style="background:var(--wx-ink);color:var(--wx-paper);padding:8px 14px;display:flex;justify-content:space-between;align-items:baseline;font-family:var(--wx-mono);font-size:12px;letter-spacing:.06em;font-weight:700;flex-shrink:0">
        <span>${escapeHtml((data.label || "OCTOPRINT").toUpperCase())}</span>
        <span style="opacity:.85;font-weight:400">${escapeHtml(data.time || nowTime())}</span>
      </div>
      <div style="flex:1;display:grid;grid-template-columns:1.7fr 1fr;grid-template-rows:1fr 1fr;gap:3px;min-height:0">
        <div style="grid-row:1 / span 2;display:flex;min-width:0">${progressBlock}</div>
        ${tempBlock("HOTEND", temps.tool, TOOL_COLOR)}
        ${tempBlock("BED", temps.bed, BED_COLOR)}
      </div>
      <div class="size-hide-xs" style="background:var(--wx-paper);color:var(--wx-ink);padding:8px 14px;display:flex;align-items:center;gap:8px;font-family:var(--wx-mono);font-size:11px;flex-shrink:0;min-width:0">
        ${WX.icon("cube", { size: 14, color: "var(--wx-ink)" })}
        <span style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:700;letter-spacing:.03em">${escapeHtml(job ? job.name : "—")}</span>
        ${job ? `<span style="margin-left:auto;color:var(--wx-ink-60);flex-shrink:0">${escapeHtml(fmtDur(job.elapsed))} ELAPSED</span>` : ""}
      </div>
    </div>
  `;
}

// ===========================================================
// S3 — SWISS (hairline header, field table, light numerals)
// ===========================================================
function renderS3(data, size) {
  const { meta, text } = stateOf(data);
  const job = data.job;
  const temps = data.temps || {};

  const row = (label, value, dotColor) => `
    <div style="display:flex;align-items:baseline;gap:10px;padding:6px 0;border-bottom:1px solid var(--c-line)">
      ${dotColor ? `<span style="width:7px;height:7px;background:${dotColor};display:inline-block;flex-shrink:0;align-self:center"></span>` : `<span style="width:7px;flex-shrink:0"></span>`}
      <span style="flex:1;min-width:0;font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--wx-ink-60)">${label}</span>
      <span class="wx-tnum" style="font-size:18px;font-weight:300;text-align:right;white-space:nowrap">${value}</span>
    </div>
  `;

  return `
    ${styleBlock()}
    <div class="wx-art size-${size}" style="font-family:var(--wx-swiss);padding:18px 24px;display:flex;flex-direction:column">
      <div style="display:flex;justify-content:space-between;align-items:baseline">
        <span style="font-size:12px;letter-spacing:.2em;font-weight:700;text-transform:uppercase">${escapeHtml(data.label || "OctoPrint")}</span>
        <span style="font-size:10.5px;letter-spacing:.14em;color:${meta.c};text-transform:uppercase">${escapeHtml(text || meta.label)} · ${escapeHtml(data.time || nowTime())}</span>
      </div>
      <div style="height:2px;background:var(--wx-ink);margin:10px 0 6px"></div>
      <div style="display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin-bottom:4px">
        <span style="font-size:12.5px;font-weight:400;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0">${escapeHtml(job ? job.name : "No active job")}</span>
        <span class="wx-tnum" style="font-size:30px;font-weight:200;line-height:.9;flex-shrink:0">${escapeHtml(job ? fmtPct(job.completion) : "—")}</span>
      </div>
      <div style="height:3px;background:var(--wx-paper-3);margin-bottom:8px">
        <div style="width:${job ? Math.max(0, Math.min(100, Number(job.completion) || 0)).toFixed(1) : 0}%;height:100%;background:${meta.c}"></div>
      </div>
      <div style="flex:1;display:flex;flex-direction:column;justify-content:flex-start">
        ${row("Elapsed", escapeHtml(job ? fmtDur(job.elapsed) : "—"), null)}
        ${row("Remaining", escapeHtml(job ? fmtDur(job.remaining) : "—"), null)}
        <div class="size-hide-xs">${row("ETA", escapeHtml((job && job.eta) || "—"), null)}</div>
        ${row("Hotend", escapeHtml(tempStr(temps.tool)), TOOL_COLOR)}
        ${row("Bed", escapeHtml(tempStr(temps.bed)), BED_COLOR)}
      </div>
    </div>
  `;
}

// ===========================================================
// D4 — DATA (progress foregrounded + temp sidebar w/ targets)
// ===========================================================
function renderD4(data, size) {
  const { meta, text } = stateOf(data);
  const job = data.job;
  const temps = data.temps || {};
  const pct = job ? Math.max(0, Math.min(100, Number(job.completion) || 0)) : 0;

  const tempReadout = (label, t, color) => {
    const a = t && t.actual;
    const target = t && t.target;
    const f = target ? Math.max(0, Math.min(1, (Number(a) || 0) / target)) : 0;
    return `
      <div style="display:flex;flex-direction:column;gap:4px">
        <div style="display:flex;align-items:baseline;justify-content:space-between">
          <span style="font-family:var(--wx-mono);font-size:10px;letter-spacing:.08em;color:var(--wx-ink-60)">${label}</span>
          <span class="wx-tnum" style="font-family:var(--wx-mono);font-size:10px;color:var(--wx-ink-60)">${target ? "→ " + fmtTemp(target) : "OFF"}</span>
        </div>
        <span class="wx-tnum" style="font-family:var(--wx-black);font-size:24px;line-height:.85;color:var(--wx-ink)">${escapeHtml(fmtTemp(a))}</span>
        <div style="position:relative;height:6px;background:var(--wx-paper-3)">
          <div style="width:${(f * 100).toFixed(1)}%;height:100%;background:${color}"></div>
          ${target ? `<div style="position:absolute;top:-2px;left:100%;width:2px;height:10px;background:var(--wx-ink);transform:translateX(-2px)"></div>` : ""}
        </div>
      </div>
    `;
  };

  return `
    ${styleBlock()}
    <div class="wx-art size-${size}" style="font-family:${DEFAULT_FONT};padding:14px 18px;display:flex;flex-direction:column">
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px">
        <span style="font-family:var(--wx-black);font-size:16px;letter-spacing:.03em">${escapeHtml((data.label || "OCTOPRINT").toUpperCase())}</span>
        <span style="font-family:var(--wx-mono);font-size:11px;color:${meta.c}">${WX.icon(meta.icon, { size: 12, color: meta.c })} ${escapeHtml((text || meta.label).toUpperCase())} · ${escapeHtml(data.time || nowTime())}</span>
      </div>
      <div style="display:flex;gap:18px;flex:1;min-height:0">
        <div style="flex:1;display:flex;flex-direction:column;min-width:0">
          <div style="display:flex;align-items:baseline;gap:10px">
            <span class="wx-tnum" style="font-family:var(--wx-black);font-size:clamp(40px,12cqw,64px);line-height:.8;color:${meta.c}">${escapeHtml(job ? fmtPct(job.completion) : "—")}</span>
            <span style="font-family:var(--wx-grotesk);font-size:12px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;color:var(--wx-ink-60)">${escapeHtml(job ? job.name : "NO ACTIVE JOB")}</span>
          </div>
          <div style="height:14px;background:var(--wx-paper-3);margin-top:10px">
            <div style="width:${pct.toFixed(1)}%;height:100%;background:${meta.c}"></div>
          </div>
          <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:14px">
            ${[["ELAPSED", job ? fmtDur(job.elapsed) : "—"], ["REMAINING", job ? fmtDur(job.remaining) : "—"], ["ETA", (job && job.eta) || "—"]].map(([k, v]) => `
              <div style="display:flex;flex-direction:column;gap:2px">
                <span style="font-family:var(--wx-mono);font-size:9.5px;letter-spacing:.08em;color:var(--wx-ink-60)">${k}</span>
                <span class="wx-tnum" style="font-family:var(--wx-black);font-size:16px;line-height:1">${escapeHtml(v)}</span>
              </div>`).join("")}
          </div>
        </div>
        <div style="box-sizing:border-box;width:34%;max-width:180px;flex-shrink:0;border-left:1px solid var(--c-line);padding-left:14px;display:flex;flex-direction:column;justify-content:center;gap:14px">
          ${tempReadout("HOTEND", temps.tool, TOOL_COLOR)}
          ${tempReadout("BED", temps.bed, BED_COLOR)}
        </div>
      </div>
    </div>
  `;
}

// ===========================================================
// LEGACY — quiet paper card: charcoal header, hero progress as
// hairline bar, temp rows. No solid accent panels.
// ===========================================================
function renderLegacy(data, size) {
  const { meta, text } = stateOf(data);
  const job = data.job;
  const temps = data.temps || {};
  const headerRight = `${WX.icon(meta.icon, { size: 14, color: "var(--wb-bar-fg)" })}
    <span style="margin-left:6px">${escapeHtml((text || meta.label).toUpperCase())} · ${escapeHtml(data.time || nowTime())}</span>`;

  const tempRow = (label, t, color, icon) => `
    <div style="display:flex;align-items:center;gap:10px">
      ${WX.icon(icon, { size: 18, color })}
      <span style="flex:0 0 30%;font-family:var(--wx-grotesk);font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--wx-ink-60)">${label}</span>
      <div style="flex:1;min-width:24px">
        ${WX.barChart({ value: (t && t.actual) || 0, max: (t && t.target) || (t && t.actual) || 1, color, height: 7 })}
      </div>
      <span class="wx-tnum" style="font-family:var(--wx-black);font-size:14px;min-width:84px;text-align:right">${escapeHtml(tempStr(t))}</span>
    </div>
  `;

  const hero = job
    ? `
      <div style="display:flex;align-items:baseline;justify-content:space-between;gap:12px">
        <div style="display:flex;align-items:center;gap:8px;min-width:0">
          ${WX.icon("cube", { size: 16, color: "var(--wx-ink)" })}
          <span style="font-family:var(--wx-grotesk);font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(job.name)}</span>
        </div>
        <span class="wx-tnum" style="font-family:var(--wx-black);font-size:34px;line-height:.8;color:${meta.c}">${escapeHtml(fmtPct(job.completion))}</span>
      </div>
      <div style="height:12px;background:var(--wx-paper-3);margin-top:2px">
        <div style="width:${Math.max(0, Math.min(100, Number(job.completion) || 0)).toFixed(1)}%;height:100%;background:${meta.c}"></div>
      </div>
      <div style="display:flex;justify-content:space-between;font-family:var(--wx-mono);font-size:10.5px;letter-spacing:.04em;color:var(--wx-ink-60)">
        <span>ELAPSED ${escapeHtml(fmtDur(job.elapsed))}</span>
        <span>LEFT ${escapeHtml(fmtDur(job.remaining))}</span>
        <span>ETA ${escapeHtml(job.eta || "—")}</span>
      </div>`
    : `
      <div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;padding:8px 0">
        ${WX.icon(meta.icon, { size: 40, color: meta.c })}
        <span style="font-family:var(--wx-black);font-size:18px;letter-spacing:.04em;color:${meta.c}">${escapeHtml(text || meta.label)}</span>
        <span style="font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink-60)">NO ACTIVE JOB</span>
      </div>`;

  return `
    ${styleBlock()}
    <div class="wx-art size-${size}" style="font-family:${DEFAULT_FONT};display:flex;flex-direction:column;background:var(--wx-paper)">
      ${WX.darkHeader({ title: data.label || "OCTOPRINT", accent: "ink", right: headerRight })}
      <div style="flex:1;display:flex;flex-direction:column;gap:12px;padding:14px 16px;border-top:2px solid var(--wx-ink);min-height:0">
        ${hero}
        <div style="margin-top:auto;display:flex;flex-direction:column;gap:9px">
          ${tempRow("Hotend", temps.tool, TOOL_COLOR, "thermometer-hot")}
          ${tempRow("Bed", temps.bed, BED_COLOR, "thermometer-simple")}
        </div>
      </div>
    </div>
  `;
}

// ===========================================================
// dispatch
// ===========================================================
const VARIANTS = { r1: renderR1, g2: renderG2, s3: renderS3, d4: renderD4, legacy: renderLegacy };

function renderError(msg) {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <div class="root error" style="padding:12px;font-family:system-ui,sans-serif;color:var(--c-danger);display:flex;align-items:center;gap:8px;height:100%;box-sizing:border-box">
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
  const variant = ctx.cell.options.variant || "r1";
  const renderer = VARIANTS[variant] || renderR1;
  shadow.innerHTML = renderer(data, ctx.cell.size || "md");
}
