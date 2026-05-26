// weather_forecast — Bauhaus 5-day card. Inverted header strip, then
// 5 colour-blocked day columns. Today's column takes the accent block;
// remaining days alternate between surface and surface2 so the row
// reads as a clear primary-coloured rhythm without any drawn lines.

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
function fmtTemp(v) { return v == null ? "—" : Math.round(v) + "°"; }
function dayLabel(idx, position) {
  if (position === 0) return "Today";
  if (position === 1) return "Tom";
  return idx >= 0 && idx < 7 ? DAY_NAMES[idx] : "—";
}
function nowTime() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
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

// Day-block colour: today gets the accent, the rest alternate between
// surface and surface2 so the row has a Bauhaus colour-block rhythm.
function dayTone(position) {
  if (position === 0) return "accent";
  return position % 2 === 1 ? "surface" : "surface2";
}

function dayBlock(day, position, showRain) {
  const [icon, label] = describe(day.code);
  const tone = dayTone(position);
  const rainPct = day.rain == null ? null : Math.round(day.rain);
  const rainWet = rainPct != null && rainPct >= 30;
  return `
    <article class="wf-day wf-day--${tone}${position === 0 ? " is-today" : ""}">
      <div class="wf-day-name">${escapeHtml(dayLabel(day.weekday, position))}</div>
      <i class="ph-bold ph-bold-${icon} wf-day-icon" aria-hidden="true"></i>
      <div class="wf-day-cond">${escapeHtml(label)}</div>
      <div class="wf-day-temps">
        <span class="wf-day-high">${fmtTemp(day.high)}</span>
        <span class="wf-day-low">${fmtTemp(day.low)}</span>
      </div>
      ${showRain && rainPct != null ? `
      <div class="wf-day-rain${rainWet ? " is-wet" : ""}">
        <i class="ph-bold ph-bold-drop" aria-hidden="true"></i>
        <span>${rainPct}%</span>
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
  const showRain = size === "md" || size === "lg";

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/weather_forecast/client.css">
    <div class="root size-${size}">
      <header class="wf-bar">
        <span class="wf-mark" aria-hidden="true"></span>
        <span class="wf-title">${data.label ? escapeHtml(data.label) + " · " : ""}5-day forecast</span>
        <span class="wf-time">${nowTime()}</span>
      </header>
      <section class="wf-days">
        ${days.map((d, i) => dayBlock(d, i, showRain)).join("")}
      </section>
    </div>
  `;
}
