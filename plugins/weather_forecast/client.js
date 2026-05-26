// weather_forecast — 5 days as columns of (label, icon, high, low, rain).
// Flat layout; today's column gets a subtle highlight.

const WMO = {
  0:  ["sun",             "Clear"],
  1:  ["sun",             "Clear"],
  2:  ["cloud-sun",       "Partly"],
  3:  ["cloud",           "Overcast"],
  45: ["cloud-fog",       "Fog"],
  48: ["cloud-fog",       "Fog"],
  51: ["cloud-rain",      "Drizzle"],
  53: ["cloud-rain",      "Drizzle"],
  55: ["cloud-rain",      "Drizzle"],
  56: ["snowflake",       "Freezing"],
  57: ["snowflake",       "Freezing"],
  61: ["cloud-rain",      "Rain"],
  63: ["cloud-rain",      "Rain"],
  65: ["cloud-rain",      "Heavy rain"],
  66: ["snowflake",       "Freezing"],
  67: ["snowflake",       "Freezing"],
  71: ["snowflake",       "Snow"],
  73: ["snowflake",       "Snow"],
  75: ["snowflake",       "Heavy snow"],
  77: ["snowflake",       "Snow"],
  80: ["cloud-rain",      "Showers"],
  81: ["cloud-rain",      "Showers"],
  82: ["cloud-rain",      "Showers"],
  85: ["snowflake",       "Snow"],
  86: ["snowflake",       "Heavy snow"],
  95: ["cloud-lightning", "Storm"],
  96: ["cloud-lightning", "Storm"],
  99: ["cloud-lightning", "Storm"],
};

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function describe(code) { return WMO[code] || ["cloud", "—"]; }

function conditionTone(code) {
  if (code === 0 || code === 1) return "warn";              // clear
  if (code === 2) return "accent";                          // partly cloudy
  if (code === 3 || code === 45 || code === 48) return "muted"; // overcast / fog
  if ((code >= 51 && code <= 67) || (code >= 80 && code <= 82)) return "accent"; // rain
  if ((code >= 71 && code <= 77) || code === 85 || code === 86) return "fgSoft"; // snow
  if (code >= 95) return "danger";                          // storm
  return "accent";
}

function fmtTemp(v) { return v == null ? "—" : Math.round(v) + "°"; }
function dayLabel(idx, position) {
  if (position === 0) return "Today";
  if (position === 1) return "Tomorrow";
  return idx >= 0 && idx < 7 ? DAY_NAMES[idx] : "—";
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function renderError(msg) {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/plugins/weather_forecast/client.css">
    <div class="root error">
      <i class="ph ph-warning-circle" aria-hidden="true"></i>
      <span>${escapeHtml(msg)}</span>
    </div>
  `;
}

function dayColumn(day, position, showRain) {
  const [icon, label] = describe(day.code);
  const tone = conditionTone(day.code);
  const isToday = position === 0;
  const rainPct = day.rain == null ? null : Math.round(day.rain);
  const rainWet = rainPct != null && rainPct >= 30;
  return `
    <div class="day${isToday ? " is-today" : ""}">
      <div class="day-name">${escapeHtml(dayLabel(day.weekday, position))}</div>
      <i class="ph-fill ph-fill-${icon} day-icon" style="color: var(--theme-${tone})" aria-hidden="true"></i>
      <div class="day-cond">${escapeHtml(label)}</div>
      <div class="day-high">${fmtTemp(day.high)}</div>
      <div class="day-low">${fmtTemp(day.low)}</div>
      ${showRain ? `
      <div class="day-rain${rainWet ? " is-wet" : ""}">
        <i class="ph-fill ph-fill-drop" aria-hidden="true"></i>
        <span>${rainPct == null ? "—" : rainPct + "%"}</span>
      </div>` : ""}
    </div>
  `;
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = renderError(data.error);
    return;
  }
  const days = Array.isArray(data.days) ? data.days : [];
  if (!days.length) {
    shadow.innerHTML = renderError("no forecast data");
    return;
  }
  const size = ctx.cell.size;
  const showHeader = size !== "sm";
  const showRain = size === "md" || size === "lg";

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/fill/style.css">
    <link rel="stylesheet" href="/plugins/weather_forecast/client.css">
    <div class="root size-${size}">
      ${showHeader ? `
      <header class="head">
        <span class="head-title">
          <i class="ph-fill ph-fill-calendar-dots" style="color: var(--theme-accent)" aria-hidden="true"></i>
          <span>5-day forecast</span>
        </span>
        ${data.label ? `<span class="head-place">${escapeHtml(data.label)}</span>` : ""}
      </header>` : ""}
      <section class="days">
        ${days.map((d, i) => dayColumn(d, i, showRain)).join("")}
      </section>
    </div>
  `;
}
