// sky_moon — Bauhaus moon-phase card. SVG draws the actual lit
// portion of the disc (not just an icon), backed by a small bit of
// astronomy math computed server-side.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function timeStr(iso) {
  if (!iso) return "—";
  const m = String(iso).match(/T(\d{2}):(\d{2})/);
  return m ? `${m[1]}:${m[2]}` : "—";
}

function shortDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  // "Tue 3 Jun" — local time.
  const weekday = d.toLocaleDateString([], { weekday: "short" });
  const day = d.getDate();
  const month = d.toLocaleDateString([], { month: "short" });
  return `${weekday} ${day} ${month}`;
}

// SVG path for the lit portion of the moon. Centered at (0, 0), radius r.
//
// Convention: in the northern hemisphere the lit hemisphere is on the
// RIGHT when waxing; in the southern hemisphere it's on the LEFT. The
// terminator (line between lit and dark) is half of an ellipse whose
// horizontal semi-axis collapses to zero at first/last quarter and
// reaches full-radius at new and full. The sweep flag on the inner arc
// flips at quarter so the terminator stays on the correct side of the
// disc as phase progresses.
function moonLitPath(fraction, r, southern) {
  const phaseAngle = fraction * 2 * Math.PI;
  const k = Math.cos(phaseAngle); // 1 at new, 0 at quarter, -1 at full
  const rx = Math.abs(r * k);
  const waxing = fraction < 0.5;
  const litRight = waxing !== southern;
  const outerSweep = litRight ? 1 : 0;
  const terminatorSweep = k > 0 ? outerSweep : 1 - outerSweep;
  return `M 0 ${-r}
          A ${r} ${r} 0 0 ${outerSweep} 0 ${r}
          A ${rx.toFixed(2)} ${r} 0 0 ${terminatorSweep} 0 ${-r}
          Z`;
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = `
      <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
      <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
      <link rel="stylesheet" href="/plugins/sky_moon/client.css">
      <div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>
    `;
    return;
  }
  const size = ctx.cell.size;
  // Hemisphere convention: server passes a negative latitude when the
  // viewer is below the equator, in which case the lit hemisphere
  // mirrors. (Defaults to northern view when lat is unknown.)
  const southern = (data.lat ?? 0) < 0;

  const litPath = moonLitPath(data.fraction || 0, 50, southern);

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/sky_moon/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="mn-title">${escapeHtml(data.label || "Moon")}</span>
        <i class="ph-bold ph-moon-stars wb-bar-icon"></i>
      </header>

      <section class="mn-hero">
        <div class="mn-disc" aria-hidden="true">
          <svg viewBox="-55 -55 110 110" preserveAspectRatio="xMidYMid meet" class="mn-svg">
            <circle cx="0" cy="0" r="50" class="mn-shadow" />
            <path d="${litPath}" class="mn-lit" />
            <circle cx="0" cy="0" r="50" class="mn-rim" />
          </svg>
        </div>
        <div class="mn-text">
          <div class="mn-name">${escapeHtml(data.phase_name || "—")}</div>
          <div class="mn-illum">
            <span class="mn-illum-v">${data.illumination != null ? Math.round(data.illumination) : "—"}</span>
            <span class="mn-illum-u">%</span>
            <span class="mn-illum-lbl">lit</span>
          </div>
          <div class="mn-age">
            <i class="ph-bold ph-clock-countdown"></i>
            <span>Day ${data.age_days ?? "—"} of cycle</span>
          </div>
        </div>
      </section>

      <section class="mn-stats">
        <div class="mn-stat mn-stat--accent">
          <i class="ph-bold ph-circle-dashed mn-stat-icon"></i>
          <span class="mn-stat-label">Next new</span>
          <span class="mn-stat-value">${escapeHtml(shortDate(data.next_new))}</span>
        </div>
        <div class="mn-stat mn-stat--surface">
          <i class="ph-bold ph-circle-half mn-stat-icon"></i>
          <span class="mn-stat-label">First qtr</span>
          <span class="mn-stat-value">${escapeHtml(shortDate(data.next_first_quarter))}</span>
        </div>
        <div class="mn-stat mn-stat--accent2">
          <i class="ph-bold ph-circle mn-stat-icon"></i>
          <span class="mn-stat-label">Next full</span>
          <span class="mn-stat-value">${escapeHtml(shortDate(data.next_full))}</span>
        </div>
        <div class="mn-stat mn-stat--accent3">
          <i class="ph-bold ph-arrow-up mn-stat-icon"></i>
          <span class="mn-stat-label">Moonrise</span>
          <span class="mn-stat-value">${escapeHtml(timeStr(data.moonrise))}</span>
        </div>
      </section>
    </div>
  `;
}
