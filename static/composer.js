// Composer bootstrap. For each .cell on the page, attach a shadow DOM and
// call the plugin's default-export render function with the documented ctx
// shape.
//
// Theme system was stripped in v0.17; ctx no longer carries a palette and
// cells no longer cascade --theme-* / --c-* tokens into their shadow root.
//
// Partial updates: the editor patches the composer via postMessage instead
// of forcing a full iframe reload for every keystroke. See applyPatch below.

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

// Walk a freshly-rendered widget shadow root and prepend
// ``TESSERAE_URL_PREFIX`` to root-relative href / src attributes. Under
// HA Ingress that prefix is e.g. ``/api/hassio_ingress/<token>``;
// outside ingress it's empty and this is a no-op.
function prefixShadowUrls(root, prefix) {
  if (!prefix) return;
  for (const el of root.querySelectorAll("[href], [src]")) {
    for (const attr of ["href", "src"]) {
      const v = el.getAttribute(attr);
      if (!v || !v.startsWith("/") || v.startsWith("//") || v.startsWith(prefix)) continue;
      el.setAttribute(attr, prefix + v);
    }
  }
}

function reportError(cell, shadow, pluginId, err) {
  cell.classList.add("error");
  cell.dataset.error = err.message || String(err);
  shadow.innerHTML = `<div>${pluginId}: ${err.message || err}</div>`;
  cell.dataset.rendered = "true";
  // eslint-disable-next-line no-console
  console.error(`[composer] plugin ${pluginId} failed:`, err);
}

const cellState = new Map();

function buildCtx(cell, options, pluginData, fontFamily) {
  const w = Number(cell.dataset.cellW);
  const h = Number(cell.dataset.cellH);
  const panelW = Number(cell.dataset.panelW);
  const panelH = Number(cell.dataset.panelH);
  const zoom = Number(cell.dataset.cellZoom) || 1;
  const virtualW = Math.max(1, Math.round(w / (zoom > 0 ? zoom : 1)));
  const virtualH = Math.max(1, Math.round(h / (zoom > 0 ? zoom : 1)));
  return {
    cell: {
      w: virtualW,
      h: virtualH,
      size: resolveSize(virtualW, virtualH),
      plugin: cell.dataset.plugin || "",
      plugin_id: cell.dataset.plugin || "",
      options,
    },
    panel: { w: panelW, h: panelH, portrait: panelH > panelW },
    font: { family: fontFamily, weight: 400 },
    data: pluginData,
    preview: new URLSearchParams(location.search).get("preview") === "1",
  };
}

async function mountCell(cell) {
  const pluginId = cell.dataset.plugin;
  if (!pluginId) {
    cell.dataset.rendered = "true";
    return;
  }

  let options = {};
  try { options = JSON.parse(cell.dataset.options || "{}"); } catch { options = {}; }
  let pluginData = null;
  try { pluginData = JSON.parse(cell.dataset.data || "null"); } catch { pluginData = null; }
  const fontFamily =
    cell.dataset.fontFamily ||
    'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

  const host = cell.querySelector(".cell-content") || cell;
  const shadow = host.attachShadow({ mode: "open" });
  const ctx = buildCtx(cell, options, pluginData, fontFamily);

  try {
    const prefix = window.TESSERAE_URL_PREFIX || "";
    const mod = await import(`${prefix}/plugins/${pluginId}/client.js`);
    if (typeof mod.default !== "function") {
      throw new Error("plugin module has no default export");
    }
    cellState.set(cell.dataset.cellId, { module: mod, pluginId, shadow });
    await mod.default(shadow, ctx);
    prefixShadowUrls(shadow, prefix);
  } catch (err) {
    reportError(cell, shadow, pluginId, err);
  }
}

function applyPagePatch(page) {
  if (!page) return;
  if (typeof page.bleed_color === "string") {
    const bg = page.bleed_color || "var(--bg)";
    document.body.style.background = bg;
    const panelEl = document.querySelector(".panel");
    if (panelEl) panelEl.style.background = bg;
  }
  // Page-level theme — flips data-theme on body so every cell that
  // doesn't have its own override re-binds the Spectra tokens.
  if (typeof page.theme === "string" && page.theme) {
    document.body.setAttribute("data-theme", page.theme);
  }
  // Page-level style (the orthogonal typographic axis). Same shape as
  // theme: a string flips data-style on body and every non-overridden
  // cell picks up the style-tunable tokens (font / scale / edges).
  if (typeof page.style === "string" && page.style) {
    document.body.setAttribute("data-style", page.style);
  }
  // Page-level font picker. Set --font-family on body inline ONLY when
  // the page has its own font pick — same reasoning as the cell-level
  // path. When the page is inheriting (data-style controls the font),
  // we must NOT set the inline custom property, otherwise we'd override
  // the style's font with the page default and the style picker would
  // stop affecting widget typography.
  if (typeof page.font === "string" && page.font && page.font_family) {
    document.body.style.setProperty(
      "--font-family",
      `'${page.font_family}', system-ui, sans-serif`,
    );
  } else if (page && Object.prototype.hasOwnProperty.call(page, "font")) {
    document.body.style.removeProperty("--font-family");
  }
  if (page.panel && Number(page.panel.w) > 0 && Number(page.panel.h) > 0) {
    const panelEl = document.querySelector(".panel");
    if (panelEl) {
      panelEl.style.width = page.panel.w + "px";
      panelEl.style.height = page.panel.h + "px";
    }
  }
}

function ctxFingerprint(cell) {
  return JSON.stringify({
    o: cell.options ?? null,
    d: cell.data ?? null,
    f: cell.font_family ?? null,
    w: cell.w,
    h: cell.h,
    z: cell.zoom ?? 1,
  });
}

async function applyCellPatch(patch) {
  const cell = document.querySelector(
    `.cell[data-cell-id="${CSS.escape(patch.id)}"]`,
  );
  if (!cell) return;

  cell.style.left = patch.x + "px";
  cell.style.top = patch.y + "px";
  cell.style.width = patch.w + "px";
  cell.style.height = patch.h + "px";
  cell.dataset.cellW = String(patch.w);
  cell.dataset.cellH = String(patch.h);
  if (typeof patch.zoom === "number" && patch.zoom > 0) {
    cell.style.setProperty("--c-zoom", String(patch.zoom));
    cell.dataset.cellZoom = String(patch.zoom);
  }
  if (patch.font_family) {
    cell.style.fontFamily = `'${patch.font_family}', system-ui, sans-serif`;
    cell.dataset.fontFamily = patch.font_family;
    // Spectra widgets read font-family from var(--font-family) inside
    // their shadow root. Override the custom property ONLY when the
    // cell has its own font pick (patch.font is the raw picker value,
    // empty when inheriting from the page). The "always set" form with
    // a `var(--font-family, …)` fallback self-references and CSS
    // invalidates the whole declaration, breaking every chart probe
    // inside the shadow — so we either set a real override or leave
    // the inherited cascade intact.
    if (patch.font) {
      cell.style.setProperty(
        "--font-family",
        `'${patch.font_family}', system-ui, sans-serif`,
      );
    } else {
      cell.style.removeProperty("--font-family");
    }
  }
  // Per-cell theme override — empty / null clears it so the cell falls
  // back to the page theme on body.
  if (Object.prototype.hasOwnProperty.call(patch, "theme")) {
    if (patch.theme) cell.setAttribute("data-theme", patch.theme);
    else cell.removeAttribute("data-theme");
  }
  // Per-cell style override — mirrors the theme behaviour exactly. A
  // missing/empty value clears the override and the cell inherits the
  // page-level data-style from body.
  if (Object.prototype.hasOwnProperty.call(patch, "style")) {
    if (patch.style) cell.setAttribute("data-style", patch.style);
    else cell.removeAttribute("data-style");
  }

  cell.dataset.options = JSON.stringify(patch.options ?? {});
  cell.dataset.data = JSON.stringify(patch.data ?? null);

  const state = cellState.get(patch.id);
  if (!state) return;
  const fp = ctxFingerprint(patch);
  if (state.lastFp === fp) return;
  state.lastFp = fp;

  const ctx = buildCtx(
    cell,
    patch.options ?? {},
    patch.data ?? null,
    patch.font_family ||
      'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
  );
  state.shadow.innerHTML = "";
  try {
    await state.module.default(state.shadow, ctx);
    prefixShadowUrls(state.shadow, window.TESSERAE_URL_PREFIX || "");
  } catch (err) {
    reportError(cell, state.shadow, state.pluginId, err);
  }
}

window.addEventListener("message", async (ev) => {
  if (ev.origin !== location.origin) return;
  const d = ev.data;
  if (!d || d.type !== "tesserae-patch") return;
  applyPagePatch(d.page);
  for (const cellPatch of d.cells || []) {
    await applyCellPatch(cellPatch);
  }
});

const cells = document.querySelectorAll(".cell[data-plugin]");
await Promise.all(Array.from(cells).map(mountCell));
window.__tesseraeComposed = true;
for (const [id, state] of cellState.entries()) {
  const cell = document.querySelector(`.cell[data-cell-id="${CSS.escape(id)}"]`);
  if (!cell) continue;
  let options = {};
  let data = null;
  try { options = JSON.parse(cell.dataset.options || "{}"); } catch { /* fall through */ }
  try { data = JSON.parse(cell.dataset.data || "null"); } catch { /* fall through */ }
  state.lastFp = ctxFingerprint({
    options, data,
    font_family: cell.dataset.fontFamily,
    w: Number(cell.dataset.cellW),
    h: Number(cell.dataset.cellH),
    zoom: Number(cell.dataset.cellZoom) || 1,
  });
}
