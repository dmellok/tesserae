// weather_now — Bauhaus weather card. Black header strip with accent
// mark + place + time, big bold temp + condition icon on a paper
// surface, then a four-up primary-coloured stat row (humidity / wind /
// UV / rain) and a sunrise/sunset bar at md/lg.
//
// Colour mapping leans on the semantic theme tokens as a Bauhaus
// primary triad: accent = blue, warn = yellow, danger = red. Surface
// is the neutral fourth block.

// WMO weather code -> Phosphor icon + readable label (day/night where
// Phosphor offers a variant).
const WMO = {
  0:  { day: "sun",             night: "moon",            label: "Clear" },
  1:  { day: "sun",             night: "moon",            label: "Mostly clear" },
  2:  { day: "cloud-sun",       night: "cloud-moon",      label: "Partly cloudy" },
  3:  { day: "cloud",           night: "cloud",           label: "Overcast" },
  45: { day: "cloud-fog",       night: "cloud-fog",       label: "Fog" },
  48: { day: "cloud-fog",       night: "cloud-fog",       label: "Rime fog" },
  51: { day: "cloud-rain",      night: "cloud-rain",      label: "Light drizzle" },
  53: { day: "cloud-rain",      night: "cloud-rain",      label: "Drizzle" },
  55: { day: "cloud-rain",      night: "cloud-rain",      label: "Heavy drizzle" },
  56: { day: "snowflake",       night: "snowflake",       label: "Freezing drizzle" },
  57: { day: "snowflake",       night: "snowflake",       label: "Freezing drizzle" },
  61: { day: "cloud-rain",      night: "cloud-rain",      label: "Light rain" },
  63: { day: "cloud-rain",      night: "cloud-rain",      label: "Rain" },
  65: { day: "cloud-rain",      night: "cloud-rain",      label: "Heavy rain" },
  66: { day: "snowflake",       night: "snowflake",       label: "Freezing rain" },
  67: { day: "snowflake",       night: "snowflake",       label: "Freezing rain" },
  71: { day: "snowflake",       night: "snowflake",       label: "Light snow" },
  73: { day: "snowflake",       night: "snowflake",       label: "Snow" },
  75: { day: "snowflake",       night: "snowflake",       label: "Heavy snow" },
  77: { day: "snowflake",       night: "snowflake",       label: "Snow grains" },
  80: { day: "cloud-rain",      night: "cloud-rain",      label: "Rain showers" },
  81: { day: "cloud-rain",      night: "cloud-rain",      label: "Rain showers" },
  82: { day: "cloud-rain",      night: "cloud-rain",      label: "Heavy showers" },
  85: { day: "snowflake",       night: "snowflake",       label: "Snow showers" },
  86: { day: "snowflake",       night: "snowflake",       label: "Heavy snow" },
  95: { day: "cloud-lightning", night: "cloud-lightning", label: "Thunderstorm" },
  96: { day: "cloud-lightning", night: "cloud-lightning", label: "Storm + hail" },
  99: { day: "cloud-lightning", night: "cloud-lightning", label: "Storm + hail" },
};

function describe(code, isDay) {
  const entry = WMO[code] || { day: "cloud", night: "cloud", label: "—" };
  return { icon: isDay ? entry.day : entry.night, label: entry.label };
}

function fmtTemp(v) { return v == null ? "—" : Math.round(v) + "°"; }
function fmtInt(v) { return v == null ? "—" : String(Math.round(v)); }
function fmtUv(v) { return v == null ? "—" : Number(v).toFixed(1); }
function fmtTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  } catch (_e) { return "—"; }
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
    <link rel="stylesheet" href="/plugins/weather_now/client.css">
    <div class="root error">
      <i class="ph ph-warning-circle" aria-hidden="true"></i>
      <span>${escapeHtml(msg)}</span>
    </div>
  `;
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = renderError(data.error);
    return;
  }

  const size = ctx.cell.size;
  const units = ctx.cell.options.units === "imperial" ? "imperial" : "metric";
  const windUnit = units === "imperial" ? "mph" : "km/h";
  const isDay = data.is_day !== false;
  const { icon, label } = describe(data.code, isDay);
  const showSun = size === "md" || size === "lg";
  const showRainTag = size !== "xs" && data.rain_chance != null;

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/weather_now/client.css">
    <div class="root size-${size}">
      <header class="wn-bar">
        <span class="wn-mark" aria-hidden="true"></span>
        <span class="wn-place">${escapeHtml(data.label || "—")}</span>
        <span class="wn-time">${nowTime()}</span>
      </header>
      <section class="wn-hero">
        <div class="wn-hero-text">
          <div class="wn-temp">${fmtTemp(data.temp)}</div>
          <div class="wn-cond">${escapeHtml(label)}</div>
          ${data.today_max != null || data.feels != null ? `
          <div class="wn-range">
            ${data.today_max != null ? `<span class="wn-range-high">High ${fmtTemp(data.today_max)}</span>` : ""}
            ${data.today_min != null ? `<span class="wn-range-low">Low ${fmtTemp(data.today_min)}</span>` : ""}
            ${data.feels != null ? `<span class="wn-range-feels">Feels ${fmtTemp(data.feels)}</span>` : ""}
          </div>` : ""}
        </div>
        <div class="wn-hero-icon" aria-hidden="true">
          <i class="ph-bold ph-${icon}"></i>
        </div>
        ${showRainTag ? `<span class="wn-rain-tag">${Math.round(data.rain_chance)}% Rain</span>` : ""}
      </section>
      <section class="wn-stats">
        <div class="wn-stat wn-stat--accent">
          <span class="wn-stat-label">Humidity</span>
          <span class="wn-stat-value">${fmtInt(data.humidity)}<small>%</small></span>
        </div>
        <div class="wn-stat wn-stat--surface">
          <span class="wn-stat-label">Wind</span>
          <span class="wn-stat-value">${fmtInt(data.wind)}<small>${windUnit}</small></span>
        </div>
        <div class="wn-stat wn-stat--warn">
          <span class="wn-stat-label">UV Index</span>
          <span class="wn-stat-value">${fmtUv(data.uv)}</span>
        </div>
        <div class="wn-stat wn-stat--danger">
          <span class="wn-stat-label">Rain</span>
          <span class="wn-stat-value">${fmtInt(data.rain_chance)}<small>%</small></span>
        </div>
      </section>
      ${showSun ? `
      <section class="wn-sun">
        <div class="wn-sun-cell wn-sun-cell--accent">
          <i class="ph ph-sun-horizon" aria-hidden="true"></i>
          <span class="wn-sun-label">Sunrise</span>
          <span class="wn-sun-time">${fmtTime(data.sunrise)}</span>
        </div>
        <div class="wn-sun-cell wn-sun-cell--inverse">
          <i class="ph ph-moon" aria-hidden="true"></i>
          <span class="wn-sun-label">Sunset</span>
          <span class="wn-sun-time">${fmtTime(data.sunset)}</span>
        </div>
      </section>` : ""}
    </div>
  `;
}
