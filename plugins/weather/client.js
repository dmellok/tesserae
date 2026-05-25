// Weather — rich hero + stat-cards + (lg only) hourly chart + daily forecast.
// Layout adapts to cell size via container queries; everything below the hero
// hides on small cells, so the same widget works at xs/sm/md/lg.

// WMO weather codes → Phosphor icon name + readable label.
// https://open-meteo.com/en/docs#weathervariables
const WMO = {
  0:  ["ph-sun",            "Clear"],
  1:  ["ph-sun",            "Mostly clear"],
  2:  ["ph-cloud-sun",      "Partly cloudy"],
  3:  ["ph-cloud",          "Overcast"],
  45: ["ph-cloud-fog",      "Fog"],
  48: ["ph-cloud-fog",      "Rime fog"],
  51: ["ph-cloud-rain",     "Light drizzle"],
  53: ["ph-cloud-rain",     "Drizzle"],
  55: ["ph-cloud-rain",     "Heavy drizzle"],
  56: ["ph-cloud-rain",     "Freezing drizzle"],
  57: ["ph-cloud-rain",     "Freezing drizzle"],
  61: ["ph-cloud-rain",     "Light rain"],
  63: ["ph-cloud-rain",     "Rain"],
  65: ["ph-cloud-rain",     "Heavy rain"],
  66: ["ph-snowflake",      "Freezing rain"],
  67: ["ph-snowflake",      "Freezing rain"],
  71: ["ph-snowflake",      "Light snow"],
  73: ["ph-snowflake",      "Snow"],
  75: ["ph-snowflake",      "Heavy snow"],
  77: ["ph-snowflake",      "Snow grains"],
  80: ["ph-cloud-rain",     "Rain showers"],
  81: ["ph-cloud-rain",     "Rain showers"],
  82: ["ph-cloud-rain",     "Heavy showers"],
  85: ["ph-snowflake",      "Snow showers"],
  86: ["ph-snowflake",      "Heavy snow"],
  95: ["ph-cloud-lightning","Thunderstorm"],
  96: ["ph-cloud-lightning","Storm + hail"],
  99: ["ph-cloud-lightning","Storm + hail"],
};

function describe(code) {
  return WMO[code] || ["ph-cloud", "—"];
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function formatTime(now) {
  return now.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false });
}

function fmtTemp(t, unit) {
  if (t == null || isNaN(t)) return "—";
  return Math.round(t) + "°";
}

// Resolve a CSS custom-property value against the cell so we can paint the
// chart in the live theme's colours instead of a baked-in palette.
function readVar(host, name, fallback) {
  try {
    const v = getComputedStyle(host.host).getPropertyValue(name).trim();
    return v || fallback;
  } catch {
    return fallback;
  }
}

// Chart.js line+area for hourly temperature. The hour labels + weather icons
// are rendered as a separate flex row UNDER the canvas (see hourlyHtml below)
// — Chart.js's built-in x-axis hid them inconsistently at smaller heights and
// couldn't host icons. We disable the native x-axis here.
function mountHourlyChart(host, canvas, points) {
  if (!window.Chart || !canvas || !points.length) return null;
  const accent = readVar(host, "--theme-accent", "#d97757");
  const fg = readVar(host, "--theme-fg", "#1a1612");
  const temps = points.map((p) => p.temp ?? null);
  return new window.Chart(canvas.getContext("2d"), {
    type: "line",
    data: {
      labels: points.map((p) => p.label),
      datasets: [
        {
          data: temps,
          borderColor: accent,
          backgroundColor: accent + "30", // ~19% alpha hex
          tension: 0.4,
          fill: true,
          pointBackgroundColor: accent,
          pointBorderColor: accent,
          pointRadius: 4,
          pointHoverRadius: 4,
          borderWidth: 2.5,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      layout: { padding: { top: 24, bottom: 4, left: 8, right: 8 } },
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: {
        x: { display: false, grid: { display: false }, border: { display: false } },
        y: { display: false, grace: "20%" },
      },
    },
    plugins: [
      // Tiny inline plugin: draw the temperature value above each point so
      // we don't need a separate value row.
      {
        id: "pointLabels",
        afterDatasetsDraw(chart) {
          const { ctx } = chart;
          const meta = chart.getDatasetMeta(0);
          ctx.save();
          ctx.fillStyle = fg;
          ctx.font = "700 13px ui-monospace, monospace";
          ctx.textAlign = "center";
          ctx.textBaseline = "bottom";
          meta.data.forEach((point, i) => {
            const value = chart.data.datasets[0].data[i];
            if (value == null) return;
            ctx.fillText(Math.round(value) + "°", point.x, point.y - 8);
          });
          ctx.restore();
        },
      },
    ],
  });
}

// Daily-forecast min→max gradient bar. Maps each day's range onto the global
// (week-wide) min→max so cool days look cooler and hot days look warmer.
function buildDailyBar(d, weekMin, weekMax) {
  const span = Math.max(1, weekMax - weekMin);
  const start = ((d.min - weekMin) / span) * 100;
  const end = ((d.max - weekMin) / span) * 100;
  return { start, end };
}

export default function render(host, ctx) {
  const data = ctx.data || {};
  const opts = (ctx.cell && ctx.cell.options) || {};
  const place = opts.label || "Local";

  if (data.error) {
    host.innerHTML = `
      <link rel="stylesheet" href="/static/icons/phosphor.css">
      <link rel="stylesheet" href="/static/style/widget-base.css">
      <link rel="stylesheet" href="/plugins/weather/client.css">
      <div class="wx wx--error">
        <i class="ph ph-warning-circle"></i>
        <div class="msg">${escapeHtml(data.error)}</div>
      </div>
    `;
    host.host.dataset.rendered = "true";
    return;
  }

  const cur = data.current || {};
  const today = data.today || {};
  const hourly = data.hourly || [];
  const daily = data.daily || [];
  const [iconClass, conditionLabel] = describe(cur.weather_code);
  const tempUnit = data.units === "imperial" ? "°F" : "°C";
  const speedUnit = data.units === "imperial" ? "mph" : "km/h";
  const now = formatTime(new Date());

  const stats = [
    {
      icon: "ph-umbrella",
      label: "Rain today",
      value: today.precipitation_probability_max != null
        ? Math.round(today.precipitation_probability_max) + "%"
        : "—",
    },
    {
      icon: "ph-wind",
      label: "Wind",
      value: cur.wind_speed_10m != null
        ? Math.round(cur.wind_speed_10m) + " " + speedUnit
        : "—",
      smallUnit: speedUnit,
    },
    {
      icon: "ph-sun-dim",
      label: "UV index",
      value: today.uv_index_max != null ? Math.round(today.uv_index_max) : "—",
    },
    {
      icon: "ph-drop",
      label: "Humidity",
      value: cur.relative_humidity_2m != null
        ? Math.round(cur.relative_humidity_2m) + "%"
        : "—",
    },
    {
      icon: "ph-sun-horizon",
      label: "Sunrise",
      value: today.sunrise || "—",
    },
    {
      icon: "ph-moon",
      label: "Sunset",
      value: today.sunset || "—",
    },
  ];

  const statCards = stats.map((s) => `
    <div class="stat">
      <i class="ph ${s.icon}"></i>
      <div class="stat-text">
        <div class="stat-label">${escapeHtml(s.label)}</div>
        <div class="stat-value">${escapeHtml(String(s.value))}</div>
      </div>
    </div>
  `).join("");

  // Hourly block — Chart.js canvas + hour-tick row beneath. Mounted after
  // innerHTML is set below.
  const showHourly = hourly.length >= 3;
  const hourTicks = hourly.map((h) => {
    const [icon] = describe(h.code);
    return `
      <div class="hour-tick">
        <i class="ph ${icon}"></i>
        <span class="hour-tick-time">${escapeHtml(h.label)}</span>
      </div>
    `;
  }).join("");
  const hourlyHtml = showHourly
    ? `<div class="hourly-block">
         <div class="hourly-wrap"><canvas class="hourly-canvas"></canvas></div>
         <div class="hourly-ticks">${hourTicks}</div>
       </div>`
    : "";

  // 4-day daily forecast — gradient bars with min/max temps. Hidden via CSS
   // on shorter cells; the compact `.day-row` below takes its place.
  let dailyHtml = "";
  if (daily.length >= 2) {
    const allMaxes = daily.map((d) => d.max).filter((v) => v != null);
    const allMins = daily.map((d) => d.min).filter((v) => v != null);
    const wMin = Math.min(...allMins);
    const wMax = Math.max(...allMaxes);
    dailyHtml = `
      <div class="daily">
        ${daily.map((d) => {
          const { start, end } = buildDailyBar(d, wMin, wMax);
          const [ic] = describe(d.code);
          return `
            <div class="day">
              <i class="ph ${ic}"></i>
              <span class="day-label">${escapeHtml(d.label)}</span>
              <div class="day-track">
                <div class="day-bar" style="left:${start}%; right:${100 - end}%"></div>
              </div>
              <span class="day-max">${escapeHtml(fmtTemp(d.max))}</span>
              <span class="day-min">${escapeHtml(fmtTemp(d.min))}</span>
            </div>
          `;
        }).join("")}
      </div>
    `;
  }

  // Compact today/tomorrow cards. Hidden when the full daily forecast is
  // visible; shown when the cell is too short to fit the gradient bars.
  let dayRowHtml = "";
  if (daily.length >= 2) {
    const today = daily[0];
    const tomorrow = daily[1];
    const [todayIcon] = describe(today.code);
    const [tomIcon] = describe(tomorrow.code);
    dayRowHtml = `
      <div class="day-row">
        <div class="day-card">
          <i class="ph ${todayIcon}"></i>
          <div class="day-card-text">
            <div class="day-card-label">TODAY</div>
            <div class="day-card-temp">
              <span class="day-card-hi">${escapeHtml(fmtTemp(today.max))}</span>
              <span class="day-card-sep">/</span>
              <span class="day-card-lo">${escapeHtml(fmtTemp(today.min))}</span>
            </div>
          </div>
        </div>
        <div class="day-card">
          <i class="ph ${tomIcon}"></i>
          <div class="day-card-text">
            <div class="day-card-label">TOMORROW</div>
            <div class="day-card-temp">
              <span class="day-card-hi">${escapeHtml(fmtTemp(tomorrow.max))}</span>
              <span class="day-card-sep">/</span>
              <span class="day-card-lo">${escapeHtml(fmtTemp(tomorrow.min))}</span>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  host.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor.css">
    <link rel="stylesheet" href="/static/style/widget-base.css">
    <link rel="stylesheet" href="/plugins/weather/client.css">
    <div class="wx">
      <div class="head">
        <i class="ph ${iconClass} head-icon"></i>
        <span class="head-title">WEATHER</span>
        <span class="head-place">${escapeHtml(place)}</span>
        <span class="head-time">${escapeHtml(now)}</span>
      </div>
      <div class="hero">
        <i class="ph ${iconClass} hero-icon"></i>
        <div class="hero-temp">
          ${cur.temperature_2m != null ? Math.round(cur.temperature_2m) : "—"}<span class="unit">${tempUnit}</span>
        </div>
      </div>
      <div class="hero-meta">
        <div class="condition">${escapeHtml(conditionLabel)}</div>
        ${cur.apparent_temperature != null
          ? `<div class="feels">FEELS LIKE ${Math.round(cur.apparent_temperature)}${tempUnit}</div>`
          : ""}
      </div>
      <div class="stats">${statCards}</div>
      ${hourlyHtml}
      ${dayRowHtml}
      ${dailyHtml}
    </div>
  `;

  // Mount the chart now that the canvas is in the DOM. Skipping when
  // Chart.js isn't loaded (e.g. older compose.html) leaves a blank slot.
  if (showHourly) {
    const canvas = host.querySelector(".hourly-canvas");
    // Dispose any prior instance bound to this canvas (re-render path).
    if (host.__inkyChart && host.__inkyChart.destroy) {
      host.__inkyChart.destroy();
    }
    host.__inkyChart = mountHourlyChart(host, canvas, hourly);
  }

  host.host.dataset.rendered = "true";
}

export function cleanup(host) {
  if (host.__inkyChart && host.__inkyChart.destroy) {
    host.__inkyChart.destroy();
    host.__inkyChart = null;
  }
}
