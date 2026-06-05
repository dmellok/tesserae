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
  // What day-of-month is it in that timezone vs the host? Returns
  // "tomorrow" / "yesterday" / "" so the row can mark a calendar
  // shift with an icon instead of pushing extra text into the time
  // column.
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

// Return the local hour (0-23) at the given timezone, or -1 on
// failure. Used to pick a day vs night icon for the leading slot
// instead of the generic globe every row used to share.
function localHour(now, tz) {
  try {
    const fmt = new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      hour: "2-digit",
      hour12: false,
    });
    const h = parseInt(fmt.format(now), 10);
    return Number.isFinite(h) ? h % 24 : -1;
  } catch {
    return -1;
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
    const h = localHour(now, c.tz);
    // Day / night by hour band (06–17 inclusive = day). The icon
    // replaces the old generic ph-globe lead so the row tells you
    // at a glance whether it's the middle of the night in Tokyo
    // without having to read the time.
    const isDay = h >= 6 && h <= 17;
    const leadIcon = h < 0 ? "ph-globe" : (isDay ? "ph-sun" : "ph-moon");
    const leadColor = isDay ? "var(--accent-2)" : "var(--accent-5)";
    // Day-shift cue. Lives in its own slot to the LEFT of the time
    // so the time itself stays in a single right-aligned column —
    // adding "yesterday" inline used to push the time leftward and
    // break the column alignment across rows. ph-arrow-down for a
    // calendar day BEHIND the host; ph-arrow-up for AHEAD.
    let shiftIcon = "";
    if (off === "yesterday") {
      shiftIcon = `<i class="ph-bold ph-arrow-down" title="yesterday" style="font-size:.85em;color:var(--text-muted);margin-right:.3em;vertical-align:-.05em"></i>`;
    } else if (off === "tomorrow") {
      shiftIcon = `<i class="ph-bold ph-arrow-up" title="tomorrow" style="font-size:.85em;color:var(--text-muted);margin-right:.3em;vertical-align:-.05em"></i>`;
    }
    return `
      <div class="list-row ${i % 2 ? "is-zebra" : ""}">
        <div class="list-lead">
          <i class="ph-bold ${leadIcon}" style="color:${leadColor}"></i>
          <span class="list-title">${escapeHtml(c.label)}</span>
        </div>
        <span class="list-meta" style="font-variant-numeric:tabular-nums">${shiftIcon}${escapeHtml(t)}</span>
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
