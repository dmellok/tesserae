// calendar_day — today's agenda as a chronological list.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = `
      <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
      <link rel="stylesheet" href="/plugins/calendar_day/client.css">
      <div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>
    `;
    return;
  }
  const size = ctx.cell.size;
  const events = Array.isArray(data.events) ? data.events : [];

  if (!events.length) {
    shadow.innerHTML = `
      <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
      <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
      <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
      <link rel="stylesheet" href="/plugins/calendar_day/client.css">
      <div class="root size-${size}">
        <header class="wb-bar">
          <span class="wb-mark" aria-hidden="true"></span>
          <span class="cd-title">${escapeHtml(fmtDate(data.now))}</span>
          <i class="ph-bold ph-calendar-check wb-bar-icon" aria-hidden="true"></i>
        </header>
        <div class="cd-empty">
          <i class="ph-duotone ph-coffee" aria-hidden="true"></i>
          <div class="cd-empty-primary">Nothing scheduled</div>
          <div class="cd-empty-secondary">Enjoy the breathing room.</div>
        </div>
      </div>
    `;
    return;
  }

  const rows = events.map((e) => {
    const t = e.all_day
      ? "ALL DAY"
      : fmtTime(e.start) + (e.end && !e.all_day ? "–" + fmtTime(e.end) : "");
    return `
      <div class="cd-ev" style="--chip:${escapeHtml(e.colour || "var(--c-accent)")}">
        <div class="cd-when">${escapeHtml(t)}</div>
        <div class="cd-body">
          <div class="cd-summary">${escapeHtml(e.summary)}</div>
          ${e.location ? `<div class="cd-loc"><i class="ph ph-map-pin"></i>${escapeHtml(e.location)}</div>` : ""}
        </div>
      </div>
    `;
  }).join("");

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/calendar_day/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="cd-title">${escapeHtml(fmtDate(data.now))}</span>
        <span class="cd-count">${data.count}</span>
      </header>
      <section class="cd-list">${rows}</section>
    </div>
  `;
}
