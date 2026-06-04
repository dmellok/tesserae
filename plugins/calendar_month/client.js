// calendar_month — Spectra month-grid. Display header shows the
// month + year with WEEK STARTS MON/SUN as the meta line; today's
// cell gets a filled accent-1 block behind its day number. Events
// surface as up to N stacked coloured bars OR text rows, controlled
// by the ``event_display`` cell option.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const DOW_SUN = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const opts = ctx?.cell?.options || {};
  const display = opts.event_display === "text" ? "text" : "bars";
  const maxPerDay = Math.max(1, Number(opts.max_events_per_day) || 3);
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

  const dowHeader = dowNames.map((name) => `<span>${escapeHtml(name)}</span>`).join("");

  const cells = days.map((d) => {
    const classes = ["mc-cell"];
    if (!d.in_month) classes.push("is-out");
    if (d.is_today) classes.push("is-today");

    const events = Array.isArray(d.events) ? d.events : [];
    const visible = events.slice(0, maxPerDay);
    const remainder = Math.max(0, events.length - maxPerDay);

    const body = display === "text"
      ? visible.map((ev) => {
          const colour = ev.colour || "var(--accent-4)";
          return `<span class="mc-text" style="border-left-color:${colour}" title="${escapeHtml(ev.summary || "")}">${escapeHtml(ev.summary || "")}</span>`;
        }).join("")
      : visible.map((ev) => {
          const colour = ev.colour || "var(--accent-4)";
          return `<span class="mc-dot" style="background:${colour}" title="${escapeHtml(ev.summary || "")}"></span>`;
        }).join("");
    const more = remainder > 0
      ? `<span class="mc-more">+${remainder}</span>`
      : "";

    return `
      <div class="${classes.join(" ")}">
        <span class="mc-num">${escapeHtml(String(d.day))}</span>
        <div class="mc-dots">${body}${more}</div>
      </div>`;
  }).join("");

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="calendar_month">
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
