// calendar_week — seven-day strip, each column a day's agenda.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = `
      <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
      <link rel="stylesheet" href="/plugins/calendar_week/client.css">
      <div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>
    `;
    return;
  }
  const size = ctx.cell.size;
  const days = Array.isArray(data.days) ? data.days : [];

  const cols = days.map((d) => {
    const evs = (d.events || []).map((e) => {
      const t = e.all_day
        ? "ALL DAY"
        : new Date(e.start).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
      return `
        <div class="cw-ev" style="--chip:${escapeHtml(e.colour || "var(--theme-accent)")}">
          <span class="cw-ev-t">${escapeHtml(t)}</span>
          <span class="cw-ev-s">${escapeHtml(e.summary)}</span>
        </div>
      `;
    }).join("");
    return `
      <div class="cw-col ${d.is_today ? "is-today" : ""}">
        <div class="cw-colhead">
          <span class="cw-dow">${DAYS[d.weekday]}</span>
          <span class="cw-dnum">${d.day}</span>
        </div>
        <div class="cw-evs">${evs || `<div class="cw-empty">—</div>`}</div>
      </div>
    `;
  }).join("");

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/calendar_week/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="cw-title">This week · ${escapeHtml(data.start)} → ${escapeHtml(data.end)}</span>
        <i class="ph-bold ph-calendar wb-bar-icon" aria-hidden="true"></i>
      </header>
      <div class="cw-grid">${cols}</div>
    </div>
  `;
}
