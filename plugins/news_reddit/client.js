// news_reddit — Spectra list archetype. Title bar carries the
// subreddit + sort/window meta; each post is a zebra row with a
// reddit-aware leading icon, the headline, and (when available) the
// upvote score. The score colour is accent-1 on the leading post
// so the top story always reads first.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtScore(n) {
  if (n == null) return "—";
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

function sourceIcon(url, isSelf) {
  if (isSelf) return "ph-chat-circle-text";
  if (typeof url !== "string") return "ph-link";
  const u = url.toLowerCase();
  if (u.includes("github.com")) return "ph-github-logo";
  if (u.includes("youtube.com") || u.includes("youtu.be")) return "ph-youtube-logo";
  if (u.includes("twitter.com") || u.includes("x.com/")) return "ph-x-logo";
  if (u.includes("reddit.com") || u.includes("redd.it") || u.includes("i.redd.it") || u.includes("v.redd.it")) return "ph-image";
  if (u.includes(".jpg") || u.includes(".png") || u.includes(".gif") || u.includes(".webp")) return "ph-image";
  return "ph-link";
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

  if (posts.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="news_reddit">
        <div class="w-title">
          <i class="ph-bold ph-reddit-logo" style="color:var(--accent-1)"></i>
          <h3>r/${escapeHtml(sub)}</h3>
        </div>
        <div class="w-body"><p class="u-muted">No posts.</p></div>
      </div>`;
    return;
  }

  const rows = posts.map((p, i) => {
    const isLead = i === 0;
    const ph = sourceIcon(p.url, p.is_self);
    const ago = fmtAgo(p.time);
    // Meta stacks: upvote count + arrow on top, author + comments +
    // time underneath. The RSS path returns null for score / comments
    // so each piece is rendered conditionally; what survives reads as
    // a clean line.
    const scoreLine = p.score != null
      ? `<span style="display:flex;align-items:center;gap:.25em;font-feature-settings:'tnum';color:${isLead ? "var(--accent-1)" : "var(--text-primary)"}"><i class="ph-bold ph-arrow-fat-up" style="font-size:.85em"></i>${escapeHtml(fmtScore(p.score))}</span>`
      : "";
    const subBits = [];
    if (p.author) subBits.push(`<span>u/${escapeHtml(p.author)}</span>`);
    if (p.comments != null) subBits.push(`<span style="display:inline-flex;align-items:center;gap:.2em"><i class="ph-bold ph-chat-circle" style="font-size:.85em"></i>${escapeHtml(fmtScore(p.comments))}</span>`);
    if (ago) subBits.push(`<span>${escapeHtml(ago)}</span>`);
    const subLine = subBits.length
      ? `<small style="display:flex;align-items:center;gap:.4em;color:var(--text-muted);font-weight:var(--fw-semi);font-size:.7em;font-feature-settings:'tnum'">${subBits.join("")}</small>`
      : "";
    return `
      <div class="list-row ${i % 2 ? "is-zebra" : ""}">
        <div class="list-lead">
          <i class="ph-bold ${ph}" style="color:var(--accent-5)"></i>
          <span class="list-title">${escapeHtml(p.title)}</span>
        </div>
        <span class="list-meta" style="display:flex;flex-direction:column;align-items:flex-end;gap:.1em">
          ${scoreLine}${subLine}
        </span>
      </div>`;
  }).join("");

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="news_reddit">
      <div class="w-title">
        <i class="ph-bold ph-reddit-logo" style="color:var(--accent-1)"></i>
        <h3>r/${escapeHtml(sub)}</h3>
        <span class="w-title-meta">${escapeHtml(sort)}</span>
      </div>
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
