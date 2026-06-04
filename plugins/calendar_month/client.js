// calendar_month — Spectra month-grid archetype. 7-col day-of-week
// header + 5-6 row matrix of day cells. Out-of-month days dim down,
// today gets an accent-4 day number, events surface as up to three
// stacked bars in the feed colour with a "+N more" remainder.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const DOW_SUN = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

const MAX_BARS = 3;

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="calendar_month">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Month</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const days = Array.isArray(data.days) ? data.days : [];
  const dowNames = (data.week_start === "sunday") ? DOW_SUN : DOW;
  const monthName = data.month_name || "";
  const year = data.year || "";

  const dowHeader = dowNames.map((name) => `<span>${escapeHtml(name)}</span>`).join("");

  const cells = days.map((d) => {
    const classes = ["mc-cell"];
    if (!d.in_month) classes.push("is-out");
    if (d.is_today) classes.push("is-today");

    const events = Array.isArray(d.events) ? d.events : [];
    const visible = events.slice(0, MAX_BARS);
    const remainder = Math.max(0, events.length - MAX_BARS);
    const bars = visible.map((ev) => {
      const colour = ev.colour || "var(--accent-4)";
      return `<span class="mc-dot" style="background:${colour}" title="${escapeHtml(ev.summary || "")}"></span>`;
    }).join("");
    const more = remainder > 0
      ? `<span class="mc-more">+${remainder}</span>`
      : "";

    return `
      <div class="${classes.join(" ")}">
        <span class="mc-num">${escapeHtml(String(d.day))}</span>
        <div class="mc-dots">${bars}${more}</div>
      </div>`;
  }).join("");

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="calendar_month">
      <div class="w-title">
        <i class="ph-bold ph-calendar" style="color:var(--accent-3)"></i>
        <h3>${escapeHtml(monthName)} ${escapeHtml(String(year))}</h3>
      </div>
      <div class="w-body mc-body">
        <div class="mc-dow">${dowHeader}</div>
        <div class="mc-grid">${cells}</div>
      </div>
    </div>`;
}
