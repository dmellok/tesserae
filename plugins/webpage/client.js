// webpage — embed any URL into a cell via iframe. The composer captures
// whatever the iframe renders; sites that block embedding (X-Frame-Options,
// strict CSP) will show a fallback message and the page's own block screen.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function renderError(msg) {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/plugins/webpage/client.css">
    <div class="root error">
      <i class="ph ph-warning-circle" aria-hidden="true"></i>
      <span>${escapeHtml(msg)}</span>
    </div>
  `;
}

export default async function render(shadow, ctx) {
  const opts = ctx.cell.options || {};
  const url = (opts.url || "").trim();
  if (!url) {
    shadow.innerHTML = renderError("No URL configured.");
    return;
  }
  // Reject anything that isn't http/https so we don't surface internal
  // file:// paths or javascript: schemes.
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    shadow.innerHTML = renderError(`Invalid URL: ${url}`);
    return;
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    shadow.innerHTML = renderError(`Only http/https URLs are allowed (got ${parsed.protocol}).`);
    return;
  }

  // Render the iframe at a "desktop" logical width so responsive sites
  // serve the full layout, then scale it to fit the cell. Scale "fit"
  // computes a ratio that fits the cell exactly; numeric scales use the
  // literal percentage. "100" lets the iframe overflow + scroll.
  const viewportW = Math.max(200, Math.min(4096, Number(opts.viewport_w) || 1280));
  const scaleOpt = String(opts.scale || "fit");
  const refreshSeconds = Math.max(0, Math.min(86400, Number(opts.refresh_seconds) || 0));

  shadow.innerHTML = `
    <link rel="stylesheet" href="/plugins/webpage/client.css">
    <div class="root">
      <iframe class="wp-frame"
              data-wp-frame
              src="${escapeHtml(url)}"
              referrerpolicy="no-referrer"
              sandbox="allow-scripts allow-same-origin"
              loading="lazy"
              title="${escapeHtml(parsed.hostname)}"></iframe>
    </div>
  `;

  const root = shadow.querySelector(".root");
  const frame = shadow.querySelector("[data-wp-frame]");

  function applyScale() {
    if (!root || !frame) return;
    const rect = root.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;
    let scale;
    if (scaleOpt === "fit") {
      scale = rect.width / viewportW;
    } else {
      scale = Number(scaleOpt) / 100;
    }
    const viewportH = Math.ceil(rect.height / scale);
    frame.style.width = viewportW + "px";
    frame.style.height = viewportH + "px";
    frame.style.transform = `scale(${scale})`;
    frame.style.transformOrigin = "0 0";
  }

  applyScale();
  // ResizeObserver reflows scale when the cell changes size in the
  // editor preview. Safe to no-op if the browser is ancient.
  if (typeof ResizeObserver !== "undefined") {
    if (shadow.__wpRO) shadow.__wpRO.disconnect();
    shadow.__wpRO = new ResizeObserver(applyScale);
    shadow.__wpRO.observe(root);
  }

  // Auto-refresh by replacing src. Avoids cache by appending a hash.
  if (shadow.__wpTimer) clearInterval(shadow.__wpTimer);
  if (refreshSeconds > 0) {
    shadow.__wpTimer = setInterval(() => {
      if (frame) frame.src = url + (url.includes("#") ? "&" : "#") + "_t=" + Date.now();
    }, refreshSeconds * 1000);
  }
}
