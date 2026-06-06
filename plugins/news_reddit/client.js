// news_reddit, Spectra list archetype. Title bar carries the
// subreddit + sort/window meta; each row has a post-type lead glyph
// (image / link / video / text-post / github / youtube …), the
// title, and author + age on the meta side. The widget itself wears
// a subreddit-coloured left stripe (deterministic hash → accent
// token) so two side-by-side subreddit cells feel visually distinct.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtScore(n) {
  if (n == null) return "-";
  const v = Number(n) || 0;
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k`;
  return String(v);
}

function fmtAgo(epochSec) {
  if (!Number.isFinite(epochSec) || epochSec <= 0) return "";
  const secs = Math.max(0, Date.now() / 1000 - epochSec);
  if (secs < 60) return "now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h`;
  if (secs < 604800) return `${Math.floor(secs / 86400)}d`;
  return `${Math.floor(secs / 604800)}w`;
}

// Post-type / source glyph. self-posts → text-post, image → image,
// video → video, github → github logo, etc. Falls back to ph-link.
function postTypeIcon(url, isSelf) {
  if (isSelf) return "ph-text-aa";
  if (typeof url !== "string") return "ph-link";
  const u = url.toLowerCase();
  if (u.includes("v.redd.it") || u.includes("youtube.com") || u.includes("youtu.be") || u.endsWith(".mp4")) return "ph-video-camera";
  if (u.includes("i.redd.it") || u.includes("imgur.com") || /\.(jpe?g|png|gif|webp)(\?|$)/.test(u)) return "ph-image";
  if (u.includes("github.com")) return "ph-github-logo";
  if (u.includes("twitter.com") || u.includes("x.com/")) return "ph-x-logo";
  if (u.includes("arxiv.org")) return "ph-graduation-cap";
  if (u.includes("reddit.com/r/")) return "ph-chat-circle-text";
  return "ph-link";
}

// Hash a subreddit name → one of the six accent tokens. Stable
// across renders so the stripe colour stays put per subreddit.
function subColor(sub) {
  let h = 0;
  const s = String(sub || "").toLowerCase();
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  const accents = ["var(--accent-1)", "var(--accent-2)", "var(--accent-3)", "var(--accent-4)", "var(--accent-5)", "var(--accent-6)"];
  return accents[Math.abs(h) % accents.length];
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="news_reddit">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Reddit</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const posts = Array.isArray(data.posts) ? data.posts : [];
  const sub = data.subreddit || "reddit";
  const sort = data.sort ? String(data.sort).toUpperCase() : "TOP";
  const accent = subColor(sub);

  if (posts.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="news_reddit">
        <div class="w-title">
          <i class="ph-bold ph-reddit-logo" style="color:${accent}"></i>
          <h3>r/${escapeHtml(sub)}</h3>
        </div>
        <div class="w-body"><p class="u-muted">No posts.</p></div>
      </div>`;
    return;
  }

  const rows = posts.map((p, i) => {
    const ph = postTypeIcon(p.url, p.is_self);
    const ago = fmtAgo(p.time);
    const scoreLine = p.score != null
      ? `<span class="rd-score"><i class="ph-bold ph-arrow-fat-up"></i>${escapeHtml(fmtScore(p.score))}</span>`
      : "";
    const subBits = [];
    if (p.author) subBits.push(`u/${escapeHtml(p.author)}`);
    if (p.comments != null) subBits.push(`<span><i class="ph-bold ph-chat-circle"></i>${escapeHtml(fmtScore(p.comments))}</span>`);
    if (ago) subBits.push(`<span>${escapeHtml(ago)}</span>`);
    return `
      <div class="rd-row ${i % 2 ? "is-zebra" : ""}">
        <div class="list-lead rd-row-lead">
          <i class="ph-bold ${ph}" style="color:${accent}"></i>
          <span class="list-title">${escapeHtml(p.title)}</span>
        </div>
        <div class="rd-meta">
          ${scoreLine}
          ${subBits.length ? `<small class="rd-sub">${subBits.join(" · ")}</small>` : ""}
        </div>
      </div>`;
  }).join("");

  const layout = `
    /* Subreddit-coloured left stripe, a 3px accent border via
       box-shadow inset, so the widget itself wears the subreddit's
       colour identity. Two r/programming + r/eink cells side by side
       are immediately distinguishable without reading the heading. */
    .w[data-widget="news_reddit"] {
      box-shadow: inset 4px 0 0 ${accent};
    }
    .rd-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--space-3);
      padding: var(--space-2) var(--space-3);
      border-radius: var(--radius-1);
      min-width: 0;
    }
    .rd-row.is-zebra {
      background: color-mix(in oklab, var(--text-primary) 3%, transparent);
    }
    .rd-row-lead {
      flex: 1 1 auto;
      min-width: 0;
      gap: var(--space-2);
    }
    .rd-row-lead .list-title {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .rd-meta {
      display: flex;
      flex-direction: column;
      align-items: flex-end;
      gap: 1px;
      flex: 0 0 auto;
    }
    .rd-score {
      display: inline-flex;
      align-items: center;
      gap: 2px;
      color: ${accent};
      font-weight: var(--fw-black);
      font-variant-numeric: tabular-nums;
    }
    .rd-score i {
      font-size: .85em;
    }
    .rd-sub {
      color: var(--text-muted);
      font-weight: var(--fw-semi);
      font-size: .75em;
      font-variant-numeric: tabular-nums;
    }
    .rd-sub i {
      font-size: .9em;
      margin-right: 2px;
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="news_reddit">
      <div class="w-title">
        <i class="ph-bold ph-reddit-logo" style="color:${accent}"></i>
        <h3>r/${escapeHtml(sub)}</h3>
        <span class="w-title-meta">${escapeHtml(sort)}</span>
      </div>
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
