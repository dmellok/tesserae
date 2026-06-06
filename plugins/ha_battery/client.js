// ha_battery — Spectra list archetype. One zebra row per battery,
// each leading with a device-type Phosphor glyph (phone / watch /
// keyboard / mouse / remote / vacuum / sensor / camera / tracker by
// name pattern, fallback ph-circuitry) so the row reads "which
// thing" at a glance. The level lives in a tiny SVG battery shape
// with a fill bar so you see the exact percentage as height, not
// just a step-quantized icon — fill colour tracks the severity
// (critical → terracotta, low → ochre, healthy → moss). Items
// crossing the low / critical threshold also wear a pill chip.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Device-type icon table. First match wins; matched against the
// entity's friendly name. Falls back to ph-circuitry for the generic
// "unknown wireless gadget" case. The list is intentionally broad
// because HA exposes everything as `device_class=battery` and the
// only signal we have to pick a glyph is the entity name.
const DEVICE_ICONS = [
  [/phone|iphone|android|pixel|galaxy/i, "ph-device-mobile"],
  [/watch|garmin|fitbit/i, "ph-watch"],
  [/airpods?|earbud|headphone|buds/i, "ph-headphones"],
  [/keyboard/i, "ph-keyboard"],
  [/mouse|trackpad/i, "ph-mouse"],
  [/controller|joystick|gamepad|joy-?con|switch pro/i, "ph-game-controller"],
  [/remote|wand/i, "ph-remote-control"],
  [/vacuum|roomba|roborock|robot/i, "ph-broom"],
  [/scooter|bike|car|tesla/i, "ph-car"],
  [/key|fob|smart\s*lock/i, "ph-key"],
  [/tile|airtag|tracker|tag\b/i, "ph-map-pin"],
  [/camera|cam\b/i, "ph-video-camera"],
  [/light|bulb|lamp|hue/i, "ph-lightbulb"],
  [/door|window|contact/i, "ph-door"],
  [/motion|presence|pir/i, "ph-person-simple-walk"],
  [/thermometer|temp|temperature|aqara/i, "ph-thermometer"],
  [/humid|moisture/i, "ph-drop"],
  [/leak|water/i, "ph-drop-half"],
  [/smoke|co2?\b/i, "ph-fire"],
  [/tablet|ipad/i, "ph-device-tablet"],
  [/laptop|book\b/i, "ph-laptop"],
  [/router|gateway|hub\b/i, "ph-router"],
  [/scale\b/i, "ph-scales"],
  [/pen\b|stylus/i, "ph-pen"],
  [/sensor/i, "ph-circuitry"],
];

function deviceIcon(name) {
  for (const [re, icon] of DEVICE_ICONS) {
    if (re.test(name || "")) return icon;
  }
  return "ph-circuitry";
}

function levelAccent(item) {
  if (item.critical) return "var(--accent-1)";
  if (item.low) return "var(--accent-2)";
  return "var(--accent-3)";
}

// SVG mini battery — rounded body + nub, fill bar inside. The fill
// always has at least a 1px sliver so 0% still reads as "empty
// battery shape" rather than "no battery at all". Used twice: as a
// row-level indicator and as the title-bar icon.
function batterySvg(level, color) {
  const lv = Math.max(0, Math.min(100, Number.isFinite(level) ? level : 0));
  const fillW = (lv / 100) * 18 + 0.5;
  return `
    <svg viewBox="0 0 28 14" aria-hidden="true" style="width:1.6em;height:0.85em;flex:0 0 auto">
      <rect x="0.5" y="0.5" width="22" height="13" rx="2" ry="2"
            fill="none" stroke="currentColor" stroke-width="1" opacity="0.7"/>
      <rect x="23" y="4" width="3" height="6" rx="0.8" ry="0.8"
            fill="currentColor" opacity="0.7"/>
      <rect x="2" y="2" width="${fillW.toFixed(2)}" height="10" rx="1.2" ry="1.2"
            fill="${color}"/>
    </svg>`;
}

function statusPill(item) {
  if (item.critical) {
    return `<span class="bat-pill bat-critical">
      <i class="ph-bold ph-warning" style="font-size:.9em"></i>CRITICAL
    </span>`;
  }
  if (item.low) {
    return `<span class="bat-pill bat-low">
      <i class="ph-bold ph-warning-circle" style="font-size:.9em"></i>LOW
    </span>`;
  }
  return "";
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_battery">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Batteries</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const items = Array.isArray(data.items) ? data.items : [];
  const summary = data.summary || {};
  const title = data.label || "Batteries";

  let meta = "";
  if (summary.critical > 0) {
    meta = `<span class="w-title-meta" style="color:var(--accent-1)">${summary.critical} CRITICAL</span>`;
  } else if (summary.low > 0) {
    meta = `<span class="w-title-meta" style="color:var(--accent-2)">${summary.low} LOW</span>`;
  } else if (summary.shown != null && summary.count != null) {
    meta = `<span class="w-title-meta">${summary.shown}/${summary.count}</span>`;
  }

  const rows = items.map((it, i) => {
    const accent = levelAccent(it);
    const devPh = deviceIcon(it.name);
    const lvl = it.level == null ? "—" : `${it.level}%`;
    const batSvg = it.level == null ? "" : batterySvg(it.level, accent);
    return `
      <div class="bat-row ${i % 2 ? "is-zebra" : ""}">
        <div class="list-lead bat-row-lead">
          <i class="ph-bold ${devPh}" style="color:var(--text-secondary)"></i>
          <span class="list-title bat-name">${escapeHtml(it.name)}</span>
          ${statusPill(it)}
        </div>
        <div class="bat-meta" style="color:${accent}">
          <span class="bat-svg-wrap" style="color:${accent}">${batSvg}</span>
          <span class="bat-pct">${escapeHtml(lvl)}</span>
        </div>
      </div>`;
  }).join("");

  // Title-bar icon swaps to a critical/low filled battery if any
  // item triggers; otherwise it's a healthy moss-tinted battery to
  // signal "nothing to worry about".
  const titleColor = summary.critical > 0
    ? "var(--accent-1)"
    : summary.low > 0
      ? "var(--accent-2)"
      : "var(--accent-3)";

  const layout = `
    .bat-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--space-2);
      padding: var(--space-2) var(--space-3);
      border-radius: var(--radius-1);
      min-width: 0;
    }
    .bat-row.is-zebra {
      background: color-mix(in oklab, var(--text-primary) 3%, transparent);
    }
    .bat-row-lead {
      flex: 1 1 auto;
      min-width: 0;
      gap: var(--space-2);
    }
    .bat-name {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .bat-meta {
      display: flex;
      align-items: center;
      gap: var(--space-1);
      font-variant-numeric: tabular-nums;
      font-weight: var(--fw-bold);
      flex: 0 0 auto;
    }
    .bat-svg-wrap {
      display: inline-flex;
      align-items: center;
    }
    .bat-pct {
      min-width: 2.6em;
      text-align: right;
    }
    .bat-pill {
      display: inline-flex;
      align-items: center;
      gap: 3px;
      padding: 1px var(--space-1);
      border-radius: 999px;
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      letter-spacing: var(--ls-label);
      flex: 0 0 auto;
    }
    .bat-pill.bat-critical {
      color: var(--accent-1);
      background: color-mix(in oklab, var(--accent-1) 14%, var(--surface));
    }
    .bat-pill.bat-low {
      color: var(--accent-2);
      background: color-mix(in oklab, var(--accent-2) 14%, var(--surface));
    }
    @container (max-width: 320px) {
      .bat-pill { display: none; }
      .bat-svg-wrap { display: none; }
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="ha_battery">
      <div class="w-title">
        <i class="ph-bold ph-battery-medium" style="color:${titleColor}"></i>
        <h3>${escapeHtml(title)}</h3>
        ${meta}
      </div>
      <div class="w-body list-body">${rows || '<p class="u-muted">No batteries.</p>'}</div>
    </div>`;
}
