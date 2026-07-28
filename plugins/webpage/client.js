// webpage, Spectra full-bleed iframe. The cell hosts an external
// URL inside a sandboxed iframe; ``scale`` shrinks the page's logical
// pixels so a desktop layout fits in a small cell, and a fixed
// ``viewport_w`` keeps responsive sites from collapsing to a mobile
// breakpoint. The composer's headless render screenshots whatever
// the iframe lands on.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Absolute cap on the whole wait (load + settle) so a slow / hung site
// can't pin the render. Kept comfortably under the renderer's 15s
// ``__tesseraeComposed`` budget so the screenshot still happens even when
// the iframe never fires ``load`` or the settle is set high.
const IFRAME_HARD_CAP_MS = 12000;
// Default settle window (seconds) after ``load`` when the cell doesn't set one.
const DEFAULT_SETTLE_S = 2;

export default async function render(shadow, ctx) {
  const opts = ctx?.cell?.options || {};
  const url = String(opts.url || "").trim();
  const scaleOpt = String(opts.scale || "fit");
  const viewportW = Math.max(200, Math.min(4096, Number(opts.viewport_w) || 1280));
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (!url || !url.startsWith("http")) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="webpage">
        <div class="w-title"><i class="ph-bold ph-globe" style="color:var(--text-muted)"></i><h3>Webpage</h3></div>
        <div class="w-body"><p class="u-muted">Set a URL in the cell options (must start with http/https).</p></div>
      </div>`;
    return;
  }

  // Compute a target scale. "fit" sizes the iframe to viewport_w
  // logical pixels and lets CSS scale it down to fit the cell;
  // numeric options (25/50/75/100) are pinned.
  const cellW = Number(ctx?.cell?.w) || 1;
  const scale = scaleOpt === "fit"
    ? Math.min(1, cellW / viewportW)
    : Math.max(0.1, Math.min(1, Number(scaleOpt) / 100));
  const sizedW = `${(100 / scale).toFixed(2)}%`;
  const sizedH = `${(100 / scale).toFixed(2)}%`;

  shadow.innerHTML = `
    ${css}
    <div class="w is-bleed" data-widget="webpage">
      <iframe
        src="${escapeHtml(url)}"
        sandbox="allow-same-origin allow-scripts"
        style="border:0;width:${sizedW};height:${sizedH};transform-origin:top left;transform:scale(${scale.toFixed(3)});display:block"
        loading="eager"
        referrerpolicy="no-referrer"></iframe>
    </div>`;

  // The iframe is its own browsing context and its content load is
  // independent of the parent compose page's network state. Without
  // this await, the renderer's __tesseraeComposed signal fires the
  // instant the iframe element exists, and Playwright screenshots a
  // blank cell.
  //
  // ``load`` is necessary but not sufficient: it fires once the HTML /
  // CSS / JS have downloaded, which is BEFORE a data-driven page (a
  // weather dashboard, an SPA) runs its post-load fetch and updates the
  // DOM. Screenshotting on ``load`` alone captures the empty pre-data
  // state (issue #152). So after ``load`` we hold for a settle window to
  // let that async work paint, capped by ``IFRAME_HARD_CAP_MS`` so a
  // never-loading or deliberately-slow site can't pin the render.
  const iframe = shadow.querySelector("iframe");
  if (!iframe) return;
  const rawSettle = Number(opts.settle_seconds);
  const settleMs = Math.max(
    0,
    Math.min(IFRAME_HARD_CAP_MS, (Number.isFinite(rawSettle) ? rawSettle : DEFAULT_SETTLE_S) * 1000),
  );
  await new Promise((resolve) => {
    let settled = false;
    let settleTimer = null;
    const done = () => {
      if (settled) return;
      settled = true;
      if (settleTimer) clearTimeout(settleTimer);
      resolve();
    };
    const afterLoad = () => {
      // Page finished loading; give its scripts the settle window to
      // fetch + paint before the composer screenshots.
      settleTimer = setTimeout(done, settleMs);
    };
    iframe.addEventListener("load", afterLoad, { once: true });
    iframe.addEventListener("error", done, { once: true });
    // Absolute cap regardless of load / settle.
    setTimeout(done, IFRAME_HARD_CAP_MS);
  });
}
