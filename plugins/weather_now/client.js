// weather_now — current conditions hero + stat grid + sun times.
// Adapts xs/sm/md/lg via a size class on .root; CSS does the rest.

// WMO weather code → Phosphor icon name + readable label.
// https://open-meteo.com/en/docs#weathervariables
// We pick day/night variants where Phosphor offers them; client.js gets
// is_day from the server.
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

function uvBand(uv) {
  if (uv == null) return "—";
  if (uv < 3) return "Low";
  if (uv < 6) return "Moderate";
  if (uv < 8) return "High";
  if (uv < 11) return "Very High";
  return "Extreme";
}

function windCompass(deg) {
  if (deg == null) return "";
  const dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  return dirs[Math.round(deg / 45) % 8];
}

function fmtTemp(v) {
  return v == null ? "—" : Math.round(v) + "°";
}

function fmtTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  } catch (_e) {
    return "—";
  }
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function statCard(icon, label, value, sub) {
  return `
    <div class="stat">
      <i class="ph ph-${icon} stat-icon" aria-hidden="true"></i>
      <div class="stat-body">
        <div class="stat-label">${escapeHtml(label)}</div>
        <div class="stat-value">${escapeHtml(value)}</div>
        ${sub ? `<div class="stat-sub">${escapeHtml(sub)}</div>` : ""}
      </div>
    </div>
  `;
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
  const { icon, label } = describe(data.code, data.is_day !== false);
  const wind = data.wind != null ? `${Math.round(data.wind)}` : "—";
  const windSub = data.wind != null
    ? `${windUnit}${data.wind_dir != null ? " · " + windCompass(data.wind_dir) : ""}`
    : "";

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/fill/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
    <link rel="stylesheet" href="/plugins/weather_now/client.css">
    <div class="root size-${size}">
      <section class="panel hero">
        <div class="hero-place">
          <i class="ph ph-map-pin" aria-hidden="true"></i>
          <span>${escapeHtml(data.label || "—")}</span>
        </div>
        <i class="ph-fill ph-fill-${icon} hero-icon" aria-hidden="true"></i>
        <div class="hero-temp">${fmtTemp(data.temp)}</div>
        <div class="hero-cond">${escapeHtml(label)}</div>
        ${data.today_max != null ? `
        <div class="hero-range">
          <span><i class="ph ph-arrow-up" aria-hidden="true"></i>${fmtTemp(data.today_max)}</span>
          <span><i class="ph ph-arrow-down" aria-hidden="true"></i>${fmtTemp(data.today_min)}</span>
          ${data.rain_chance != null ? `<span><i class="ph ph-drop" aria-hidden="true"></i>${Math.round(data.rain_chance)}%</span>` : ""}
        </div>` : ""}
      </section>
      <section class="stats" aria-label="Current stats">
        ${statCard("thermometer-simple", "Feels like", fmtTemp(data.feels))}
        ${statCard("drop-half", "Humidity",
                   data.humidity != null ? `${Math.round(data.humidity)}%` : "—")}
        ${statCard("wind", "Wind", wind, windSub)}
        ${statCard("sun-dim", "UV",
                   data.uv != null ? data.uv.toFixed(1) : "—",
                   uvBand(data.uv))}
      </section>
      <section class="panel sun" aria-label="Sun times">
        <div class="sun-row">
          <i class="ph-duotone ph-duotone-sun-horizon" aria-hidden="true"></i>
          <div class="sun-meta">
            <div class="sun-label">Sunrise</div>
            <div class="sun-time">${fmtTime(data.sunrise)}</div>
          </div>
        </div>
        <div class="sun-row">
          <i class="ph-duotone ph-duotone-moon-stars" aria-hidden="true"></i>
          <div class="sun-meta">
            <div class="sun-label">Sunset</div>
            <div class="sun-time">${fmtTime(data.sunset)}</div>
          </div>
        </div>
      </section>
    </div>
  `;
}
