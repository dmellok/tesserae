// calendar_month, Spectra month-grid. Same per-text-type sizing/spacing/
// label controls as the other calendar_* widgets: dashboard title scale, day
// header (mc-num) scale, event title scale, event block spacing, a
// show_location toggle (appends location in text-display mode; absent
// from the bundled widget), and date_label_style (short/minimal weekday
// abbreviations). No hourly timeline here, so there's no day start/end
// hour option — every other widget's configurability that applies to a
// grid-of-days layout is carried over.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const DOW_SUN = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

// date_label_style cell option: "short" (default, current 3-letter
// behaviour), "minimal" (1-2 chars, just enough to stay unambiguous), or
// "full" (the whole word).
const DOW_MINIMAL = { Sun: "Su", Mon: "M", Tue: "Tu", Wed: "W", Thu: "Th", Fri: "F", Sat: "Sa" };
const DOW_FULL = { Sun: "Sunday", Mon: "Monday", Tue: "Tuesday", Wed: "Wednesday", Thu: "Thursday", Fri: "Friday", Sat: "Saturday" };

export function styleShortLabel(label, style, minimalMap, fullMap) {
  if (style === "minimal") return minimalMap[label] || label.slice(0, 2);
  if (style === "full") return (fullMap && fullMap[label]) || label;
  return label;
}

// Clamps a cell-option slider value, defaulting on missing/non-numeric
// input. Same shape as the other calendar_* widgets' clampScale.
export function clampScale(raw, def, lo, hi) {
  const v = raw === null || raw === undefined || raw === "" ? def : Number(raw);
  return Number.isFinite(v) ? Math.max(lo, Math.min(hi, v)) : def;
}

// Dedupe feed colours preserving first-seen order.
function uniqueColours(events) {
  const seen = new Set();
  const out = [];
  for (const ev of events) {
    const c = ev.colour || "var(--accent-4)";
    if (!seen.has(c)) {
      seen.add(c);
      out.push(c);
    }
  }
  return out;
}

// Heat-tint background, scales the cell's background from surface
// (0 events) up to a `color-mix` overlay of accent-4 at increasing alpha.
function heatBackground(count) {
  if (count <= 0) return "";
  const intensity = Math.min(1, count / 5);
  const alpha = 6 + 18 * intensity;
  return `background: color-mix(in oklab, var(--accent-4) ${alpha.toFixed(0)}%, var(--surface));`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const opts = ctx?.cell?.options || {};
  const display = opts.event_display === "text" ? "text" : "bars";
  const maxPerDay = Math.max(1, Number(opts.max_events_per_day) || 3);
  const heatmap = opts.heatmap !== false;
  const showLocations = opts.show_location === true;
  const labelStyle = ["short", "minimal", "full"].includes(opts.date_label_style) ? opts.date_label_style : "short";
  const titleScale = clampScale(opts.title_scale, 1.0, 0.01, 10.0);
  const headerScale = clampScale(opts.header_scale, 1.0, 0.01, 10.0);
  const eventScale = clampScale(opts.event_title_scale, 1.0, 0.01, 10.0);
  const rowPad = clampScale(opts.event_row_padding_em, 0.0, 0.0, 3.0);
  const styleAttr = `--mc-dash-title-scale:${titleScale};--mc-header-scale:${headerScale};--mc-event-scale:${eventScale};--mc-row-pad:${rowPad}em;`;
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="calendar_month">
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const days = Array.isArray(data.days) ? data.days : [];
  const dowNames = (data.week_start === "sunday") ? DOW_SUN : DOW;
  const monthName = (data.month_name || "").toUpperCase();
  const year = data.year || "";
  const weekStartLabel = (data.week_start === "sunday") ? "WEEK STARTS SUN" : "WEEK STARTS MON";

  const dowHeader = dowNames
    .map((name) => `<span>${escapeHtml(styleShortLabel(name, labelStyle, DOW_MINIMAL, DOW_FULL))}</span>`)
    .join("");

  const cells = days.map((d) => {
    const classes = ["mc-cell"];
    if (!d.in_month) classes.push("is-out");
    if (d.is_today) classes.push("is-today");

    const events = Array.isArray(d.events) ? d.events : [];
    const count = events.length;
    const heatStyle = (heatmap && d.in_month) ? heatBackground(count) : "";

    let body = "";
    if (display === "text") {
      const visible = events.slice(0, maxPerDay);
      const remainder = Math.max(0, count - maxPerDay);
      body = `
        ${visible.map((ev) => {
          const colour = ev.colour || "var(--accent-4)";
          const location = showLocations && ev.location ? ev.location : "";
          const text = location ? `${ev.summary || ""} · ${location}` : (ev.summary || "");
          return `<span class="mc-text" style="border-left-color:${colour}" title="${escapeHtml(text)}">${escapeHtml(text)}</span>`;
        }).join("")}
        ${remainder > 0 ? `<span class="mc-more">+${remainder}</span>` : ""}`;
    } else {
      const colours = uniqueColours(events);
      const visibleColours = colours.slice(0, 4);
      const overflow = colours.length - visibleColours.length;
      const stripsRow = visibleColours.length
        ? `<div class="mc-strips">
            ${visibleColours.map((c) => `<span class="mc-strip" style="background:${c}"></span>`).join("")}
          </div>`
        : "";
      const moreChip = (count > visibleColours.length || overflow > 0)
        ? `<span class="mc-more">${count}</span>`
        : "";
      body = `${stripsRow}${moreChip}`;
    }

    return `
      <div class="${classes.join(" ")}" style="${heatStyle}">
        <span class="mc-num">${escapeHtml(String(d.day))}</span>
        <div class="mc-dots">${body}</div>
      </div>`;
  }).join("");

  const layout = `
    .mc-strips {
      display: flex;
      gap: var(--stroke-1);
      width: 100%;
      height: var(--stroke-3);
    }
    .mc-strip {
      flex: 1 1 0;
      display: block;
    }
    .mc-cell .mc-dots {
      display: flex;
      flex-direction: column;
      gap: 0.15em;
    }
    .mc-more {
      align-self: flex-end;
      font-size: calc(var(--fs-caption) * var(--mc-event-scale, 1));
      font-weight: var(--fw-black);
      letter-spacing: var(--ls-label);
      color: var(--text-muted);
      font-variant-numeric: tabular-nums;
      line-height: 1;
    }
    @container (min-width: 700px) {
      .mc-strips { height: calc(var(--stroke-3) * 1.4); }
    }

    /* header_scale: the day-number badge in each cell.
       event_title_scale: event text in text-display mode.
       event_row_padding_em: extra padding inside each cell, on top of
       the shared spectra-widgets.css default (0 = default).
       title_scale: the "MONTH YYYY" dashboard title. */
    .mc-num { font-size: calc(var(--fs-body) * var(--mc-header-scale, 1)); }
    .mc-text { font-size: calc(var(--fs-caption) * var(--mc-event-scale, 1)); }
    .mc-cell { padding: calc(var(--space-1) + var(--mc-row-pad, 0em)) var(--space-2); }
    .cal-head-title { font-size: calc(1em * var(--mc-dash-title-scale, 1)); }
    .cal-head-meta { font-size: calc(1em * var(--mc-dash-title-scale, 1)); }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="calendar_month" style="${styleAttr}">
      <div class="w-body" style="gap:var(--space-3)">
        <div class="cal-head">
          <div class="cal-head-row">
            <span class="cal-head-title">${escapeHtml(monthName)} <span class="num">${escapeHtml(String(year))}</span></span>
            <span class="cal-head-meta">${escapeHtml(weekStartLabel)}</span>
          </div>
          <div class="cal-head-rule"></div>
        </div>
        <div class="mc-body" style="flex:1 1 auto;min-height:0;display:flex;flex-direction:column;gap:var(--space-2)">
          <div class="mc-dow">${dowHeader}</div>
          <div class="mc-grid">${cells}</div>
        </div>
      </div>
    </div>`;
}
