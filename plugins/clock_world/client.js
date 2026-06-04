// clock_world — Spectra list archetype. One row per configured city
// with its current time in the specified IANA timezone. Times use
// Intl.DateTimeFormat which respects the cell's chosen 12h/24h format.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function parseCities(raw) {
  return String(raw || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [label, tz] = line.split("|").map((s) => s.trim());
      return { label: label || tz, tz: tz || label };
    });
}

function formatTime(now, tz, format) {
  try {
    return new Intl.DateTimeFormat([], {
      timeZone: tz,
      hour: "2-digit",
      minute: "2-digit",
      hour12: format === "12h",
    }).format(now);
  } catch {
    return "—";
  }
}

function dayOffset(now, tz) {
  // What day-of-month is it in that timezone vs the host?
  try {
    const fmt = new Intl.DateTimeFormat([], { timeZone: tz, day: "numeric" });
    const remote = parseInt(fmt.format(now), 10);
    const here = now.getDate();
    if (Number.isNaN(remote)) return "";
    if (remote === here) return "";
    if (remote > here) return "tomorrow";
    return "yesterday";
  } catch {
    return "";
  }
}

export default function render(shadow, ctx) {
  const opts = ctx?.cell?.options || {};
  const cities = parseCities(opts.cities);
  const format = opts.format || "24h";
  const now = new Date();

  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (cities.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="clock_world">
        <div class="w-title"><i class="ph-bold ph-globe"></i><h3>World</h3></div>
        <div class="w-body"><p class="u-muted">No cities configured.</p></div>
      </div>`;
    return;
  }

  const rows = cities.map((c, i) => {
    const t = formatTime(now, c.tz, format);
    const off = dayOffset(now, c.tz);
    const offSpan = off ? `<small style="font-size:.6em;color:var(--text-muted);font-weight:var(--fw-semi);margin-left:.3em">${escapeHtml(off)}</small>` : "";
    return `
      <div class="list-row ${i % 2 ? "is-zebra" : ""}">
        <div class="list-lead">
          <i class="ph-bold ph-globe" style="color:var(--accent-5)"></i>
          <span class="list-title">${escapeHtml(c.label)}</span>
        </div>
        <span class="list-meta">${escapeHtml(t)}${offSpan}</span>
      </div>`;
  }).join("");

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="clock_world">
      <div class="w-title">
        <i class="ph-bold ph-globe" style="color:var(--accent-5)"></i>
        <h3>World</h3>
        <span class="w-title-meta">${cities.length} TZ</span>
      </div>
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
