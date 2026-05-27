// clock_world — multiple cities, one cell, no server fetch.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function parseCities(s) {
  return (s || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [label, tz] = line.split("|").map((x) => (x || "").trim());
      return { label: label || tz, tz: tz || label };
    })
    .filter((c) => c.tz);
}

function fmtTime(d, tz, hour12) {
  try {
    return new Intl.DateTimeFormat([], {
      timeZone: tz,
      hour:   "2-digit",
      minute: "2-digit",
      hour12: hour12,
    }).format(d);
  } catch {
    return "—";
  }
}

function fmtDay(d, tz) {
  try {
    return new Intl.DateTimeFormat([], {
      timeZone: tz, weekday: "short", day: "numeric", month: "short",
    }).format(d);
  } catch {
    return "";
  }
}

function offset(d, tz) {
  try {
    const opts = { timeZone: tz, timeZoneName: "short" };
    const parts = new Intl.DateTimeFormat([], opts).formatToParts(d);
    const tzName = parts.find((p) => p.type === "timeZoneName");
    return tzName ? tzName.value : "";
  } catch {
    return "";
  }
}

export default async function render(shadow, ctx) {
  const cities = parseCities(ctx.cell.options.cities);
  const hour12 = ctx.cell.options.format === "12h";
  const size = ctx.cell.size;

  function rows(d) {
    return cities.map((c) => `
      <div class="cw-row">
        <span class="cw-city" title="${escapeHtml(c.tz)}">${escapeHtml(c.label)}</span>
        <span class="cw-time">${escapeHtml(fmtTime(d, c.tz, hour12))}</span>
        <span class="cw-off">${escapeHtml(offset(d, c.tz))}</span>
        <span class="cw-day">${escapeHtml(fmtDay(d, c.tz))}</span>
      </div>
    `).join("");
  }

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/clock_world/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="cw-title">World</span>
        <i class="ph-bold ph-globe wb-bar-icon"></i>
      </header>
      <section class="cw-list" data-cw-list>${rows(new Date())}</section>
    </div>
  `;

  function tick() {
    const list = shadow.querySelector("[data-cw-list]");
    if (list) list.innerHTML = rows(new Date());
  }
  if (shadow.__cwTimer) clearInterval(shadow.__cwTimer);
  shadow.__cwTimer = setInterval(tick, 15000);
}
