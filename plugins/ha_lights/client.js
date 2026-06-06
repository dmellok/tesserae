// ha_lights, Spectra list archetype. One row per light with a
// filled-bulb icon when on (accent-2 ochre) and an empty bulb
// otherwise (text-muted). Each row carries a brightness mini-bar
// (track + filled portion ∝ brightness%) and a tiny colour swatch
// dot showing either the light's RGB colour (hs_color mode) or its
// colour-temperature mapped to a warm/cool tone (color_temp_kelvin
// mode). Title-bar icon tints by the overall "any on" state.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// HSL → CSS for the hue/saturation case. Lightness fixed at 55% for
// a friendly mid-tone the eye can read against either light or dark
// surfaces.
function hsColorCss([hue, sat]) {
  const h = Math.max(0, Math.min(360, hue));
  const s = Math.max(0, Math.min(100, sat));
  return `hsl(${h.toFixed(0)}, ${s.toFixed(0)}%, 55%)`;
}

// Kelvin → CSS RGB. Tanner Helland's approximation, distilled for the
// 1800-12000K band that covers every domestic bulb. Returns hex.
function kelvinCss(k) {
  const T = Math.max(1000, Math.min(12000, Number(k))) / 100;
  let r, g, b;
  if (T <= 66) {
    r = 255;
    g = clamp255(99.4708025861 * Math.log(T) - 161.1195681661);
    b = T <= 19 ? 0 : clamp255(138.5177312231 * Math.log(T - 10) - 305.0447927307);
  } else {
    r = clamp255(329.698727446 * Math.pow(T - 60, -0.1332047592));
    g = clamp255(288.1221695283 * Math.pow(T - 60, -0.0755148492));
    b = 255;
  }
  return `rgb(${Math.round(r)}, ${Math.round(g)}, ${Math.round(b)})`;
}

function clamp255(v) {
  if (!Number.isFinite(v)) return 0;
  return Math.max(0, Math.min(255, v));
}

// Per-light swatch, either an RGB dot (hs mode) or a kelvin dot
// (color_temp mode). Falls back to an ochre dot for lights that
// don't expose either attribute (most ON/OFF-only smart switches).
function swatchFor(light) {
  if (Array.isArray(light.hs_color)) return hsColorCss(light.hs_color);
  if (Number.isFinite(light.color_temp_kelvin)) return kelvinCss(light.color_temp_kelvin);
  return "var(--accent-2)";
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_lights">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Lights</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const lights = Array.isArray(data.lights) ? data.lights : [];
  const place = data.place || "Lights";
  const onCount = data.on_count ?? 0;
  const total = data.total ?? lights.length;

  if (data.empty || lights.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_lights">
        <div class="w-title"><i class="ph-bold ph-lightbulb"></i><h3>${escapeHtml(place)}</h3></div>
        <div class="w-body"><p class="u-muted">No lights selected.</p></div>
      </div>`;
    return;
  }

  const rows = lights.map((l, i) => {
    const isOn = !!l.on;
    const accent = isOn ? "var(--accent-2)" : "var(--text-muted)";
    const ph = isOn ? "ph-lightbulb-filament" : "ph-lightbulb";
    const swatchColor = swatchFor(l);
    const brightnessPct = Number.isFinite(l.brightness_pct) ? l.brightness_pct : null;

    // Brightness bar, track + filled portion. Empty bar for off
    // lights so the alignment of the % text stays consistent across
    // rows. Filled portion uses the light's colour swatch so a
    // warm-white bulb's bar reads warm and a cool-white bulb's reads
    // cool, small visual cue but reads as "this is the colour"
    // without needing a separate swatch dot to take space.
    const brightnessBar = isOn ? `
      <span class="bri-wrap" title="${brightnessPct != null ? brightnessPct : "-"}%">
        <span class="bri-track"></span>
        <span class="bri-fill" style="width:${brightnessPct != null ? brightnessPct : 100}%;background:${swatchColor}"></span>
      </span>` : `<span class="bri-wrap is-off"><span class="bri-track"></span></span>`;
    const valueText = isOn
      ? (brightnessPct != null ? `${brightnessPct}%` : "ON")
      : "off";
    const swatchDot = isOn && (Array.isArray(l.hs_color) || Number.isFinite(l.color_temp_kelvin))
      ? `<span class="bri-swatch" style="background:${swatchColor}" title="${Number.isFinite(l.color_temp_kelvin) ? `${l.color_temp_kelvin}K` : "RGB"}"></span>`
      : "";

    return `
      <div class="light-row ${i % 2 ? "is-zebra" : ""}${isOn ? " is-on" : ""}">
        <div class="list-lead light-row-lead">
          <i class="ph-bold ${ph}" style="color:${accent}"></i>
          <span class="list-title">${escapeHtml(l.name)}</span>
        </div>
        <div class="light-meta">
          ${swatchDot}
          ${brightnessBar}
          <span class="bri-value" style="color:${accent}">${valueText}</span>
        </div>
      </div>`;
  }).join("");

  const layout = `
    .light-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--space-2);
      padding: var(--space-2) var(--space-3);
      border-radius: var(--radius-1);
      min-width: 0;
    }
    .light-row.is-zebra {
      background: color-mix(in oklab, var(--text-primary) 3%, transparent);
    }
    /* On-lights get a subtle ochre wash so the list reads as
       "which ones are lit" before you scan brightness numbers. */
    .light-row.is-on {
      background: color-mix(in oklab, var(--accent-2) 5%, transparent);
    }
    .light-row.is-on.is-zebra {
      background: color-mix(in oklab, var(--accent-2) 7%, transparent);
    }
    .light-row-lead {
      flex: 1 1 auto;
      min-width: 0;
      gap: var(--space-2);
    }
    .light-row-lead .list-title {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .light-meta {
      display: flex;
      align-items: center;
      gap: var(--space-2);
      flex: 0 0 auto;
    }
    .bri-swatch {
      display: inline-block;
      width: 0.85em;
      height: 0.85em;
      border-radius: 50%;
      flex: 0 0 auto;
      box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.12);
    }
    .bri-wrap {
      position: relative;
      width: 4em;
      height: 0.55em;
      border-radius: 999px;
      overflow: hidden;
      flex: 0 0 auto;
    }
    .bri-wrap .bri-track {
      position: absolute;
      inset: 0;
      background: color-mix(in oklab, var(--text-primary) 8%, transparent);
      border-radius: inherit;
    }
    .bri-wrap .bri-fill {
      position: absolute;
      left: 0;
      top: 0;
      bottom: 0;
      border-radius: inherit;
    }
    .bri-value {
      font-weight: var(--fw-bold);
      font-variant-numeric: tabular-nums;
      min-width: 2.4em;
      text-align: right;
    }
    @container (max-width: 280px) {
      .bri-swatch { display: none; }
      .bri-wrap { width: 2.5em; }
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="ha_lights">
      <div class="w-title">
        <i class="ph-bold ph-lightbulb" style="color:${onCount > 0 ? "var(--accent-2)" : "var(--accent-3)"}"></i>
        <h3>${escapeHtml(place)}</h3>
        <span class="w-title-meta">${onCount}/${total} ON</span>
      </div>
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
