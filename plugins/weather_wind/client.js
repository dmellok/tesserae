// weather_wind — Spectra stat archetype, wind speed as the hero number.
//
// Hero = current wind speed (display) + unit, caption shows the Beaufort
// state and direction (e.g. "Breezy · NW"); gust delta tucked alongside
// when meaningfully higher than steady speed.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtNum(v) {
  if (v == null) return "—";
  const n = Number(v);
  if (Number.isNaN(n)) return "—";
  return n >= 100 ? String(Math.round(n)) : String(Math.round(n * 10) / 10);
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="weather_wind">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Wind</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const label = data.place || data.label || "";
  const speed = fmtNum(data.speed);
  const unit = data.unit || "km/h";
  const dir = data.dir || "";
  const beaufortLabel = data.beaufortLabel || "";
  const gust = Number(data.gust);
  const speedN = Number(data.speed);
  const gustDelta = !Number.isNaN(gust) && !Number.isNaN(speedN) && gust > speedN
    ? `<span class="stat-delta" style="color:var(--accent-1)"><i class="ph-bold ph-arrow-fat-up"></i>${fmtNum(gust)} gust</span>`
    : "";

  const captionBits = [beaufortLabel, dir].filter(Boolean).map(escapeHtml).join(" · ");

  // Rotate the compass icon by the wind bearing so its tail points at
  // where the wind is coming from. Phosphor's ph-navigation-arrow points
  // up by default; bearing 0 = N, so the rotation is just the bearing.
  const bearing = Number(data.bearing);
  const rot = Number.isNaN(bearing) ? 0 : bearing;

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="weather_wind">
      <div class="w-title">
        <i class="ph-bold ph-wind"></i>
        <h3>${escapeHtml(label || "Wind")}</h3>
        <span class="w-title-meta">${escapeHtml(data.time || "")}</span>
      </div>
      <div class="w-body stat-body">
        <div class="stat-value">
          <i class="ph-bold ph-navigation-arrow" style="font-size:.55em;color:var(--accent-4);transform:rotate(${rot}deg)"></i>
          ${escapeHtml(speed)}<span class="unit">${escapeHtml(unit)}</span>
        </div>
        <div class="stat-caption u-row">
          ${captionBits ? `<span>${captionBits}</span>` : ""}
          ${gustDelta}
        </div>
      </div>
    </div>`;
}
