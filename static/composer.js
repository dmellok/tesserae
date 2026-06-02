// Composer bootstrap. For each .cell on the page, attach a shadow DOM and
// call the plugin's default-export render function with the documented ctx
// shape (see docs/contracts/plugins.md once that's written).
//
// Theme palette and font family are resolved server-side by app/composer.py;
// CSS custom properties (--theme-*) and font-family are already on the cell's
// inline style attribute. We just read them back into ctx.theme / ctx.font for
// plugins that need actual hex values (Chart.js, canvas, etc.).
//
// Partial updates: the editor patches the composer via postMessage instead
// of forcing a full iframe reload for every keystroke. See applyPatch below
// — page-level CSS swaps, per-cell repositions, and re-renders against a
// cached plugin module so a theme change skips re-import + re-fetch.

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

// Walk a freshly-rendered widget shadow root and prepend
// ``TESSERAE_URL_PREFIX`` to root-relative href / src attributes. Under
// HA Ingress that prefix is e.g. ``/api/hassio_ingress/<token>``;
// outside ingress it's empty and this is a no-op. Lets widget authors
// keep writing ``<link href="/static/foo.css">`` without each one
// having to know about ingress paths.
//
// Only ``/single-leading-slash`` paths are rewritten; protocol-relative
// (``//cdn…``) and already-prefixed URLs are left alone. Inline CSS
// ``url(…)`` is not touched — no widget currently uses absolute
// ``url("/…")`` so the regex sweep isn't worth it.
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

// Per-cell state retained across patches so a theme swap can re-render
// without re-importing the plugin module or re-fetching widget data.
// Keyed by the cell's stable id (data-cell-id).
const cellState = new Map();

function buildCtx(cell, options, pluginData, palette, fontFamily) {
  const w = Number(cell.dataset.cellW);
  const h = Number(cell.dataset.cellH);
  const panelW = Number(cell.dataset.panelW);
  const panelH = Number(cell.dataset.panelH);
  // The widget renders into a 1/zoom virtual container that's then
  // transform-scaled back up by .cell-content — see compose.html. Lie
  // to widget JS about cell.w/h to match what the CSS container queries
  // see, so size resolution (xs/sm/md/lg) + JS layout agree with the
  // virtual dims rather than the panel-allocated outer dims. zoom is
  // clamped 0.5–3.0 by the Cell model; the >0 fallback is paranoid.
  const zoom = Number(cell.dataset.cellZoom) || 1;
  const virtualW = Math.max(1, Math.round(w / (zoom > 0 ? zoom : 1)));
  const virtualH = Math.max(1, Math.round(h / (zoom > 0 ? zoom : 1)));
  return {
    cell: {
      w: virtualW,
      h: virtualH,
      size: resolveSize(virtualW, virtualH),
      options,
    },
    panel: { w: panelW, h: panelH, portrait: panelH > panelW },
    theme: palette,
    font: { family: fontFamily, weight: 400 },
    data: pluginData,
    preview: new URLSearchParams(location.search).get("preview") === "1",
  };
}

async function mountCell(cell) {
  const pluginId = cell.dataset.plugin;
  // Unassigned cells (layout slot without a widget yet) carry an empty
  // data-plugin attribute. The template renders its own "pick a widget"
  // placeholder; don't try to import /plugins//client.js.
  if (!pluginId) {
    cell.dataset.rendered = "true";
    return;
  }

  let options = {};
  try { options = JSON.parse(cell.dataset.options || "{}"); } catch { options = {}; }
  let pluginData = null;
  try { pluginData = JSON.parse(cell.dataset.data || "null"); } catch { pluginData = null; }
  let palette = FALLBACK_THEME;
  try {
    palette = JSON.parse(cell.dataset.themePalette || "null") || FALLBACK_THEME;
  } catch { palette = FALLBACK_THEME; }
  const fontFamily =
    cell.dataset.fontFamily ||
    'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

  // Shadow attaches to the inner .cell-content (the inverse-sized box)
  // so its inside is what gets transform-scaled by per-cell zoom. The
  // outer .cell stays at its exact panel-pixel position.
  const host = cell.querySelector(".cell-content") || cell;
  const shadow = host.attachShadow({ mode: "open" });
  const ctx = buildCtx(cell, options, pluginData, palette, fontFamily);

  try {
    const prefix = window.TESSERAE_URL_PREFIX || "";
    const mod = await import(`${prefix}/plugins/${pluginId}/client.js`);
    if (typeof mod.default !== "function") {
      throw new Error("plugin module has no default export");
    }
    // Stash the module + ctx so a future patch can re-render this cell
    // without re-fetching the module or recomputing data server-side.
    cellState.set(cell.dataset.cellId, { module: mod, pluginId, shadow });
    await mod.default(shadow, ctx);
    prefixShadowUrls(shadow, prefix);
  } catch (err) {
    reportError(cell, shadow, pluginId, err);
  }
}

// -- Patch handling ----------------------------------------------------
//
// editor.js sends ``{type: "tesserae-patch", page, cells: [...]}`` after
// every preview update. Cell entries carry the latest hydrated context
// (position, options, palette, data) — apply them in place rather than
// reloading the iframe.

function applyPagePatch(page) {
  if (!page) return;
  // Body / panel background + foreground. The original render embeds
  // these via Jinja into <style>; for live editing we mirror them via
  // inline styles so they win the cascade.
  if (typeof page.bleed_color === "string") {
    document.body.style.background = page.bleed_color;
    const panelEl = document.querySelector(".panel");
    if (panelEl) panelEl.style.background = page.bleed_color;
  }
  if (page.palette && typeof page.palette.fg === "string") {
    document.body.style.color = page.palette.fg;
  }
  if (page.panel && Number(page.panel.w) > 0 && Number(page.panel.h) > 0) {
    const panelEl = document.querySelector(".panel");
    if (panelEl) {
      panelEl.style.width = page.panel.w + "px";
      panelEl.style.height = page.panel.h + "px";
    }
  }
}

// Compare just the ctx-bearing fields so we don't waste a re-render when
// the patch carries identical content. JSON.stringify is the dumb-but-
// fine option here — payloads are small (a single cell's options + data).
// zoom counts because it changes virtualW/H and the size class.
function ctxFingerprint(cell) {
  return JSON.stringify({
    o: cell.options ?? null,
    d: cell.data ?? null,
    p: cell.palette ?? null,
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

  // Update position + cell-host CSS (theme tokens, font-family). The
  // --theme-* vars cascade into the cell's shadow root, so CSS-only
  // widgets restyle for free.
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
    cell.style.setProperty(
      "--theme-font",
      `'${patch.font_family}', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif`,
    );
    cell.dataset.fontFamily = patch.font_family;
  }
  if (patch.palette) {
    for (const [k, v] of Object.entries(patch.palette)) {
      cell.style.setProperty(`--theme-${k}`, v);
    }
    cell.dataset.themePalette = JSON.stringify(patch.palette);
  }

  // Mirror options / data back to the data-* attributes so a future
  // full reload picks up the same view (the iframe HTML is server-
  // rendered from these and we want to keep them in sync).
  cell.dataset.options = JSON.stringify(patch.options ?? {});
  cell.dataset.data = JSON.stringify(patch.data ?? null);

  // Decide whether to skip the re-render. CSS-only changes (theme
  // tokens, font-family, position) propagate via the cascade — but
  // widgets that bake colours into a canvas (Chart.js, sparkline
  // canvases) need a re-render with the new palette. Fingerprint
  // catches *any* meaningful change and re-renders once.
  const state = cellState.get(patch.id);
  if (!state) return; // first paint of a cell that wasn't there at mount
  const fp = ctxFingerprint(patch);
  if (state.lastFp === fp) return;
  state.lastFp = fp;

  const ctx = buildCtx(
    cell,
    patch.options ?? {},
    patch.data ?? null,
    patch.palette ?? FALLBACK_THEME,
    patch.font_family ||
      'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
  );
  // Clear and re-call. The cached module reference means no re-import
  // and no network — the widget renders against the new ctx from
  // already-loaded JS. Widget side-effects (intervals, listeners) are
  // expected to be idempotent or scoped to the shadow root.
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
// Seed each cell's fingerprint so the first patch doesn't unnecessarily
// re-render content the mount already painted.
for (const [id, state] of cellState.entries()) {
  const cell = document.querySelector(`.cell[data-cell-id="${CSS.escape(id)}"]`);
  if (!cell) continue;
  let options = {};
  let data = null;
  let palette = FALLBACK_THEME;
  try { options = JSON.parse(cell.dataset.options || "{}"); } catch { /* fall through */ }
  try { data = JSON.parse(cell.dataset.data || "null"); } catch { /* fall through */ }
  try { palette = JSON.parse(cell.dataset.themePalette || "null") || FALLBACK_THEME; } catch { /* fall through */ }
  state.lastFp = ctxFingerprint({
    options, data, palette,
    font_family: cell.dataset.fontFamily,
    w: Number(cell.dataset.cellW),
    h: Number(cell.dataset.cellH),
    zoom: Number(cell.dataset.cellZoom) || 1,
  });
}
