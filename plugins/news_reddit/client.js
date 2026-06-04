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
  if (n == null) return "";
  const v = Number(n) || 0;
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k`;
  return String(v);
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
    const ph = p.is_self ? "ph-chat-circle-text" : "ph-link";
    const score = fmtScore(p.score);
    return `
      <div class="list-row ${i % 2 ? "is-zebra" : ""}">
        <div class="list-lead">
          <i class="ph-bold ${ph}" style="color:var(--accent-5)"></i>
          <span class="list-title">${escapeHtml(p.title)}</span>
        </div>
        <span class="list-meta ${i === 0 && score ? "is-accent" : ""}" style="${i === 0 && score ? "color:var(--accent-1)" : ""}">${escapeHtml(score)}</span>
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
