// webpage, Spectra full-bleed iframe. The cell hosts an external
// URL inside a sandboxed iframe; ``scale`` shrinks the page's logical
// pixels so a desktop layout fits in a small cell, and a fixed
// ``viewport_w`` keeps responsive sites from collapsing to a mobile
// breakpoint. The composer's headless render screenshots whatever
// the iframe lands on. A URL that resolves to an image takes a
// separate <img> path instead, see ``probeImage``.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Grace period for the iframe's ``load`` on top of the configured settle.
// The overall cap is ``settle + grace`` rather than a fixed number: a fixed
// 12s cap silently ate into the settle window whenever the page loaded
// slowly, so a 10s settle on a 5s-loading page got 7s and a page that took
// longer than the cap to load was captured blank (the "sometimes blank"
// intermittent). Worst case (12s settle) is 22s, which the renderer's 25s
// ``__tesseraeComposed`` budget (_COMPOSE_SIGNAL_TIMEOUT_MS) is sized to
// outlast, so a slow / hung site still can't pin the render.
const IFRAME_LOAD_GRACE_MS = 10000;
// Default settle window (seconds) after ``load`` when the cell doesn't set one.
const DEFAULT_SETTLE_S = 2;
// Ceiling on the settle option, mirrors plugin.json's ``max: 12``.
const MAX_SETTLE_MS = 12000;
// Cap on the "is this URL an image?" probe. Kept short because it is spent
// before the iframe path even starts, and an HTML URL normally fails the
// probe immediately (the decoder rejects the first bytes).
const IMAGE_PROBE_CAP_MS = 6000;

// A URL that resolves to an image is not a page. Chromium wraps a bare image
// in its own image document rendered at the image's natural size, which the
// iframe then clips to the cell rather than fitting, so an image taller than
// the cell silently loses its bottom edge. Load it as an <img> instead and
// let object-fit do the work. Probing by decode rather than by file extension
// keeps image URLs served from an extension-less path on this path too.
function probeImage(url) {
  return new Promise((resolve) => {
    const img = new Image();
    let settled = false;
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      resolve(ok);
    };
    img.onload = () => finish(img.naturalWidth > 0 && img.naturalHeight > 0);
    img.onerror = () => finish(false);
    img.referrerPolicy = "no-referrer";
    setTimeout(() => finish(false), IMAGE_PROBE_CAP_MS);
    img.src = url;
  });
}

// Same fit vocabulary the Send tab and the push pipeline use, so a URL framed
// one way in Send frames the same way in a cell. "blur" is the odd one: it
// contains the image over a cover-cropped, blurred copy of itself, matching
// the backdrop the renderer produces (see .preview-bg in send.css).
const IMAGE_OBJECT_FIT = {
  fit: "contain",
  fill: "cover",
  stretch: "fill",
  center: "none",
  blur: "contain",
};

function imageMarkup(url, fitOpt) {
  const mode = Object.hasOwn(IMAGE_OBJECT_FIT, fitOpt) ? fitOpt : "fit";
  const src = escapeHtml(url);
  const fg = `<img class="fg" src="${src}" alt="" referrerpolicy="no-referrer"
       style="object-fit:${IMAGE_OBJECT_FIT[mode]}">`;
  const bg = mode === "blur"
    ? `<img class="bg" src="${src}" alt="" aria-hidden="true" referrerpolicy="no-referrer">`
    : "";
  return `
    <style>
      .shell { position:relative; width:100%; height:100%; overflow:hidden; background:#fff; }
      .shell > img { position:absolute; inset:0; width:100%; height:100%; display:block; }
      .shell > img.fg { z-index:1; }
      /* Overscaled so the blur's soft edge never reveals the cell border. */
      .shell > img.bg { z-index:0; object-fit:cover; transform:scale(1.08);
                        filter:blur(18px) saturate(1.15); }
    </style>
    <div class="shell">${bg}${fg}</div>`;
}

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

  if (await probeImage(url)) {
    shadow.innerHTML = `
      ${css}
      <div class="w is-bleed" data-widget="webpage">
        ${imageMarkup(url, String(opts.image_fit || "fit"))}
      </div>`;
    // The probe warmed the cache, so these decodes are normally instant;
    // awaiting them keeps the composer from screenshotting a half-painted cell.
    await Promise.all(
      [...shadow.querySelectorAll("img")].map((img) =>
        img.decode ? img.decode().catch(() => {}) : Promise.resolve(),
      ),
    );
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
  // let that async work paint, capped at settle + ``IFRAME_LOAD_GRACE_MS``
  // so a never-loading or deliberately-slow site can't pin the render.
  const iframe = shadow.querySelector("iframe");
  if (!iframe) return;
  const rawSettle = Number(opts.settle_seconds);
  const settleMs = Math.max(
    0,
    Math.min(MAX_SETTLE_MS, (Number.isFinite(rawSettle) ? rawSettle : DEFAULT_SETTLE_S) * 1000),
  );
  const hardCapMs = settleMs + IFRAME_LOAD_GRACE_MS;
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
    // Absolute cap: full settle + the load grace, so a hung site can't pin
    // the render but a slow-loading one no longer eats the settle window.
    setTimeout(done, hardCapMs);
  });
}
