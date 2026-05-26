// weather_now — flat, dense, theme-aware. The cell is the card; no
// nested panels. Adapts xs/sm/md/lg via a .size-* class on .root.

// WMO weather code → Phosphor icon name + readable label.
// Day/night variants where Phosphor offers them.
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

// Theme token to colour the condition icon by — leans on the existing
// palette so it works on every user theme without hard-coded hex.
function conditionTone(code, isDay) {
  if (code === 0 || code === 1) return isDay ? "warn" : "accent";   // clear sun / moon
  if (code === 2) return "accent";                                   // partly cloudy
  if (code === 3 || code === 45 || code === 48) return "muted";      // overcast / fog
  if ((code >= 51 && code <= 67) || (code >= 80 && code <= 82)) return "accent"; // rain
  if ((code >= 71 && code <= 77) || code === 85 || code === 86) return "fgSoft"; // snow
  if (code >= 95) return "danger";                                   // thunderstorm
  return "accent";
}

function uvTone(uv) {
  if (uv == null) return "fgSoft";
  if (uv < 3) return "ok";
  if (uv < 8) return "warn";
  return "danger";
}

function uvBand(uv) {
  if (uv == null) return "—";
  if (uv < 3) return "Low";
  if (uv < 6) return "Mod";
  if (uv < 8) return "High";
  if (uv < 11) return "V High";
  return "Extreme";
}

function windCompass(deg) {
  if (deg == null) return "";
  const dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  return dirs[Math.round(deg / 45) % 8];
}

function fmtTemp(v) { return v == null ? "—" : Math.round(v) + "°"; }
function fmtTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  } catch (_e) { return "—"; }
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function statColumn(icon, label, value, sub, tone) {
  const toneStyle = tone ? ` style="color: var(--theme-${tone})"` : "";
  return `
    <div class="stat">
      <i class="ph-fill ph-fill-${icon} stat-icon"${toneStyle} aria-hidden="true"></i>
      <div class="stat-body">
        <div class="stat-label">${escapeHtml(label)}</div>
        <div class="stat-value"${toneStyle}>${escapeHtml(value)}</div>
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
  const isDay = data.is_day !== false;
  const { icon, label } = describe(data.code, isDay);
  const condTone = conditionTone(data.code, isDay);
  const windVal = data.wind != null ? `${Math.round(data.wind)}` : "—";
  const windSub = data.wind != null
    ? `${windUnit}${data.wind_dir != null ? " " + windCompass(data.wind_dir) : ""}`
    : "";

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/fill/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
    <link rel="stylesheet" href="/plugins/weather_now/client.css">
    <div class="root size-${size}">
      <header class="head">
        <span class="place">
          <i class="ph ph-map-pin" aria-hidden="true"></i>
          <span>${escapeHtml(data.label || "—")}</span>
        </span>
      </header>
      <section class="now">
        <i class="ph-fill ph-fill-${icon} now-icon" style="color: var(--theme-${condTone})" aria-hidden="true"></i>
        <div class="now-body">
          <div class="now-temp">${fmtTemp(data.temp)}</div>
          <div class="now-cond">${escapeHtml(label)}</div>
          <div class="now-range">
            ${data.today_max != null ? `
              <span><i class="ph-fill ph-fill-arrow-up" style="color: var(--theme-warn)" aria-hidden="true"></i>${fmtTemp(data.today_max)}</span>
              <span><i class="ph-fill ph-fill-arrow-down" style="color: var(--theme-fgSoft)" aria-hidden="true"></i>${fmtTemp(data.today_min)}</span>
            ` : ""}
            ${data.rain_chance != null ? `<span class="rain"><i class="ph-fill ph-fill-drop" style="color: var(--theme-accent)" aria-hidden="true"></i>${Math.round(data.rain_chance)}%</span>` : ""}
          </div>
        </div>
      </section>
      <section class="stats">
        ${statColumn("thermometer-simple", "Feels", fmtTemp(data.feels), null, "warn")}
        ${statColumn("drop-half", "Humidity",
                     data.humidity != null ? `${Math.round(data.humidity)}%` : "—", null, "accent")}
        ${statColumn("wind", "Wind", windVal, windSub, "fgSoft")}
        ${statColumn("sun-dim", "UV",
                     data.uv != null ? data.uv.toFixed(1) : "—",
                     uvBand(data.uv), uvTone(data.uv))}
      </section>
      <section class="sun">
        <span class="sun-row">
          <i class="ph-duotone ph-duotone-sun-horizon" aria-hidden="true"></i>
          <span class="sun-time">${fmtTime(data.sunrise)}</span>
        </span>
        <span class="sun-row">
          <i class="ph-duotone ph-duotone-moon-stars" aria-hidden="true"></i>
          <span class="sun-time">${fmtTime(data.sunset)}</span>
        </span>
      </section>
    </div>
  `;
}
