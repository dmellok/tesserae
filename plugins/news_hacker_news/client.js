// news_hacker_news — Spectra list archetype.
//
// Title bar shows the feed name; body is a zebra-striped grid of stories
// with a leading newspaper icon, the headline, and a right-aligned
// upvote count (accent-1 for the leading story).

const FEED_LABELS = {
  top: "Top",
  new: "New",
  best: "Best",
  show: "Show",
  ask: "Ask",
};

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtScore(n) {
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
      <div class="w" data-widget="news_hacker_news">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Hacker News</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const stories = Array.isArray(data.stories) ? data.stories : [];
  const feedLabel = FEED_LABELS[data.feed] || "Top";

  if (stories.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="news_hacker_news">
        <div class="w-title">
          <i class="ph-bold ph-newspaper-clipping" style="color:var(--accent-5)"></i>
          <h3>Hacker News</h3>
          <span class="w-title-meta">${escapeHtml(feedLabel)}</span>
        </div>
        <div class="w-body"><p class="u-muted">No stories.</p></div>
      </div>`;
    return;
  }

  const rows = stories.map((s, i) => {
    const isLead = i === 0;
    const score = fmtScore(s.score);
    return `
      <div class="list-row ${i % 2 ? "is-zebra" : ""}">
        <div class="list-lead">
          <i class="ph-bold ph-newspaper-clipping" style="color:var(--accent-5)"></i>
          <span class="list-title">${escapeHtml(s.title)}</span>
        </div>
        <span class="list-meta ${isLead ? "is-accent" : ""}">${escapeHtml(score)}</span>
      </div>`;
  }).join("");

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="news_hacker_news">
      <div class="w-title">
        <i class="ph-bold ph-newspaper-clipping"></i>
        <h3>Hacker News</h3>
        <span class="w-title-meta">${escapeHtml(feedLabel)}</span>
      </div>
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
