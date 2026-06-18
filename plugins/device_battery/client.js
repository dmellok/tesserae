// device_battery, Spectra battery-strip archetype.
//
// One row per device: name, percent number, a horizontal fill bar
// coloured by tone (critical <= 10, low <= 30, ok above). When the
// history store has enough samples, a small "in N days" prediction
// trails the row.
//
// Size tiers via container queries on .w-body:
//   xs/sm  big number + tiny bar, no name.
//   md     name + number + bar.
//   lg     above + prediction + last-seen tag.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function tone(pct) {
  if (pct <= 10) return "critical";
  if (pct <= 30) return "low";
  return "ok";
}

function fmtAgo(seconds) {
  if (!Number.isFinite(seconds)) return "";
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function fmtDays(d) {
  if (!Number.isFinite(d) || d <= 0) return "";
  if (d < 1) return "today";
  if (d < 2) return "tomorrow";
  if (d < 14) return `${Math.round(d)} days`;
  if (d < 60) return `${Math.round(d / 7)} weeks`;
  return `${Math.round(d / 30)} months`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = '<link rel="stylesheet" href="/static/style/spectra-widgets.css">';

  const devices = Array.isArray(data.devices) ? data.devices : [];

  if (devices.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="device_battery">
        <div class="w-title">
          <i class="ph-bold ph-battery-charging" style="color:var(--accent-2)"></i>
          <h3>Device Batteries</h3>
        </div>
        <div class="w-body"><p class="u-muted">No battery-reporting devices registered yet.</p></div>
      </div>`;
    return;
  }

  const rows = devices.map((d) => {
    const t = tone(d.pct);
    const accent =
      t === "critical" ? "var(--accent-1)"
        : t === "low" ? "var(--accent-2)"
        : "var(--accent-5)";
    const prediction =
      Number.isFinite(d.days_to_empty)
        ? `<span class="db-predict">in ${escapeHtml(fmtDays(d.days_to_empty))}</span>`
        : "";
    const ago = d.seconds_ago != null
      ? `<span class="db-ago">${escapeHtml(fmtAgo(d.seconds_ago))}</span>`
      : "";
    return `
      <div class="db-row" data-tone="${t}">
        <div class="db-head">
          <span class="db-name">${escapeHtml(d.name || d.device_id || "")}</span>
          <span class="db-pct" style="color:${accent}">${escapeHtml(String(d.pct))}<small>%</small></span>
        </div>
        <div class="db-bar">
          <div class="db-bar-fill" style="width:${d.pct}%;background:${accent}"></div>
        </div>
        <div class="db-meta">
          ${prediction}
          ${ago}
        </div>
      </div>`;
  }).join("");

  const layout = `
    .w-body { container-type: inline-size; }
    .db-list {
      display: flex;
      flex-direction: column;
      gap: var(--space-2);
    }
    .db-row {
      display: flex;
      flex-direction: column;
      gap: 3px;
    }
    .db-head {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: var(--space-2);
    }
    .db-name {
      font-weight: var(--fw-bold);
      font-size: var(--fs-h5);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      min-width: 0;
    }
    .db-pct {
      font-weight: var(--fw-black);
      font-size: var(--fs-h4);
      font-variant-numeric: tabular-nums;
    }
    .db-pct small {
      font-size: 0.55em;
      font-weight: var(--fw-bold);
      color: var(--text-muted);
      margin-left: 2px;
    }
    .db-bar {
      height: 6px;
      background: color-mix(in oklab, var(--text-muted) 18%, transparent);
      border-radius: var(--radius-1);
      overflow: hidden;
    }
    .db-bar-fill {
      height: 100%;
      border-radius: var(--radius-1);
    }
    .db-meta {
      display: flex;
      gap: var(--space-2);
      font-size: var(--fs-caption);
      color: var(--text-muted);
      letter-spacing: var(--ls-label);
      text-transform: uppercase;
    }
    .db-meta:empty { display: none; }
    .db-predict { font-weight: var(--fw-bold); }

    @container (max-width: 280px) {
      .db-name { display: none; }
      .db-pct { font-size: var(--fs-h3); }
      .db-meta { display: none; }
    }
    @container (min-width: 281px) and (max-width: 460px) {
      .db-meta { display: none; }
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="device_battery">
      <div class="w-title">
        <i class="ph-bold ph-battery-charging" style="color:var(--accent-2)"></i>
        <h3>Device Batteries</h3>
        ${data.total_devices > devices.length ? `<span class="w-title-meta">${devices.length} of ${data.total_devices}</span>` : ""}
      </div>
      <div class="w-body">
        <div class="db-list">${rows}</div>
      </div>
    </div>`;
}
