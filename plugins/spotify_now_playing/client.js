// spotify_now_playing — Bauhaus now-playing card, five selectable layouts
// (the "variant" cell option): split (default), cover, minimal, vinyl, stack.
// All share the data prep + the bauhaus shell; each builds its own body.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function mmss(ms) {
  const total = Math.max(0, Math.floor((ms || 0) / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

const HEAD = `
  <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/fill/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
  <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
  <link rel="stylesheet" href="/plugins/spotify_now_playing/client.css">`;

function header() {
  return `
    <header class="wb-bar">
      <span class="wb-mark" aria-hidden="true"></span>
      <span class="wb-title">Now Playing</span>
      <i class="wb-bar-icon ph ph-spotify-logo" aria-hidden="true"></i>
    </header>`;
}

// Album art element. `circle` clips it to a disc for the vinyl variant.
function art(v, cls = "snp-art") {
  if (v.showArt) {
    return `<div class="${cls}" aria-hidden="true"><img src="${escapeHtml(v.data.album_art)}" alt=""></div>`;
  }
  return `<div class="${cls} snp-art--placeholder" aria-hidden="true"><i class="ph-bold ph-vinyl-record"></i></div>`;
}

function trackLine(v) {
  return `
    <div class="snp-trackrow">
      <i class="ph-fill ph-${v.stateIcon} snp-state" aria-hidden="true"></i>
      <span class="snp-track">${escapeHtml(v.data.track)}</span>
    </div>`;
}

function artistLine(v) {
  return `<div class="snp-artist"><i class="ph-bold ph-user snp-artist-icon" aria-hidden="true"></i>${escapeHtml(v.data.artist)}</div>`;
}

function albumLine(v) {
  if (!v.showAlbum) return "";
  return `<div class="snp-album"><i class="ph-bold ph-disc snp-album-icon" aria-hidden="true"></i>${escapeHtml(v.data.album)}</div>`;
}

// Standard accent progress block (split / vinyl).
function progress(v) {
  if (!v.showProgress) return "";
  return `
    <section class="snp-progress">
      <div class="snp-bar"><span style="width:${v.pct}%"></span></div>
      <div class="snp-times">
        <span>${mmss(v.data.progress_ms)}</span>
        <span>${mmss(v.data.duration_ms)}</span>
      </div>
    </section>`;
}

// Thin progress line, no times (cover / minimal — keeps them quiet).
function progressThin(v) {
  if (!v.showProgress) return "";
  return `<div class="snp-thinbar"><span style="width:${v.pct}%"></span></div>`;
}

// --- variant bodies ---------------------------------------------------

function bodySplit(v) {
  return `
    ${header()}
    <section class="snp-hero">
      ${art(v)}
      <div class="snp-meta">
        ${trackLine(v)}
        ${artistLine(v)}
        ${albumLine(v)}
      </div>
    </section>
    ${progress(v)}`;
}

function bodyCover(v) {
  return `
    ${header()}
    <section class="snp-cover">${art(v, "snp-cover-art")}</section>
    <section class="snp-caption">
      ${trackLine(v)}
      ${artistLine(v)}
    </section>
    ${progressThin(v)}`;
}

function bodyMinimal(v) {
  return `
    ${header()}
    <section class="snp-min">
      ${art(v, "snp-min-art")}
      <div class="snp-min-text">
        ${trackLine(v)}
        ${artistLine(v)}
        ${albumLine(v)}
      </div>
    </section>
    ${progressThin(v)}`;
}

function bodyVinyl(v) {
  return `
    ${header()}
    <section class="snp-hero snp-vinyl">
      <div class="snp-disc">${art(v, "snp-disc-art")}</div>
      <div class="snp-meta">
        ${trackLine(v)}
        ${artistLine(v)}
        ${albumLine(v)}
      </div>
    </section>
    ${progress(v)}`;
}

function bodyStack(v) {
  return `
    ${header()}
    <section class="snp-stack-art">${art(v, "snp-stack-img")}</section>
    <section class="snp-stack-meta">
      ${trackLine(v)}
      ${artistLine(v)}
    </section>
    <section class="snp-bigprog">
      <div class="snp-bigbar"><span style="width:${v.pct}%"></span></div>
      ${v.showProgress ? `<div class="snp-bigtimes"><span>${mmss(v.data.progress_ms)}</span><span>${mmss(v.data.duration_ms)}</span></div>` : ""}
    </section>`;
}

const VARIANTS = {
  split: bodySplit,
  cover: bodyCover,
  minimal: bodyMinimal,
  vinyl: bodyVinyl,
  stack: bodyStack,
};

export default async function render(shadow, ctx) {
  const data = ctx.data || {};

  if (data.error) {
    shadow.innerHTML = `${HEAD}
      <div class="root error">
        <i class="ph ph-warning-circle" aria-hidden="true"></i>
        <span>${escapeHtml(data.error)}</span>
      </div>`;
    return;
  }
  if (data.idle || !data.track) {
    shadow.innerHTML = `${HEAD}
      <div class="root">
        ${header()}
        <div class="snp-stub">
          <i class="ph-duotone ph-music-notes" aria-hidden="true"></i>
          <div class="snp-stub-primary">Nothing playing</div>
        </div>
      </div>`;
    return;
  }

  const size = ctx.cell.size;
  const opts = ctx.cell.options || {};
  const variant = VARIANTS[opts.variant] ? opts.variant : "split";

  const v = {
    data,
    size,
    showArt: opts.show_art !== false && !!data.album_art,
    showAlbum: opts.show_album !== false && !!data.album,
    showProgress: opts.show_progress !== false && data.duration_ms > 0,
    stateIcon: data.is_playing ? "play-circle" : "pause-circle",
    pct: data.duration_ms > 0
      ? Math.max(0, Math.min(100, (data.progress_ms / data.duration_ms) * 100))
      : 0,
  };

  shadow.innerHTML = `${HEAD}
    <div class="root size-${size} v-${variant}${v.showArt ? " has-art" : ""}">
      ${VARIANTS[variant](v)}
    </div>`;
}
