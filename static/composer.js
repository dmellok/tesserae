// Composer bootstrap. For each .cell on the page, attach a shadow DOM and
// call the plugin's default-export render function with the documented ctx
// shape (see docs/contracts/plugins.md once that's written).
//
// Theme palette and font family are resolved server-side by app/composer.py;
// CSS custom properties (--theme-*) and font-family are already on the cell's
// inline style attribute. We just read them back into ctx.theme / ctx.font for
// plugins that need actual hex values (Chart.js, canvas, etc.).

const SIZE_THRESHOLDS = [
  { size: "xs", max: 200 },
  { size: "sm", max: 400 },
  { size: "md", max: 700 },
];

function resolveSize(w, h) {
  const longer = Math.max(w, h);
  for (const { size, max } of SIZE_THRESHOLDS) {
    if (longer <= max) return size;
  }
  return "lg";
}

const FALLBACK_THEME = {
  bg: "#ffffff", fg: "#1a1a1a", fgSoft: "#555555",
  surface: "#f5f5f5", surface2: "#e8e8e8", muted: "#888888",
  accent: "#3060c0", accentSoft: "#2148a0",
  divider: "#c8c8c8", danger: "#c44a3a", warn: "#c89028", ok: "#3a8848",
};

function reportError(cell, shadow, pluginId, err) {
  cell.classList.add("error");
  cell.dataset.error = err.message || String(err);
  shadow.innerHTML = `
    <style>.error { color: #c44a3a; padding: 8px; font: 12px/1.4 monospace; }</style>
    <div class="error">${pluginId}: ${err.message || err}</div>
  `;
  // Mark as rendered so the screenshot pipeline doesn't hang waiting for a
  // cell that's already failed.
  cell.dataset.rendered = "true";
  // eslint-disable-next-line no-console
  console.error(`[composer] plugin ${pluginId} failed:`, err);
}

async function mountCell(cell) {
  const w = Number(cell.dataset.cellW);
  const h = Number(cell.dataset.cellH);
  const panelW = Number(cell.dataset.panelW);
  const panelH = Number(cell.dataset.panelH);
  const pluginId = cell.dataset.plugin;

  let options = {};
  try {
    options = JSON.parse(cell.dataset.options || "{}");
  } catch {
    options = {};
  }

  let pluginData = null;
  try {
    pluginData = JSON.parse(cell.dataset.data || "null");
  } catch {
    pluginData = null;
  }

  let palette = FALLBACK_THEME;
  try {
    palette = JSON.parse(cell.dataset.themePalette || "null") || FALLBACK_THEME;
  } catch {
    palette = FALLBACK_THEME;
  }

  const fontFamily =
    cell.dataset.fontFamily ||
    'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

  const shadow = cell.attachShadow({ mode: "open" });

  const ctx = {
    cell: { w, h, size: resolveSize(w, h), options },
    panel: { w: panelW, h: panelH, portrait: panelH > panelW },
    theme: palette,
    font: { family: fontFamily, weight: 400 },
    data: pluginData,
    preview: new URLSearchParams(location.search).get("preview") === "1",
  };

  try {
    const mod = await import(`/plugins/${pluginId}/client.js`);
    if (typeof mod.default !== "function") {
      throw new Error("plugin module has no default export");
    }
    await mod.default(shadow, ctx);
  } catch (err) {
    reportError(cell, shadow, pluginId, err);
  }
}

const cells = document.querySelectorAll(".cell[data-plugin]");
await Promise.all(Array.from(cells).map(mountCell));
