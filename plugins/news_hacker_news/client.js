// news_hacker_news, Spectra list archetype.
//
// Each row carries a story-type chip (Show / Ask / Job / Story
// derived from title prefix), the headline, and a right column with
// the score, a thin proportional score-strength bar (relative to
// the feed's max), and comments + age beneath. Source-host glyph
// leads each row so the feed's palette stays varied.

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

// Story-type from the title prefix. HN's API doesn't ship a type
// field for the top stories endpoint; the prefix convention is how
// the site itself categorises them.
function storyType(title) {
  if (typeof title !== "string") return null;
  if (/^show hn:?/i.test(title)) return { label: "SHOW", color: "var(--accent-3)" };
  if (/^ask hn:?/i.test(title)) return { label: "ASK", color: "var(--accent-5)" };
  if (/^tell hn:?/i.test(title)) return { label: "TELL", color: "var(--accent-4)" };
  if (/^launch hn:?/i.test(title)) return { label: "LAUNCH", color: "var(--accent-2)" };
  // Pure-domain hire posts are typically titled like a company name + month/year.
  if (/^[A-Z][A-Za-z0-9 .&-]+\s+\(YC\b/.test(title) && /hiring/i.test(title)) return { label: "JOB", color: "var(--accent-6)" };
  return null;
}

// Strip the type prefix from the title so the chip carries the
// label and the text stays clean.
function cleanTitle(title, type) {
  if (!type) return title;
  return title.replace(/^(Show|Ask|Tell|Launch)\s+HN:?\s*/i, "");
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

  // Score-strength bar uses the feed's max score so the ratio is
  // honest within this widget instance, a quiet new-stories feed
  // and a peak top-stories feed both scale to their own context.
  const maxScore = Math.max(1, ...stories.map((s) => Number(s.score) || 0));

  const rows = stories.map((s, i) => {
    const type = storyType(s.title);
    const title = cleanTitle(s.title, type);
    const ph = sourceIcon(s.url);
    const ago = fmtAgo(s.time);
    const score = Number(s.score) || 0;
    const scorePct = (score / maxScore) * 100;
    const scoreColor = i === 0 ? "var(--accent-1)" : "var(--accent-2)";
    const typeChip = type
      ? `<span class="hn-type" style="color:${type.color};background:color-mix(in oklab, ${type.color} 14%, var(--surface))">${type.label}</span>`
      : "";
    const subBits = [];
    if (s.comments != null) subBits.push(`<span class="hn-sub-item"><i class="ph-bold ph-chat-circle"></i>${escapeHtml(fmtScore(s.comments))}</span>`);
    if (ago) subBits.push(`<span class="hn-sub-item">${escapeHtml(ago)}</span>`);
    return `
      <div class="hn-row ${i % 2 ? "is-zebra" : ""}">
        <div class="hn-row-head">
          <div class="list-lead hn-row-lead">
            <i class="ph-bold ${ph}" style="color:var(--accent-5)"></i>
            ${typeChip}
            <span class="list-title">${escapeHtml(title)}</span>
          </div>
          <div class="hn-score-cell">
            <span class="hn-score" style="color:${scoreColor}">
              <i class="ph-bold ph-arrow-fat-up"></i>${escapeHtml(fmtScore(score))}
            </span>
            ${subBits.length ? `<small class="hn-sub">${subBits.join("")}</small>` : ""}
          </div>
        </div>
        <div class="hn-bar-track">
          <div class="hn-bar-fill" style="width:${scorePct.toFixed(1)}%;background:${scoreColor}"></div>
        </div>
      </div>`;
  }).join("");

  const layout = `
    .hn-row {
      display: flex;
      flex-direction: column;
      gap: 3px;
      padding: var(--space-2) var(--space-3);
      border-radius: var(--radius-1);
    }
    .hn-row.is-zebra {
      background: color-mix(in oklab, var(--text-primary) 3%, transparent);
    }
    .hn-row-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: var(--space-3);
      min-width: 0;
    }
    .hn-row-lead {
      flex: 1 1 auto;
      min-width: 0;
      gap: var(--space-2);
      align-items: baseline;
    }
    .hn-row-lead .list-title {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .hn-type {
      display: inline-flex;
      align-items: center;
      padding: 1px var(--space-1);
      border-radius: 999px;
      font-size: var(--fs-caption);
      font-weight: var(--fw-black);
      letter-spacing: var(--ls-label);
      flex: 0 0 auto;
    }
    .hn-score-cell {
      display: flex;
      flex-direction: column;
      align-items: flex-end;
      gap: 0;
      flex: 0 0 auto;
    }
    .hn-score {
      display: inline-flex;
      align-items: center;
      gap: 3px;
      font-weight: var(--fw-black);
      font-variant-numeric: tabular-nums;
    }
    .hn-score i {
      font-size: .85em;
    }
    .hn-sub {
      display: flex;
      gap: var(--space-2);
      color: var(--text-muted);
      font-weight: var(--fw-semi);
      font-size: .75em;
      font-variant-numeric: tabular-nums;
    }
    .hn-sub-item {
      display: inline-flex;
      align-items: center;
      gap: 2px;
    }
    .hn-sub-item i {
      font-size: .9em;
    }
    /* Score-strength bar, thin track + filled portion proportional
       to the story's score vs the feed's max. Sits at the bottom of
       the row as a tertiary signal. */
    .hn-bar-track {
      height: 3px;
      border-radius: 2px;
      background: color-mix(in oklab, var(--text-primary) 5%, transparent);
      overflow: hidden;
    }
    .hn-bar-fill {
      height: 100%;
      border-radius: 2px;
    }
    @container (max-width: 320px) {
      .hn-sub { display: none; }
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="news_hacker_news">
      <div class="w-title">
        <i class="ph-bold ph-flame" style="color:var(--accent-1)"></i>
        <h3>Hacker News</h3>
        <span class="w-title-meta">${escapeHtml(feedLabel)}</span>
      </div>
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
