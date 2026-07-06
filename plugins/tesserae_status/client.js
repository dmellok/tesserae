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
// update is pending, so the everyday state is just the ambient stats.
// The version chip's update indicator fires after a fetch against
// api.tesserae.ink/version/latest (opt-in via ``check_for_updates``).

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
  shadow.innerHTML = `
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
  const iconHtml = iconEnabled
    ? `<i class="ph-bold ph-squares-four leading-icon" aria-hidden="true"></i>`
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
  // Always-on ambient stats (in order): time, weather, battery, wifi, broker.
  if (data.show_time) {
    chips.push({
      key: "time",
      icon: "ph-clock",
      value: formatTime(new Date(), data.time_format || "24h"),
      live: "time",
    });
  }
  if (data.show_weather && String(data.weather_value || "").trim()) {
    chips.push({
      key: "weather",
      icon: normaliseIcon(data.weather_icon, "ph-sun"),
      value: String(data.weather_value).trim(),
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
  // Update chips: rendered only when an update is available.
  if (data.check_for_updates && data.version && state.updateAvailable && state.latestVersion) {
    chips.push({
      key: "version",
      icon: "ph-tag",
      value: `v${String(data.version).replace(/^v/, "")}`,
      updateSub: `-> ${updateShorthand(state.latestVersion, data.version)}`,
      isUpdate: true,
    });
  }
  if (data.show_firmware_updates && Number.isFinite(data.firmware_updates) && data.firmware_updates > 0) {
    chips.push({
      key: "firmware",
      icon: "ph-cpu",
      value: "FW",
      updateSub: `${data.firmware_updates} update${data.firmware_updates === 1 ? "" : "s"}`,
      isUpdate: true,
    });
  }
  // Two optional custom slots (user-provided icon + text).
  const slot1 = String(data.custom_slot_1_value || "").trim();
  if (slot1) {
    chips.push({
      key: "slot1",
      icon: normaliseIcon(data.custom_slot_1_icon, "ph-thermometer-simple"),
      value: slot1,
    });
  }
  const slot2 = String(data.custom_slot_2_value || "").trim();
  if (slot2) {
    chips.push({
      key: "slot2",
      icon: normaliseIcon(data.custom_slot_2_icon, "ph-calendar-dots"),
      value: slot2,
    });
  }
  return chips;
}

function chipHtml(chip, chipMode) {
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

const PH_ALLOWED = new Set([
  "ph-clock",
  "ph-sun",
  "ph-cloud",
  "ph-cloud-rain",
  "ph-cloud-snow",
  "ph-cloud-lightning",
  "ph-moon",
  "ph-moon-stars",
  "ph-thermometer-simple",
  "ph-trash",
  "ph-calendar-dots",
  "ph-house",
  "ph-drop",
  "ph-wind",
]);
function normaliseIcon(value, fallback) {
  const v = String(value || "").trim();
  return PH_ALLOWED.has(v) ? v : fallback;
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

function updateShorthand(latest, current) {
  const l = String(latest).replace(/^v/, "");
  const c = String(current).replace(/^v/, "");
  const lp = l.split(".");
  const cp = c.split(".");
  // If major/minor match, show only the patch delta (e.g. "-> .18").
  if (lp.length === 3 && cp.length === 3 && lp[0] === cp[0] && lp[1] === cp[1]) {
    return `.${lp[2]}`;
  }
  return `v${l}`;
}

function wireClock(shadow, data, state) {
  if (typeof setInterval !== "function") return;
  const el = shadow.querySelector('[data-kind="time"] .chip-value');
  if (!el || !data.show_time) return;
  setInterval(() => {
    el.textContent = formatTime(new Date(), data.time_format || "24h");
  }, 30 * 1000);
}

function wireUpdateCheck(shadow, data, state) {
  if (!data.check_for_updates || !data.version) return;
  if (typeof fetch !== "function") return;
  const params = new URLSearchParams({
    channel: "stable",
    current: String(data.version).replace(/^v/, ""),
  });
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
      if (!body || body.is_current !== false || !body.latest || !body.latest.version) return;
      state.latestVersion = String(body.latest.version);
      state.latestUrl = body.latest.url || null;
      state.updateAvailable = true;
      // Repaint so the version chip enters with the badge dot / accent
      // sub in the correct slot. Chip order stays stable because the
      // rebuild uses the same builder.
      paint(shadow, data, state);
      wireClock(shadow, data, state);
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
        container-type: inline-size;
      }
      .frame {
        width: 100%;
        height: 100%;
        box-sizing: border-box;
        display: flex;
        align-items: center;
        color: ${fg};
      }
      .frame[data-mode="bar"] {
        min-height: 48px;
        padding: 0 22px;
        border-bottom: 3px solid ${fg};
      }
      .frame[data-mode="block"] {
        padding: 15px 18px;
        border: 3px solid ${fg};
        flex-direction: column;
        align-items: stretch;
        gap: 11px;
      }
      /* Identity ---------------------------------------------------- */
      .identity {
        display: inline-flex;
        align-items: center;
        gap: 11px;
        flex: 0 0 auto;
      }
      .frame[data-mode="block"] .identity { gap: 10px; }
      .leading-icon { font-size: 21px; line-height: 1; }
      .name {
        font-size: 16px;
        font-weight: 700;
        letter-spacing: -0.2px;
        line-height: 1.1;
      }
      /* Rule separating identity from chips ------------------------- */
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
        overflow: hidden;
      }
      .frame[data-mode="bar"] .chips {
        justify-content: flex-end;
        gap: 20px;
        flex-wrap: nowrap;
      }
      .frame[data-mode="bar"][data-chipmode="icon-only"] .chips,
      .frame[data-mode="bar"][data-chipmode="text-only"] .chips {
        gap: 22px;
      }
      .frame[data-mode="block"] .chips {
        flex-wrap: wrap;
        gap: 12px 18px;
      }
      /* Chip -------------------------------------------------------- */
      .chip {
        display: inline-flex;
        align-items: center;
        gap: 8px;
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
      .frame[data-mode="bar"] .chip-icon i { font-size: 20px; }
      .frame[data-mode="block"] .chip-icon i { font-size: 20px; }
      .chip-text {
        display: inline-flex;
        align-items: baseline;
        gap: 6px;
        line-height: 1.1;
      }
      .frame[data-mode="bar"] .chip-value { font-size: 15px; }
      .frame[data-mode="block"] .chip-value { font-size: 14px; }
      .chip-value { font-weight: 600; }
      .chip-sub {
        font-size: 13px;
        font-weight: 700;
        letter-spacing: 0.2px;
        color: ${red};
      }
      /* Badge dot on the icon (update signal) ----------------------- */
      .badge {
        position: absolute;
        top: -2px;
        right: -3px;
        width: 9px;
        height: 9px;
        border-radius: 50%;
        background: ${red};
        box-shadow: 0 0 0 2px ${bg};
      }
      .frame[data-mode="block"] .badge {
        width: 8px;
        height: 8px;
      }
      /* Icon-only + text-only tweaks --------------------------------- */
      .frame[data-chipmode="icon-only"] .chip { gap: 0; }
      .frame[data-chipmode="text-only"] .chip-text { gap: 6px; }
    </style>
  `;
}
