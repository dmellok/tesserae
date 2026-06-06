// spotify_queue, Spectra list archetype. Currently-playing track is
// pinned at the top in accent-3 with a circular progress arc around
// its album thumbnail (when Spotify reports progress_ms + duration_ms);
// upcoming queue items follow as zebra rows. Each row carries a
// duration mini-bar scaled to the longest track in the visible queue.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtMmSs(ms) {
  if (typeof ms !== "number" || !Number.isFinite(ms) || ms < 0) return "";
  const total = Math.floor(ms / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

// SVG progress ring around the now-playing thumbnail. Uses a CSS-
// trick of stroke-dasharray on a circle so the arc length tracks
// the progress fraction.
function progressRingSvg({ pct, color }) {
  const r = 14;
  const circ = 2 * Math.PI * r;
  const filled = circ * Math.max(0, Math.min(1, pct / 100));
  return `
    <svg viewBox="-16 -16 32 32" aria-hidden="true"
         style="position:absolute;inset:0;width:100%;height:100%;pointer-events:none">
      <g transform="rotate(-90)">
        <circle r="${r}" fill="none" stroke="color-mix(in oklab, var(--text-primary) 12%, transparent)" stroke-width="2.4"/>
        <circle r="${r}" fill="none" stroke="${color}" stroke-width="2.4"
                stroke-dasharray="${filled.toFixed(2)} ${circ.toFixed(2)}"
                stroke-linecap="round"/>
      </g>
    </svg>`;
}

function row(item, idx, opts) {
  const { isCurrent, longestMs, pct } = opts;
  const accent = isCurrent ? "var(--accent-3)" : "var(--accent-5)";
  const art = item.album_art_thumb || item.album_art;
  const durMs = Number(item.duration_ms) || 0;
  const lead = art
    ? `<span class="sq-thumb-wrap">
        <img class="sq-thumb" src="${escapeHtml(art)}" alt="">
        ${isCurrent && Number.isFinite(pct) ? progressRingSvg({ pct, color: accent }) : ""}
      </span>`
    : `<i class="ph-bold ${isCurrent ? "ph-play" : "ph-music-note"}" style="color:${accent}"></i>`;
  const durBar = durMs > 0 && longestMs > 0
    ? `<span class="sq-dur-wrap">
        <span class="sq-dur-fill" style="width:${((durMs / longestMs) * 100).toFixed(1)}%;background:${accent}"></span>
      </span>`
    : "";
  return `
    <div class="sq-row ${(idx % 2 && !isCurrent) ? "is-zebra" : ""}${isCurrent ? " is-current" : ""}">
      <div class="list-lead sq-row-lead">
        ${lead}
        <div class="sq-text">
          <span class="sq-title" style="${isCurrent ? `color:${accent}` : ""}">${escapeHtml(item.track || item.title || "-")}</span>
          <small class="sq-artist">${escapeHtml(item.artist || "")}</small>
        </div>
      </div>
      <div class="sq-meta">
        ${durBar}
        <span class="sq-dur-text">${escapeHtml(fmtMmSs(durMs))}</span>
      </div>
    </div>`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="spotify_queue">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Spotify Queue</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  if (data.idle) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="spotify_queue">
        <div class="w-title">
          <i class="ph-bold ph-queue" style="color:var(--text-muted)"></i>
          <h3>Spotify Queue</h3>
        </div>
        <div class="w-body"><p class="u-muted">Not playing.</p></div>
      </div>`;
    return;
  }

  const queue = Array.isArray(data.queue) ? data.queue : [];
  const current = data.currently_playing || null;
  const total = queue.length + (current ? 1 : 0);

  if (total === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="spotify_queue">
        <div class="w-title">
          <i class="ph-bold ph-queue" style="color:var(--accent-3)"></i>
          <h3>Spotify Queue</h3>
        </div>
        <div class="w-body"><p class="u-muted">Queue empty.</p></div>
      </div>`;
    return;
  }

  const everything = [current, ...queue].filter(Boolean);
  const longestMs = Math.max(0, ...everything.map((t) => Number(t.duration_ms) || 0));

  let currentPct = null;
  if (current && Number.isFinite(current.progress_ms) && Number.isFinite(current.duration_ms) && current.duration_ms > 0) {
    currentPct = (current.progress_ms / current.duration_ms) * 100;
  } else if (Number.isFinite(data.progress_ms) && Number.isFinite(data.duration_ms) && data.duration_ms > 0) {
    currentPct = (data.progress_ms / data.duration_ms) * 100;
  }

  const rows = [
    current ? row(current, 0, { isCurrent: true, longestMs, pct: currentPct }) : "",
    ...queue.map((t, i) => row(t, i + (current ? 1 : 0), { isCurrent: false, longestMs })),
  ].join("");

  const layout = `
    .sq-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--space-2);
      padding: var(--space-2) var(--space-3);
      border-radius: var(--radius-1);
      min-width: 0;
    }
    .sq-row.is-zebra {
      background: color-mix(in oklab, var(--text-primary) 3%, transparent);
    }
    .sq-row.is-current {
      background: color-mix(in oklab, var(--accent-3) 7%, transparent);
    }
    .sq-row-lead {
      flex: 1 1 auto;
      min-width: 0;
      gap: var(--space-2);
    }
    .sq-thumb-wrap {
      position: relative;
      width: 2.4em;
      height: 2.4em;
      flex: 0 0 auto;
      display: inline-block;
    }
    .sq-thumb {
      width: 100%;
      height: 100%;
      object-fit: cover;
      border-radius: var(--radius-1);
    }
    .sq-text {
      display: flex;
      flex-direction: column;
      gap: 0;
      min-width: 0;
    }
    .sq-title {
      font-weight: var(--fw-bold);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      max-width: 100%;
    }
    .sq-row.is-current .sq-title {
      font-weight: var(--fw-black);
    }
    .sq-artist {
      color: var(--text-muted);
      font-weight: var(--fw-semi);
      font-size: .75em;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .sq-meta {
      display: flex;
      align-items: center;
      gap: var(--space-1);
      flex: 0 0 auto;
    }
    .sq-dur-wrap {
      position: relative;
      width: 3em;
      height: 4px;
      border-radius: 2px;
      background: color-mix(in oklab, var(--text-primary) 6%, transparent);
      overflow: hidden;
    }
    .sq-dur-fill {
      position: absolute;
      left: 0;
      top: 0;
      bottom: 0;
      border-radius: 2px;
      opacity: 0.55;
    }
    .sq-dur-text {
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      font-variant-numeric: tabular-nums;
      color: var(--text-secondary);
      min-width: 2.4em;
      text-align: right;
    }
    @container (max-width: 320px) {
      .sq-dur-wrap { display: none; }
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="spotify_queue">
      <div class="w-title">
        <i class="ph-bold ph-queue" style="color:var(--accent-3)"></i>
        <h3>Spotify Queue</h3>
        <span class="w-title-meta">${queue.length} UP NEXT</span>
      </div>
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
