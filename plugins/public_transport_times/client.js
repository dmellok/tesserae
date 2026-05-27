// public_transport_times — Bauhaus PTV departures card.
//
// Layout:
//   1. Inverted header bar (mark + stop name + mode icon)
//   2. One row per departure: route number/letter, destination, minutes
//      until departure, on-time / delay tag.
//
// Live countdown updates every 15s so "5 min" doesn't sit stale on the
// dashboard between data refreshes.

const ROUTE_TYPE_ICON = {
  0: "train",       // Train (Metro)
  1: "tram",        // Tram
  2: "bus",         // Bus
  3: "train",       // V/Line
  4: "bus",         // Night Bus
};
const ROUTE_TYPE_LABEL = {
  0: "Train",
  1: "Tram",
  2: "Bus",
  3: "V/Line",
  4: "Night Bus",
};

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function minsUntil(iso) {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return null;
  return Math.round((t - Date.now()) / 60000);
}

function fmtMins(n) {
  if (n == null) return "—";
  if (n <= 0) return "now";
  if (n < 60) return `${n}m`;
  const h = Math.floor(n / 60);
  const m = n % 60;
  return m ? `${h}h${m}m` : `${h}h`;
}

function delayMins(scheduled, estimated) {
  if (!scheduled || !estimated) return 0;
  return Math.round((new Date(estimated).getTime() - new Date(scheduled).getTime()) / 60000);
}

function renderError(msg) {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/plugins/public_transport_times/client.css">
    <div class="root error">
      <i class="ph ph-warning-circle" aria-hidden="true"></i>
      <span>${escapeHtml(msg)}</span>
    </div>
  `;
}

function renderRow(d, modeIcon) {
  const departureIso = d.estimated || d.scheduled;
  const delay = delayMins(d.scheduled, d.estimated);
  let tag = "";
  let tagClass = "pt-tag--ontime";
  if (delay >= 2) { tag = `+${delay}m`; tagClass = "pt-tag--late"; }
  else if (delay <= -2) { tag = `${delay}m`; tagClass = "pt-tag--early"; }
  else if (d.scheduled) { tag = "On time"; }
  if (d.at_platform) { tag = "At pl."; tagClass = "pt-tag--now"; }

  const routeBadge = d.route_number || (d.route_name ? d.route_name.split(" ")[0] : "—");

  return `
    <div class="pt-row">
      <i class="ph-bold ph-${modeIcon} pt-row-icon" aria-hidden="true"></i>
      <span class="pt-row-route">${escapeHtml(routeBadge)}</span>
      <span class="pt-row-dest">${escapeHtml(d.direction_name || d.route_name || "")}</span>
      <span class="pt-row-mins" data-iso="${escapeHtml(departureIso || "")}">${fmtMins(minsUntil(departureIso))}</span>
      ${tag ? `<span class="pt-tag ${tagClass}">${escapeHtml(tag)}</span>` : ""}
    </div>
  `;
}

export default async function render(shadow, ctx) {
  // Clear any prior live-countdown timer — render() may be called
  // repeatedly on theme/option changes.
  if (shadow.__ptTimer) {
    clearInterval(shadow.__ptTimer);
    shadow.__ptTimer = null;
  }

  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = renderError(data.error);
    return;
  }

  const size = ctx.cell.size;
  const departures = Array.isArray(data.departures) ? data.departures : [];
  const modeIcon = ROUTE_TYPE_ICON[data.route_type] || "train";
  const modeLabel = ROUTE_TYPE_LABEL[data.route_type] || "";

  if (!departures.length) {
    shadow.innerHTML = `
      <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
      <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
      <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
      <link rel="stylesheet" href="/plugins/public_transport_times/client.css">
      <div class="root size-${size}">
        <header class="wb-bar">
          <span class="wb-mark" aria-hidden="true"></span>
          <span class="wb-title">${escapeHtml(data.stop_name || "Stop")}</span>
          <i class="ph-bold ph-${modeIcon} wb-bar-mode" aria-hidden="true"></i>
        </header>
        <div class="pt-empty">
          <i class="ph-duotone ph-moon-stars" aria-hidden="true"></i>
          <div class="pt-empty-primary">No departures</div>
          <div class="pt-empty-secondary">${escapeHtml(modeLabel)} services aren't running right now.</div>
        </div>
      </div>
    `;
    return;
  }

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/public_transport_times/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="wb-title">${escapeHtml(data.stop_name || "Stop")}</span>
        <i class="ph-bold ph-${modeIcon} wb-bar-mode" aria-hidden="true"></i>
      </header>
      <section class="pt-rows">
        ${departures.map((d) => renderRow(d, modeIcon)).join("")}
      </section>
    </div>
  `;

  // Live tick — update the minutes column without re-fetching.
  const tick = () => {
    shadow.querySelectorAll("[data-iso]").forEach((el) => {
      el.textContent = fmtMins(minsUntil(el.getAttribute("data-iso")));
    });
  };
  shadow.__ptTimer = setInterval(tick, 15000);
}
