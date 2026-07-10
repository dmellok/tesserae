// tesserae_status: dashboard status strip renderer.
//
// Implements the converged design spec (design_handoff_status_widget/
// README.md, round 5): left identity (leading icon + dashboard name)
// paired with a right-hand row of ambient stat chips. Two placements
// (bar / block), three chip modes (icon-text / icon-only / text-only).
//
// Auto-contrast: the widget owns its own colour scheme, driven by the
// freeform ``panelBg`` option. Foreground (text, icons, rules) flips
// between ink (#1B1A16) and paper (#FCFBF7) based on background
// luminance; the update accent flips between two reds. This keeps the
// strip legible on any picked colour without a light/dark toggle.
//
// E-ink safe: no gradients, no CSS transitions, all rules >= 2 px
// (outer 3 px, inner + badge ring 2 px). Update signal is a positional
// badge dot on the icon or explicit accent-red text, never hue alone.
//
// Update chips (app version, panel firmware) render only when an
// update is pending. The app-version chip's update indicator fires
// after a fetch against api.tesserae.ink/version/latest (opt-in via
// ``check_for_updates``); firmware chip counts come from the app-
// level firmware_check cache (opt-in in Settings -> System).

const INK = "#1B1A16";
const PAPER = "#FCFBF7";
const RED_LIGHT_BG = "#C24F2C";
const RED_DARK_BG = "#E0663F";
const LUM_THRESHOLD = 0.42;

export default function render(shadow, ctx) {
  const data = (ctx && ctx.data) || {};
  const state = {
    latestVersion: null,
    latestUrl: null,
    updateAvailable: false,
  };
  paint(shadow, data, state);
  wireClock(shadow, data, state);
  wireUpdateCheck(shadow, data, state);
}

function paint(shadow, data, state) {
  const bg = normaliseHex(data.panelBg) || INK;
  const isLight = luminance(bg) > LUM_THRESHOLD;
  const fg = isLight ? INK : PAPER;
  const red = isLight ? RED_LIGHT_BG : RED_DARK_BG;
  const mode = data.mode === "block" ? "block" : "bar";
  const chipMode = normaliseChipMode(data.chipMode);
  const chips = buildChips(data, state);
  // The <link> pulls Phosphor bold (font-face + .ph-bold rules) into
  // the shadow root; class selectors don't pierce the shadow boundary
  // so without it every <i class="ph-bold ph-..."> would render as a
  // bare square. Matches the pattern the weather widgets use.
  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/style/spectra-widgets.css">
    ${styles(bg, fg, red, mode)}
    <div class="frame" data-mode="${mode}" data-chipmode="${chipMode}">
      ${identityHtml(data, chipMode)}
      <span class="rule" aria-hidden="true"></span>
      <div class="chips" role="list">
        ${chips.map((c) => chipHtml(c, chipMode)).join("")}
      </div>
    </div>
  `;
}

function normaliseChipMode(value) {
  const v = String(value || "icon-text").toLowerCase();
  return v === "icon-only" || v === "text-only" ? v : "icon-text";
}

function identityHtml(data, chipMode) {
  const name = String(data.dashboardName || data.page_name || "Dashboard").trim();
  const iconEnabled = data.leadingIcon !== false && chipMode !== "text-only";
  // Inherit the dashboard's page-level icon (Settings → dashboard icon
  // picker) when available. ``page_icon`` arrives without the ``ph-``
  // prefix (the page-store strips it on write), so prepend it back
  // when composing the class. Fall back to the neutral ``squares-four``
  // when the page has no icon override.
  const pageIconSlug = String(data.page_icon || "").trim();
  const iconClass = pageIconSlug ? `ph-${pageIconSlug}` : "ph-squares-four";
  const iconHtml = iconEnabled
    ? `<i class="ph-bold ${iconClass} leading-icon" aria-hidden="true"></i>`
    : "";
  return `
    <div class="identity">
      ${iconHtml}
      <span class="name">${escapeHtml(name)}</span>
    </div>
  `;
}

function buildChips(data, state) {
  const chips = [];
  // Always-on ambient stats (in order): time, environment, battery,
  // wifi, broker. Temperature and humidity share one compact cluster
  // so a sensor-equipped panel does not consume two full chip gaps.
  if (data.show_time) {
    chips.push({
      key: "time",
      icon: "ph-clock",
      value: formatTime(new Date(), data.time_format || "24h"),
      live: "time",
    });
  }
  const environment = environmentMetrics(data);
  if (environment.length > 0) {
    chips.push({
      key: "environment",
      metrics: environment,
    });
  }
  if (data.show_battery && Number.isFinite(data.battery_pct)) {
    const pct = Math.max(0, Math.min(100, Math.round(data.battery_pct)));
    chips.push({
      key: "battery",
      icon: batteryIcon(pct),
      value: `${pct}%`,
    });
  }
  if (data.show_wifi && data.wifi_label) {
    chips.push({
      key: "wifi",
      icon: wifiIcon(data.wifi_label),
      value: String(data.wifi_label),
    });
  }
  if (data.show_broker && data.broker_available) {
    chips.push({
      key: "broker",
      icon: "ph-plugs-connected",
      value: String(data.broker_label || "HA").trim(),
    });
  }
  // Update chips: rendered ONLY when an update is available. Both chips
  // lead with what's new (server version / "Firmware") and carry a
  // ph-download-simple icon + badge dot so the reader gets one
  // consistent "there is a download to install" signal across the two.
  if (data.check_for_updates && data.version && state.updateAvailable && state.latestVersion) {
    chips.push({
      key: "version",
      icon: "ph-download-simple",
      value: `v${String(state.latestVersion).replace(/^v/, "")}`,
      updateSub: "available",
      isUpdate: true,
    });
  }
  if (data.show_firmware_updates && Number.isFinite(data.firmware_updates) && data.firmware_updates > 0) {
    const n = data.firmware_updates;
    chips.push({
      key: "firmware",
      icon: "ph-download-simple",
      value: "Firmware",
      updateSub: `${n} available`,
      isUpdate: true,
    });
  }
  return chips;
}

function environmentMetrics(data) {
  const metrics = [];
  const imperial = String(data.units || "metric").toLowerCase() === "imperial";
  if (data.show_temperature && Number.isFinite(data.temperature_c)) {
    const temperature = imperial
      ? (data.temperature_c * 9) / 5 + 32
      : data.temperature_c;
    const unit = imperial ? "F" : "C";
    metrics.push({
      key: "temperature",
      icon: "ph-thermometer-simple",
      value: `${Math.round(temperature)}°${unit}`,
      label: `Temperature ${Math.round(temperature)} degrees ${unit}`,
    });
  }
  if (data.show_humidity && Number.isFinite(data.humidity_pct)) {
    const humidity = Math.max(0, Math.min(100, Math.round(data.humidity_pct)));
    metrics.push({
      key: "humidity",
      icon: "ph-drop",
      value: `${humidity}%`,
      textOnlyValue: `${humidity}% RH`,
      label: `Humidity ${humidity} percent`,
    });
  }
  return metrics;
}

function chipHtml(chip, chipMode) {
  if (Array.isArray(chip.metrics)) {
    return environmentChipHtml(chip, chipMode);
  }
  const showIcon = chipMode !== "text-only";
  const showText = chipMode !== "icon-only";
  const parts = [];
  if (showIcon) {
    const dot = chip.isUpdate ? `<span class="badge" aria-hidden="true"></span>` : "";
    parts.push(`
      <span class="chip-icon">
        <i class="ph-bold ${chip.icon}" aria-hidden="true"></i>
        ${dot}
      </span>
    `);
  }
  if (showText) {
    const sub = chip.isUpdate && chip.updateSub
      ? `<span class="chip-sub">${escapeHtml(chip.updateSub)}</span>`
      : "";
    parts.push(`
      <span class="chip-text">
        <span class="chip-value">${escapeHtml(chip.value)}</span>
        ${sub}
      </span>
    `);
  }
  const dataAttr = chip.isUpdate ? " data-update=\"true\"" : "";
  return `
    <span class="chip" role="listitem" data-kind="${chip.key}"${dataAttr}>
      ${parts.join("")}
    </span>
  `;
}

function environmentChipHtml(chip, chipMode) {
  const showIcon = chipMode !== "text-only";
  const showText = chipMode !== "icon-only";
  const metrics = chip.metrics || [];
  const label = metrics.map((metric) => metric.label).join(", ");
  const body = metrics.map((metric, index) => {
    const icon = showIcon
      ? `<i class="ph-bold ${metric.icon}" aria-hidden="true"></i>`
      : "";
    const value = showText
      ? `<span class="chip-value">${escapeHtml(
        chipMode === "text-only" && metric.textOnlyValue
          ? metric.textOnlyValue
          : metric.value,
      )}</span>`
      : "";
    const separator = chipMode === "text-only" && index > 0
      ? `<span class="environment-separator" aria-hidden="true">·</span>`
      : "";
    return `
      ${separator}
      <span class="environment-metric" data-metric="${metric.key}">
        ${icon}
        ${value}
      </span>
    `;
  }).join("");
  return `
    <span class="chip environment-chip" role="listitem" data-kind="${chip.key}"
          aria-label="${escapeHtml(label)}">
      ${body}
    </span>
  `;
}

function batteryIcon(pct) {
  if (pct <= 10) return "ph-battery-empty";
  if (pct <= 33) return "ph-battery-low";
  if (pct <= 66) return "ph-battery-medium";
  if (pct <= 90) return "ph-battery-high";
  return "ph-battery-full";
}

function wifiIcon(label) {
  switch (String(label).toLowerCase()) {
    case "excellent":
    case "strong":
      return "ph-wifi-high";
    case "good":
      return "ph-wifi-medium";
    case "fair":
      return "ph-wifi-low";
    default:
      return "ph-wifi-none";
  }
}

function formatTime(d, format) {
  const h24 = d.getHours();
  const m = String(d.getMinutes()).padStart(2, "0");
  if (String(format).toLowerCase() === "12h") {
    const h12 = h24 % 12 === 0 ? 12 : h24 % 12;
    const suffix = h24 < 12 ? "am" : "pm";
    return `${h12}:${m}${suffix}`;
  }
  return `${String(h24).padStart(2, "0")}:${m}`;
}

function wireClock(shadow, data, state) {
  if (typeof setInterval !== "function") return;
  const el = shadow.querySelector('[data-kind="time"] .chip-value');
  if (!el || !data.show_time) return;
  setInterval(() => {
    el.textContent = formatTime(new Date(), data.time_format || "24h");
  }, 30 * 1000);
}

// One hour: matches other polling intervals in Tesserae and keeps the
// widget from hammering api.tesserae.ink on every push (composition
// re-render). Cached negative results (running-is-latest) are honoured
// too so we don't refetch when there is nothing new.
const UPDATE_CHECK_TTL_MS = 60 * 60 * 1000;
const UPDATE_CHECK_CACHE_PREFIX = "tesserae_status:vcheck:";

function updateCheckCacheKey(channel, current) {
  return `${UPDATE_CHECK_CACHE_PREFIX}${channel}:${current}`;
}

function readUpdateCheckCache(channel, current) {
  try {
    if (typeof localStorage !== "object" || localStorage === null) return null;
    const raw = localStorage.getItem(updateCheckCacheKey(channel, current));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed.ts !== "number") return null;
    if (Date.now() - parsed.ts > UPDATE_CHECK_TTL_MS) return null;
    return parsed;
  } catch {
    return null;
  }
}

function writeUpdateCheckCache(channel, current, latestVersion, latestUrl) {
  try {
    if (typeof localStorage !== "object" || localStorage === null) return;
    localStorage.setItem(
      updateCheckCacheKey(channel, current),
      JSON.stringify({ ts: Date.now(), latestVersion, latestUrl }),
    );
  } catch {
    // Quota exceeded / private-mode storage disabled; skip silently.
  }
}

// Best-effort semver compare on the leading numeric triplet. Returns
// > 0 when a > b, < 0 when a < b, 0 when equal. Pre-release / build
// suffixes ("-edge.123") are stripped so an edge build compares as
// its release triplet — an edge build of X.Y.Z is treated as equal
// to X.Y.Z rather than "behind" it.
function versionCompare(a, b) {
  const parts = (v) =>
    String(v || "")
      .replace(/^v/, "")
      .split(/[.\-+]/)
      .slice(0, 3)
      .map((n) => parseInt(n, 10) || 0);
  const aa = parts(a);
  const bb = parts(b);
  for (let i = 0; i < 3; i++) {
    const diff = (aa[i] || 0) - (bb[i] || 0);
    if (diff !== 0) return diff;
  }
  return 0;
}

function applyUpdateResult(shadow, data, state, latestVersion, latestUrl) {
  if (!latestVersion) return;
  const current = String(data.version).replace(/^v/, "");
  // Defensive: only surface the update chip when latest is STRICTLY
  // newer than the running build. Guards against api.tesserae.ink
  // reporting is_current: false when the caller is actually ahead of
  // the latest published release (edge / local builds, or a paused
  // release marker on the server side).
  if (versionCompare(latestVersion, current) <= 0) return;
  state.latestVersion = latestVersion;
  state.latestUrl = latestUrl || null;
  state.updateAvailable = true;
  paint(shadow, data, state);
  wireClock(shadow, data, state);
}

function wireUpdateCheck(shadow, data, state) {
  if (!data.check_for_updates || !data.version) return;
  const channel = "stable";
  const current = String(data.version).replace(/^v/, "");
  const cached = readUpdateCheckCache(channel, current);
  if (cached) {
    applyUpdateResult(shadow, data, state, cached.latestVersion, cached.latestUrl);
    return;
  }
  if (typeof fetch !== "function") return;
  const params = new URLSearchParams({ channel, current });
  if (data.install_scoped_id) params.set("install", data.install_scoped_id);
  const url = `https://api.tesserae.ink/version/latest?${params.toString()}`;
  const ctrl = typeof AbortController === "function" ? new AbortController() : null;
  const timeoutId = ctrl && typeof setTimeout === "function"
    ? setTimeout(() => ctrl.abort(), 5000)
    : null;
  fetch(url, ctrl ? { signal: ctrl.signal } : {})
    .then((r) => (r.ok ? r.json() : null))
    .then((body) => {
      if (timeoutId) clearTimeout(timeoutId);
      if (!body || !body.latest || !body.latest.version) return;
      const latestVersion = String(body.latest.version);
      const latestUrl = body.latest.url || null;
      writeUpdateCheckCache(channel, current, latestVersion, latestUrl);
      applyUpdateResult(shadow, data, state, latestVersion, latestUrl);
    })
    .catch(() => {
      if (timeoutId) clearTimeout(timeoutId);
    });
}

// -- utilities ---------------------------------------------------------

function normaliseHex(input) {
  const raw = String(input || "").trim();
  if (!raw) return null;
  const bare = raw.replace(/^#/, "");
  if (!/^([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(bare)) return null;
  if (bare.length === 3) {
    return `#${bare[0]}${bare[0]}${bare[1]}${bare[1]}${bare[2]}${bare[2]}`.toLowerCase();
  }
  return `#${bare.toLowerCase()}`;
}

function luminance(hex) {
  const h = hex.replace("#", "");
  const ch = (i) => parseInt(h.slice(i, i + 2), 16) / 255;
  const lin = (c) => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
  return 0.2126 * lin(ch(0)) + 0.7152 * lin(ch(2)) + 0.0722 * lin(ch(4));
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

// -- styles ------------------------------------------------------------
//
// Text and icons auto-size against the container's height (cqh units)
// so the strip stays legible whether it's a 48 px bar, a 120 px tall
// block, or the ~400 px cell the composer preview shows. The clamp()
// wrappers keep the extremes readable: never smaller than what a
// mid-DPR e-ink panel can legibly rasterise, never larger than the
// design values from the handoff.

function styles(bg, fg, red, mode) {
  return `
    <style>
      :host {
        display: block;
        width: 100%;
        height: 100%;
        color: ${fg};
        background: ${bg};
        font-family: "Space Grotesk", var(--font-family, system-ui, sans-serif);
        font-weight: 600;
        letter-spacing: 0;
        container-type: size;
      }
      .frame {
        width: 100%;
        height: 100%;
        box-sizing: border-box;
        display: flex;
        align-items: center;
        color: ${fg};
      }
      /* Bar mode. clamp height to 48 px minimum so it never collapses
         when a user drops it into a very short cell. */
      .frame[data-mode="bar"] {
        min-height: 48px;
        padding: 0 clamp(14px, 4cqh, 22px);
        border-bottom: 3px solid ${fg};
        gap: clamp(10px, 4cqh, 18px);
      }
      .frame[data-mode="block"] {
        padding: clamp(10px, 6cqh, 18px);
        border: 3px solid ${fg};
        flex-direction: column;
        align-items: stretch;
        gap: clamp(8px, 4cqh, 14px);
      }
      /* Identity ---------------------------------------------------- */
      .identity {
        display: inline-flex;
        align-items: center;
        gap: clamp(6px, 2cqh, 11px);
        flex: 0 1 auto;
        min-width: 0;
      }
      .frame[data-mode="bar"] .identity {
        max-width: 40%;
      }
      .leading-icon {
        font-size: clamp(16px, 45cqh, 32px);
        line-height: 1;
      }
      .frame[data-mode="block"] .leading-icon {
        font-size: clamp(18px, 12cqh, 32px);
      }
      .name {
        font-size: clamp(13px, 34cqh, 26px);
        font-weight: 700;
        letter-spacing: 0;
        line-height: 1.1;
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .frame[data-mode="block"] .name {
        font-size: clamp(14px, 9cqh, 22px);
      }
      /* Rule (block mode only) -------------------------------------- */
      .rule {
        display: none;
      }
      .frame[data-mode="block"] .rule {
        display: block;
        height: 0;
        border-top: 2px solid ${fg};
        margin: 0;
      }
      /* Chip row ---------------------------------------------------- */
      .chips {
        display: flex;
        align-items: center;
        flex: 1 1 auto;
        min-width: 0;
        overflow: hidden;
      }
      .frame[data-mode="bar"] .chips {
        justify-content: flex-end;
        gap: clamp(12px, 4.5cqh, 24px);
        flex-wrap: nowrap;
      }
      .frame[data-mode="block"] .chips {
        flex-wrap: wrap;
        gap: clamp(8px, 3cqh, 14px) clamp(12px, 4cqh, 20px);
      }
      /* Chip -------------------------------------------------------- */
      .chip {
        display: inline-flex;
        align-items: center;
        flex: 0 0 auto;
        gap: clamp(4px, 1.5cqh, 8px);
        white-space: nowrap;
        color: ${fg};
      }
      .chip-icon {
        position: relative;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        line-height: 1;
      }
      .frame[data-mode="bar"] .chip-icon i,
      .frame[data-mode="bar"] .environment-metric i {
        font-size: clamp(14px, 42cqh, 28px);
      }
      .frame[data-mode="block"] .chip-icon i,
      .frame[data-mode="block"] .environment-metric i {
        font-size: clamp(15px, 10cqh, 24px);
      }
      .chip-text {
        display: inline-flex;
        align-items: baseline;
        gap: clamp(3px, 1.5cqh, 8px);
        line-height: 1.1;
      }
      .frame[data-mode="bar"] .chip-value {
        font-size: clamp(12px, 32cqh, 22px);
      }
      .frame[data-mode="block"] .chip-value {
        font-size: clamp(12px, 8cqh, 18px);
      }
      .chip-value { font-weight: 600; }
      .environment-chip {
        gap: clamp(7px, 2.5cqh, 12px);
      }
      .environment-metric {
        display: inline-flex;
        align-items: center;
        gap: clamp(3px, 1.2cqh, 6px);
        white-space: nowrap;
      }
      .environment-separator {
        font-size: clamp(12px, 28cqh, 18px);
        line-height: 1;
      }
      .chip-sub {
        font-size: clamp(11px, 25cqh, 16px);
        font-weight: 700;
        letter-spacing: 0.2px;
        color: ${red};
      }
      /* Badge dot on the icon (update signal) ----------------------- */
      .badge {
        position: absolute;
        top: -2px;
        right: -3px;
        width: clamp(6px, 2cqh, 12px);
        height: clamp(6px, 2cqh, 12px);
        border-radius: 50%;
        background: ${red};
        box-shadow: 0 0 0 2px ${bg};
      }
      /* Icon-only mode collapses the icon+text gap. */
      .frame[data-chipmode="icon-only"] .chip { gap: 0; }
      .frame[data-chipmode="icon-only"] .environment-chip {
        gap: clamp(7px, 2.5cqh, 12px);
      }
      @container (max-width: 900px) {
        .frame[data-mode="bar"] {
          padding-inline: 12px;
          gap: 8px;
        }
        .frame[data-mode="bar"] .identity { max-width: 34%; }
        .frame[data-mode="bar"] .chips { gap: 8px; }
      }
    </style>
  `;
}
