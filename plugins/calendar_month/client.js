// calendar_month — month grid with event chips. Bauhaus header bar,
// 7-column day grid, today gets accent fill, out-of-month days fade.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const WEEK_HEADERS = {
  monday: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
  sunday: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
};

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = `
      <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
      <link rel="stylesheet" href="/plugins/calendar_month/client.css">
      <div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>
    `;
    return;
  }

  const size = ctx.cell.size;
  const headers = WEEK_HEADERS[data.week_start] || WEEK_HEADERS.monday;
  const days = Array.isArray(data.days) ? data.days : [];

  const dayCells = days.map((d) => {
    const chips = (d.events || []).map((e) => {
      const t = e.all_day
        ? ""
        : new Date(e.start).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
      return `
        <div class="cm-chip" style="--chip:${escapeHtml(e.colour || "var(--c-accent)")}">
          ${t ? `<span class="cm-chip-t">${escapeHtml(t)}</span>` : ""}
          <span class="cm-chip-s">${escapeHtml(e.summary)}</span>
        </div>
      `;
    }).join("");
    const more = d.extra > 0 ? `<div class="cm-more">+${d.extra}</div>` : "";
    return `
      <div class="cm-day ${d.is_today ? "is-today" : ""} ${!d.in_month ? "is-out" : ""}">
        <div class="cm-num">${d.day}</div>
        <div class="cm-events">${chips}${more}</div>
      </div>
    `;
  }).join("");

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/calendar_month/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="cm-title">${escapeHtml(data.month_name)} ${escapeHtml(String(data.year))}</span>
        <i class="ph-bold ph-calendar wb-bar-icon" aria-hidden="true"></i>
      </header>
      <div class="cm-weekhead">
        ${headers.map((h) => `<span>${h}</span>`).join("")}
      </div>
      <div class="cm-grid">${dayCells}</div>
    </div>
  `;
}
