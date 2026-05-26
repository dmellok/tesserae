// weather_forecast — 5 day-cards in a row. Adaptive at sm/md/lg.

const WMO = {
  0:  ["sun",            "Clear"],
  1:  ["sun",            "Mostly clear"],
  2:  ["cloud-sun",      "Partly cloudy"],
  3:  ["cloud",          "Overcast"],
  45: ["cloud-fog",      "Fog"],
  48: ["cloud-fog",      "Rime fog"],
  51: ["cloud-rain",     "Drizzle"],
  53: ["cloud-rain",     "Drizzle"],
  55: ["cloud-rain",     "Drizzle"],
  56: ["snowflake",      "Freezing"],
  57: ["snowflake",      "Freezing"],
  61: ["cloud-rain",     "Light rain"],
  63: ["cloud-rain",     "Rain"],
  65: ["cloud-rain",     "Heavy rain"],
  66: ["snowflake",      "Freezing"],
  67: ["snowflake",      "Freezing"],
  71: ["snowflake",      "Light snow"],
  73: ["snowflake",      "Snow"],
  75: ["snowflake",      "Heavy snow"],
  77: ["snowflake",      "Snow"],
  80: ["cloud-rain",     "Showers"],
  81: ["cloud-rain",     "Showers"],
  82: ["cloud-rain",     "Showers"],
  85: ["snowflake",      "Snow"],
  86: ["snowflake",      "Heavy snow"],
  95: ["cloud-lightning","Storm"],
  96: ["cloud-lightning","Storm + hail"],
  99: ["cloud-lightning","Storm + hail"],
};

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function describe(code) { return WMO[code] || ["cloud", "—"]; }
function fmtTemp(v) { return v == null ? "—" : Math.round(v) + "°"; }
function dayName(idx, position) {
  if (position === 0) return "Today";
  if (position === 1) return "Tom";
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

function dayCard(day, position, showRain) {
  const [icon, label] = describe(day.code);
  const isToday = position === 0;
  return `
    <article class="day${isToday ? " is-today" : ""}">
      <div class="day-name">${escapeHtml(dayName(day.weekday, position))}</div>
      <i class="ph-fill ph-fill-${icon} day-icon" aria-hidden="true"></i>
      <div class="day-cond">${escapeHtml(label)}</div>
      <div class="day-temps">
        <span class="t-high"><i class="ph ph-arrow-up" aria-hidden="true"></i>${fmtTemp(day.high)}</span>
        <span class="t-low"><i class="ph ph-arrow-down" aria-hidden="true"></i>${fmtTemp(day.low)}</span>
      </div>
      ${showRain ? `
      <div class="day-rain ${day.rain && day.rain > 30 ? "is-wet" : ""}">
        <i class="ph ph-drop" aria-hidden="true"></i>
        <span>${day.rain == null ? "—" : Math.round(day.rain) + "%"}</span>
      </div>` : ""}
    </article>
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
        <div class="head-title">
          <i class="ph ph-calendar-dots" aria-hidden="true"></i>
          <span>5-day forecast</span>
        </div>
        ${data.label ? `<div class="head-place"><i class="ph ph-map-pin" aria-hidden="true"></i>${escapeHtml(data.label)}</div>` : ""}
      </header>` : ""}
      <section class="days">
        ${days.map((d, i) => dayCard(d, i, showRain)).join("")}
      </section>
    </div>
  `;
}
