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

// Source-host → leading icon. The default ph-newspaper-clipping stays
// the fallback so unfamiliar hosts read as plain "news"; familiar
// ones get a recognisable wordmark icon so the row palette has more
// variety than a column of identical squares.
function sourceIcon(url) {
  if (typeof url !== "string") return "ph-newspaper-clipping";
  const u = url.toLowerCase();
  if (u.includes("github.com")) return "ph-github-logo";
  if (u.includes("youtube.com") || u.includes("youtu.be")) return "ph-youtube-logo";
  if (u.includes("twitter.com") || u.includes("x.com/")) return "ph-x-logo";
  if (u.includes("arxiv.org")) return "ph-graduation-cap";
  if (u.includes("medium.com") || u.includes(".substack.com")) return "ph-article";
  if (u.includes("news.ycombinator.com")) return "ph-chat-circle-text";
  return "ph-newspaper-clipping";
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
    const ph = sourceIcon(s.url);
    const ago = fmtAgo(s.time);
    // Two-line meta: upvote count on top with the trend arrow,
    // comments + relative time underneath. Reads denser without
    // crowding the headline column.
    const scoreLine = `
      <span style="display:flex;align-items:center;gap:.25em;font-feature-settings:'tnum';color:${isLead ? "var(--accent-1)" : "var(--text-primary)"}">
        <i class="ph-bold ph-arrow-fat-up" style="font-size:.85em"></i>${escapeHtml(fmtScore(s.score))}
      </span>`;
    const subBits = [];
    if (s.comments != null) subBits.push(`<span style="display:inline-flex;align-items:center;gap:.2em"><i class="ph-bold ph-chat-circle" style="font-size:.85em"></i>${escapeHtml(fmtScore(s.comments))}</span>`);
    if (ago) subBits.push(`<span>${escapeHtml(ago)}</span>`);
    const subLine = subBits.length
      ? `<small style="display:flex;align-items:center;gap:.4em;color:var(--text-muted);font-weight:var(--fw-semi);font-size:.7em;font-feature-settings:'tnum'">${subBits.join("")}</small>`
      : "";
    return `
      <div class="list-row ${i % 2 ? "is-zebra" : ""}">
        <div class="list-lead">
          <i class="ph-bold ${ph}" style="color:var(--accent-5)"></i>
          <span class="list-title">${escapeHtml(s.title)}</span>
        </div>
        <span class="list-meta" style="display:flex;flex-direction:column;align-items:flex-end;gap:.1em">
          ${scoreLine}${subLine}
        </span>
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
