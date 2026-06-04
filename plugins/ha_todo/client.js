// ha_todo — items from a Home Assistant todo list.
//
// Server.py shapes the response into:
//   data.title              — friendly name of the list
//   data.entity_id          — todo.<name>
//   data.items[]            — {uid, summary, status, due, description}
//   data.needs_action_count — across the whole list, before truncation
//   data.completed_count    — same
//   data.total_count        — = needs_action + completed
//
// Four directions pickable per-cell via `variant`:
//
//   r1  Refined    Dark Bauhaus header (list · counts) + numbered list
//                  of summaries with due dates right-aligned.
//   g2  Geometric  Colour-block tile per item with status colour band.
//   s3  Swiss      Hairline header + tabular rows: number · summary · due.
//   d4  Data       Big stat block (X needs action / Y done) then a
//                  compact list with strikethrough on completed.

import { WX } from "../weather_core/static/wx-common.js";

const DEFAULT_FONT = "var(--wx-grotesk)";

function escapeHtml(s) { return WX.escapeHtml(s); }

function nowTime() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

// Friendly relative due date. HA returns ISO datetime, ISO date, or
// null. We want short, scannable: TODAY · TOMORROW · MON 9 JUN · OVERDUE.
function fmtDue(iso) {
  if (!iso) return "";
  // YYYY-MM-DD (date-only) vs full ISO datetime. Treat date-only as
  // "midnight local" so today/tomorrow comparisons work without an
  // accidental day skew.
  const d = iso.length === 10 ? new Date(iso + "T00:00:00") : new Date(iso);
  if (isNaN(d.getTime())) return "";
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const target = new Date(d);
  target.setHours(0, 0, 0, 0);
  const diffDays = Math.round((target - today) / 86400000);
  if (diffDays < 0) return "OVERDUE";
  if (diffDays === 0) return "TODAY";
  if (diffDays === 1) return "TOMORROW";
  if (diffDays < 7) {
    return d.toLocaleDateString(undefined, { weekday: "short" }).toUpperCase();
  }
  return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }).toUpperCase();
}

// Tone for a due date — overdue → danger, today → warn, future → soft.
function dueTone(iso) {
  if (!iso) return "var(--wx-ink-60)";
  const due = fmtDue(iso);
  if (due === "OVERDUE") return "var(--c-danger)";
  if (due === "TODAY") return "var(--c-warn)";
  return "var(--wx-ink-60)";
}

function statusIcon(status) {
  return status === "completed" ? "check-circle" : "circle";
}

function styleBlock() {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus-wx.css">
  `;
}

function emptyState(title) {
  return `
    <div style="flex:1;display:grid;place-items:center;padding:24px;text-align:center;color:var(--wx-ink-60);font-family:${DEFAULT_FONT}">
      <div>
        <i class="ph-duotone ph-check-circle" style="font-size:36px;display:block;margin-bottom:8px"></i>
        <div style="font-size:13px;font-weight:700;letter-spacing:.06em;text-transform:uppercase">${escapeHtml(title || "All done")}</div>
        <div style="font-size:11px;margin-top:4px">No items needing action.</div>
      </div>
    </div>
  `;
}

// ===========================================================
// R1 — REFINED (hero count panel + numbered list)
// ===========================================================
function renderR1(data) {
  const items = data.items || [];
  const open = data.needs_action_count || 0;
  const done = data.completed_count || 0;
  const headerRight = `${open} OPEN · ${escapeHtml(data.time || nowTime())}`;
  return `
    ${styleBlock()}
    <style>
      .ht-r1-hero { display:grid; grid-template-columns:1fr 1fr; border-top:3px solid var(--c-accent); }
      .ht-r1-stat { padding:clamp(8px, 1.6cqh, 14px) clamp(12px, 2.4cqw, 20px); display:flex; flex-direction:column; gap:2px; min-width:0; }
      .ht-r1-stat--open { background:var(--c-accent); color:var(--wx-red-fg); }
      .ht-r1-stat--done { background:var(--wx-tint); color:var(--c-text); }
      .ht-r1-stat-label { font-family:var(--wx-mono); font-size:11px; font-weight:700; letter-spacing:.1em; text-transform:uppercase; opacity:.9; }
      .ht-r1-stat-num { font-family:var(--wx-black); font-size:clamp(28px, 6cqw, 44px); line-height:1; }
      .ht-r1-list { flex:1; display:flex; flex-direction:column; border-top:3px solid var(--c-accent); overflow:hidden; background:var(--wx-paper); }
      .ht-r1-row { display:grid; grid-template-columns:32px 1fr auto; align-items:center; gap:10px; padding:8px 16px; }
      .ht-r1-row + .ht-r1-row { border-top:1px solid var(--c-line); }
      .ht-r1-row.is-done { opacity:.55; }
      .ht-r1-row.is-done .ht-r1-summary { text-decoration:line-through; }
      .ht-r1-num { font-family:var(--wx-mono); font-size:11px; font-weight:700; color:var(--c-accent); letter-spacing:.04em; }
      .ht-r1-summary { font-family:var(--wx-grotesk); font-size:13.5px; font-weight:600; color:var(--c-text); min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
      .ht-r1-due { font-family:var(--wx-mono); font-size:10px; font-weight:700; letter-spacing:.06em; }

      @container (max-height: 240px) {
        .ht-r1-hero { display:none; }
      }
    </style>
    <div class="wx-art" style="font-family:${DEFAULT_FONT};display:flex;flex-direction:column">
      ${WX.darkHeader({ title: data.title || "TODO", accent: "red", right: headerRight })}
      <div class="ht-r1-hero">
        <div class="ht-r1-stat ht-r1-stat--open">
          <span class="ht-r1-stat-label">Open</span>
          <span class="wx-tnum ht-r1-stat-num">${open}</span>
        </div>
        <div class="ht-r1-stat ht-r1-stat--done">
          <span class="ht-r1-stat-label" style="color:var(--wx-ink-60)">Done</span>
          <span class="wx-tnum ht-r1-stat-num" style="color:var(--c-accent)">${done}</span>
        </div>
      </div>
      <div class="ht-r1-list">
        ${items.length === 0 ? emptyState(data.title) : items.map((it, i) => {
          const isDone = it.status === "completed";
          const due = fmtDue(it.due);
          return `
            <div class="ht-r1-row${isDone ? " is-done" : ""}">
              <span class="wx-tnum ht-r1-num">${String(i + 1).padStart(2, "0")}</span>
              <span class="ht-r1-summary">${escapeHtml(it.summary)}</span>
              ${due
                ? `<span class="ht-r1-due" style="color:${dueTone(it.due)}">${escapeHtml(due)}</span>`
                : `<span></span>`}
            </div>
          `;
        }).join("")}
      </div>
    </div>
  `;
}

// ===========================================================
// G2 — GEOMETRIC (colour-block tiles, 2/3-col grid)
// ===========================================================
function renderG2(data) {
  const items = data.items || [];
  const cols = items.length <= 4 ? 2 : items.length <= 9 ? 3 : 4;
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-geo);display:flex;flex-direction:column;background:var(--wx-ink);gap:3px">
      <div style="background:var(--wx-ink);color:var(--wx-paper);padding:8px 14px;display:flex;justify-content:space-between;align-items:baseline;font-family:var(--wx-mono);font-size:12px;letter-spacing:.06em;font-weight:700">
        <span>${escapeHtml((data.title || "TODO").toUpperCase())}</span>
        <span style="opacity:.85;font-weight:400">${data.needs_action_count || 0} OPEN</span>
      </div>
      ${items.length === 0 ? `<div style="flex:1;background:var(--wx-paper);display:flex">${emptyState(data.title)}</div>` : `
        <div style="flex:1;display:grid;grid-template-columns:repeat(${cols},1fr);grid-auto-rows:1fr;gap:3px">
          ${items.map((it) => {
            const done = it.status === "completed";
            const overdue = !done && fmtDue(it.due) === "OVERDUE";
            const today = !done && fmtDue(it.due) === "TODAY";
            const bg = done ? "var(--c-ok)" : overdue ? "var(--c-danger)" : today ? "var(--c-warn)" : "var(--wx-blue)";
            const ink = done || overdue ? "var(--c-bg)" : today ? "var(--c-text)" : "var(--wx-blue-fg)";
            const due = fmtDue(it.due);
            return `
              <div style="background:${bg};color:${ink};padding:12px 14px;display:flex;flex-direction:column;justify-content:space-between;min-width:0">
                <div style="font-family:var(--wx-mono);font-size:10.5px;font-weight:700;letter-spacing:.04em;line-height:1.15;${done ? "text-decoration:line-through;" : ""};max-height:3.5em;overflow:hidden">${escapeHtml(it.summary)}</div>
                <div style="display:flex;justify-content:space-between;align-items:baseline;font-family:var(--wx-mono);font-size:9.5px;letter-spacing:.08em;font-weight:700">
                  <span>${due ? escapeHtml(due) : (done ? "DONE" : "OPEN")}</span>
                  ${WX.icon(statusIcon(it.status), { size: 14, color: ink })}
                </div>
              </div>
            `;
          }).join("")}
        </div>
      `}
    </div>
  `;
}

// ===========================================================
// S3 — SWISS (hairline header, table-like rows)
// ===========================================================
function renderS3(data) {
  const items = data.items || [];
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-swiss);padding:18px 24px;display:flex;flex-direction:column">
      <div style="display:flex;justify-content:space-between;align-items:baseline">
        <span style="font-size:12px;letter-spacing:.2em;font-weight:700;text-transform:uppercase">${escapeHtml(data.title || "Todo")}</span>
        <span style="font-size:10.5px;letter-spacing:.16em;color:var(--wx-ink-60)">
          ${data.needs_action_count || 0} open · ${data.completed_count || 0} done
        </span>
      </div>
      <div style="height:2px;background:var(--wx-ink);margin:10px 0 4px"></div>
      ${items.length === 0 ? emptyState(data.title) : `
        <div style="flex:1;display:flex;flex-direction:column">
          ${items.map((it, i) => {
            const done = it.status === "completed";
            const due = fmtDue(it.due);
            return `
              <div style="display:grid;grid-template-columns:24px 1fr auto;align-items:baseline;gap:14px;padding:6px 0;${i < items.length - 1 ? "border-bottom:1px solid color-mix(in oklab, var(--wx-ink) 18%, transparent);" : ""}${done ? "opacity:.55;" : ""}">
                <span class="wx-tnum" style="font-family:var(--wx-swiss);font-size:10px;font-weight:500;color:var(--wx-ink-60);letter-spacing:.06em">${String(i + 1).padStart(2, "0")}</span>
                <span style="font-size:13.5px;font-weight:500;color:var(--wx-ink);min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;${done ? "text-decoration:line-through;" : ""}">${escapeHtml(it.summary)}</span>
                <span style="font-family:var(--wx-mono);font-size:10px;font-weight:700;letter-spacing:.08em;color:${dueTone(it.due)}">${escapeHtml(due)}</span>
              </div>
            `;
          }).join("")}
        </div>
      `}
    </div>
  `;
}

// ===========================================================
// D4 — DATA (stat block + compact list)
// ===========================================================
function renderD4(data) {
  const items = data.items || [];
  const open = data.needs_action_count || 0;
  const done = data.completed_count || 0;
  const total = data.total_count || (open + done);
  const pctDone = total ? Math.round((done / total) * 100) : 0;
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};display:flex;flex-direction:column;background:var(--wx-paper)">
      <div style="background:var(--wx-ink);color:var(--wx-paper);padding:10px 16px;display:flex;justify-content:space-between;align-items:baseline">
        <span style="font-family:var(--wx-mono);font-size:11px;letter-spacing:.1em;font-weight:700;text-transform:uppercase">${escapeHtml(data.title || "Todo")}</span>
        <span style="font-family:var(--wx-mono);font-size:10px;letter-spacing:.1em;opacity:.85">${pctDone}% DONE</span>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:2px;background:var(--wx-ink)">
        <div style="background:var(--wx-paper);padding:12px 16px">
          <div style="font-family:var(--wx-mono);font-size:9.5px;letter-spacing:.12em;font-weight:700;color:var(--wx-ink-60);text-transform:uppercase">OPEN</div>
          <div class="wx-tnum" style="font-family:var(--wx-black);font-size:42px;line-height:1;color:var(--c-danger)">${open}</div>
        </div>
        <div style="background:var(--wx-paper);padding:12px 16px">
          <div style="font-family:var(--wx-mono);font-size:9.5px;letter-spacing:.12em;font-weight:700;color:var(--wx-ink-60);text-transform:uppercase">DONE</div>
          <div class="wx-tnum" style="font-family:var(--wx-black);font-size:42px;line-height:1;color:var(--c-ok)">${done}</div>
        </div>
      </div>
      <div style="flex:1;background:var(--wx-paper);border-top:2px solid var(--wx-ink);overflow:hidden">
        ${items.length === 0 ? emptyState(data.title) : items.slice(0, 6).map((it) => {
          const isDone = it.status === "completed";
          const due = fmtDue(it.due);
          return `
            <div style="display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:10px;padding:5px 14px;border-bottom:1px solid color-mix(in oklab, var(--wx-ink) 10%, transparent);${isDone ? "opacity:.5;" : ""}">
              ${WX.icon(statusIcon(it.status), { size: 13, color: isDone ? "var(--c-ok)" : "var(--c-danger)" })}
              <span style="font-family:var(--wx-grotesk);font-size:11.5px;font-weight:600;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;${isDone ? "text-decoration:line-through;" : ""}">${escapeHtml(it.summary)}</span>
              <span style="font-family:var(--wx-mono);font-size:9px;font-weight:700;letter-spacing:.08em;color:${dueTone(it.due)}">${escapeHtml(due)}</span>
            </div>
          `;
        }).join("")}
      </div>
    </div>
  `;
}

const VARIANTS = {
  r1: renderR1,
  g2: renderG2,
  s3: renderS3,
  d4: renderD4,
};

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  const opts = ctx.cell.options || {};

  if (data.error) {
    shadow.innerHTML = `${styleBlock()}
      <div class="wb-root is-error">
        <i class="ph-bold ph-warning-circle" aria-hidden="true"></i>
        <span>${escapeHtml(data.error)}</span>
      </div>`;
    return;
  }

  const variant = (opts.variant || "r1").toLowerCase();
  const renderer = VARIANTS[variant] || renderR1;
  shadow.innerHTML = renderer({
    ...data,
    time: nowTime(),
  });
}
