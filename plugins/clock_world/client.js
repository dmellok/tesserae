// clock_world — Bauhaus world clock. Each city is a colour-blocked
// row with a hero time, the city name, timezone abbreviation, local
// day-of-week, and a sun/moon icon indicating whether it's daytime
// at that location. Pure client-side, no fetch.

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
      hour: "2-digit",
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
      timeZone: tz, weekday: "short",
    }).format(d).toUpperCase();
  } catch {
    return "";
  }
}

function fmtDate(d, tz) {
  try {
    return new Intl.DateTimeFormat([], {
      timeZone: tz, day: "numeric", month: "short",
    }).format(d).toUpperCase();
  } catch {
    return "";
  }
}

function offset(d, tz) {
  try {
    const parts = new Intl.DateTimeFormat([], {
      timeZone: tz, timeZoneName: "short",
    }).formatToParts(d);
    const tzName = parts.find((p) => p.type === "timeZoneName");
    return tzName ? tzName.value : "";
  } catch {
    return "";
  }
}

// Local hour in the given timezone — used to pick day vs night icon.
function localHour(d, tz) {
  try {
    const h = new Intl.DateTimeFormat([], {
      timeZone: tz, hour: "numeric", hour12: false,
    }).formatToParts(d).find((p) => p.type === "hour");
    return h ? Number(h.value) : 12;
  } catch {
    return 12;
  }
}

// Bauhaus city tints — cycle through the decorative triad. First city
// always lands on accent so the "home" entry feels primary.
const ROW_TINTS = ["row-accent", "row-accent2", "row-accent3", "row-surface"];

export default async function render(shadow, ctx) {
  const cities = parseCities(ctx.cell.options.cities);
  const hour12 = ctx.cell.options.format === "12h";
  const size = ctx.cell.size;

  function rows(d) {
    return cities.map((c, i) => {
      const h = localHour(d, c.tz);
      const isDay = h >= 6 && h < 18;
      const phaseIcon = isDay ? "ph-sun" : "ph-moon-stars";
      const tint = ROW_TINTS[i % ROW_TINTS.length];
      return `
        <div class="cw-row ${tint}" data-day="${isDay ? '1' : '0'}">
          <div class="cw-row-text">
            <div class="cw-city">${escapeHtml(c.label)}</div>
            <div class="cw-time">${escapeHtml(fmtTime(d, c.tz, hour12))}</div>
            <div class="cw-meta">
              <span class="cw-day">${escapeHtml(fmtDay(d, c.tz))} ${escapeHtml(fmtDate(d, c.tz))}</span>
              <span class="cw-off">${escapeHtml(offset(d, c.tz))}</span>
            </div>
          </div>
          <div class="cw-row-icon" aria-hidden="true">
            <i class="ph-bold ${phaseIcon}"></i>
          </div>
        </div>
      `;
    }).join("");
  }

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/plugins/clock_world/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="cw-title">World</span>
        <i class="ph-bold ph-globe-hemisphere-west wb-bar-icon"></i>
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
