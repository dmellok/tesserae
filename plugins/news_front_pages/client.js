// news_front_pages, Spectra full-bleed image. One newspaper front page
// at the cell's full width, top-aligned: the masthead and the lead
// stories sit at the top of a front page, so a cell shorter than the
// page loses the bottom (usually adverts) rather than the headlines.
// No overlay: the masthead already names the paper and the date.
//
// ``css`` is the paper's own CSS from the widget's settings, already cut
// down by the server to a short list of safe declarations
// (sanitize_css in server.py). It goes on this paper's <img> as its
// inline style, through the DOM rather than markup, so it styles that
// image alone and has nothing to break out of. The check below is a
// second line, not the first: CSS that names a URL or carries markup
// characters is dropped whole.

function escapeHtml(s) {
  return String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[c]
  );
}

const UNSAFE_CSS = /[<>{}\\@"'#&]|url\s*\(|image-set|expression|import|var\s*\(/i;

function paperCss(data) {
  const css = typeof data.css === "string" ? data.css : "";
  return css.length <= 2000 && !UNSAFE_CSS.test(css) ? css : "";
}

// Put the page on whole pixels. A percentage translate (the usual way to
// hide a slug line: translateY(-2%) of a 2777 px page is -55.54 px) leaves
// it between pixel rows, and the browser then resamples the whole page:
// every line of text goes half a pixel soft before the panel even dithers
// it. The ``translate`` property (which a paper's CSS can't set) adds the
// missing fraction. Layout offsets are already snapped; this is for
// transforms. Measured again once the image has loaded.
function snapToPixels(img) {
  img.style.translate = "";
  const box = img.getBoundingClientRect();
  if (!box.width || !box.height) return;
  const dx = Math.round(box.left) - box.left;
  const dy = Math.round(box.top) - box.top;
  if (Math.abs(dx) > 0.001 || Math.abs(dy) > 0.001) {
    img.style.translate = `${dx.toFixed(3)}px ${dy.toFixed(3)}px`;
  }
}

// Narrower than this, a page drawn at the cell's width has headlines too
// small to read: the page is drawn this wide instead and centred, so the
// top of it (masthead and headlines) shows at a readable size.
export const READABLE_PX = 720;

// How the page sits in a ``w`` x ``h`` cell: "full" (the cell's width, from
// the top, as on the big portrait panel), "zoom" (READABLE_PX wide, centred,
// from the top) or "whole" (the whole page, smaller). ``fit`` is the
// "On smaller screens" setting; ``ratio`` the page's height over its width.
export function pageFit(w, h, ratio, fit) {
  if (!(w > 0) || !(h > 0) || !(ratio > 0)) return "full";
  if (fit === "whole" && w * ratio > h) return "whole";
  return w < READABLE_PX ? "zoom" : "full";
}

export default function render(shadow, ctx) {
  const t = ctx?.t || ((key, fallback) => fallback ?? key);
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="news_front_pages">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>${t("front_pages", "Front pages")}</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  if (!data.url) {
    // ``pending``: nothing saved yet and the download is on its way; the
    // host renders again (``live_refresh_s``) once it lands.
    const message = data.pending
      ? t("fetching", "Fetching today's front page…")
      : t("no_front_page", "No front page today.");
    shadow.innerHTML = `
      ${css}
      <div class="w is-bleed" data-widget="news_front_pages">
        <div class="bleed-empty">${escapeHtml(message)}</div>
      </div>`;
    return;
  }

  const alt = [data.code, data.date].filter(Boolean).join(", ");
  // The page's own size, so it is laid out (and snapped) before it loads.
  const width = Math.round(Number(data.width) || 0);
  const height = Math.round(Number(data.height) || 0);
  const size = width > 0 && height > 0 ? ` width="${width}" height="${height}"` : "";
  const fit = pageFit(
    Number(ctx?.cell?.w) || 0,
    Number(ctx?.cell?.h) || 0,
    width > 0 ? height / width : 0,
    data.fit,
  );
  shadow.innerHTML = `
    ${css}
    <style>
      .w.is-bleed {
        overflow: hidden;
      }
      .w.is-bleed > img.page {
        flex: none;
        width: 100%;
        height: auto;
        object-fit: fill;
      }
      .w.is-bleed.fit-zoom > img.page {
        width: ${READABLE_PX}px;
        max-width: none;
        align-self: center;
      }
      .w.is-bleed.fit-whole { align-items: center; }
      .w.is-bleed.fit-whole > img.page {
        width: auto;
        height: 100%;
        max-width: 100%;
        object-fit: contain;
      }
    </style>
    <div class="w is-bleed fit-${fit}" data-widget="news_front_pages">
      <img class="page" src="${escapeHtml(data.url)}" alt="${escapeHtml(alt)}"${size}>
    </div>`;
  const img = shadow.querySelector("img.page");
  if (!img) return;
  img.style.cssText = paperCss(data);
  snapToPixels(img);
  img.addEventListener("load", () => snapToPixels(img), { once: true });
}
