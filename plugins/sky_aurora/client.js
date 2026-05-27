// sky_aurora — Kp + visibility for the user's location.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function kpClass(kp) {
  if (kp >= 7) return "kp-extreme";
  if (kp >= 5) return "kp-storm";
  if (kp >= 4) return "kp-active";
  return "kp-quiet";
}

function sparkBars(forecast) {
  if (!forecast || !forecast.length) return "";
  return forecast.slice(0, 24).map((f) => {
    const h = Math.max(8, Math.min(100, (f.kp / 9) * 100));
    return `<span class="wb-bar ${kpClass(f.kp)}" style="height:${h}%" title="${escapeHtml(f.time)}: Kp ${f.kp}"></span>`;
  }).join("");
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = `
      <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
      <link rel="stylesheet" href="/plugins/sky_aurora/client.css">
      <div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>
    `;
    return;
  }
  const size = ctx.cell.size;
  const kpNow = data.current_kp;
  const kpMax = data.max_kp_3d;
  const visible = data.visible_now;
  const visibleSoon = data.visible_soon;

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
    <link rel="stylesheet" href="/plugins/sky_aurora/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="ar-title">Aurora · Kp index</span>
        <i class="ph-bold ph-mountains wb-bar-icon"></i>
      </header>
      <section class="ar-hero ${kpClass(kpNow)}">
        <div class="ar-now">
          <span class="ar-now-lbl">Now</span>
          <span class="ar-now-v">${kpNow.toFixed(1)}</span>
        </div>
        <div class="ar-visible">
          ${visible
            ? `<i class="ph-bold ph-check-circle"></i><span>Likely visible</span>`
            : visibleSoon
            ? `<i class="ph-bold ph-arrow-up-right"></i><span>Possible later</span>`
            : `<i class="ph-bold ph-x-circle"></i><span>Not visible from lat ${Math.abs(data.lat).toFixed(1)}°</span>`}
        </div>
        <div class="ar-band">${escapeHtml(data.band_label)}</div>
      </section>
      <section class="ar-spark">
        <div class="ar-spark-head">
          <span>3-day forecast</span>
          <span class="ar-spark-peak">peak Kp ${kpMax.toFixed(1)}</span>
        </div>
        <div class="ar-spark-bars">${sparkBars(data.forecast)}</div>
      </section>
    </div>
  `;
}
