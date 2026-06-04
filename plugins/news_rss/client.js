// news_rss — Spectra list archetype. Title bar takes the feed's own
// title with a "RSS" identifier meta; each item is a zebra row
// showing the headline + the published date.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtPublished(iso) {
  if (typeof iso !== "string" || !iso) return "";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return iso;
  const secs = Math.max(0, (Date.now() - t) / 1000);
  if (secs < 3600) return `${Math.max(1, Math.floor(secs / 60))}m`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h`;
  if (secs < 604800) return `${Math.floor(secs / 86400)}d`;
  if (secs < 2592000) return `${Math.floor(secs / 604800)}w`;
  return `${Math.floor(secs / 2592000)}mo`;
}

// Pick a leading icon based on the link's host / extension so a mixed
// feed visually distinguishes a podcast / video / article. Falls
// back to the generic ph-rss when nothing matches.
function sourceIcon(url) {
  if (typeof url !== "string") return "ph-rss";
  const u = url.toLowerCase();
  if (u.includes("youtube.com") || u.includes("youtu.be") || u.endsWith(".mp4") || u.endsWith(".mov")) return "ph-play";
  if (u.endsWith(".mp3") || u.includes(".m4a") || u.includes("podcast")) return "ph-microphone";
  if (u.includes("github.com")) return "ph-github-logo";
  if (u.endsWith(".jpg") || u.endsWith(".png") || u.endsWith(".gif") || u.endsWith(".webp")) return "ph-image";
  return "ph-rss";
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="news_rss">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Feed</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const items = Array.isArray(data.items) ? data.items : [];
  const title = data.feed_title || "Feed";

  if (items.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="news_rss">
        <div class="w-title">
          <i class="ph-bold ph-rss" style="color:var(--accent-2)"></i>
          <h3>${escapeHtml(title)}</h3>
        </div>
        <div class="w-body"><p class="u-muted">No items.</p></div>
      </div>`;
    return;
  }

  const rows = items.map((it, i) => `
    <div class="list-row ${i % 2 ? "is-zebra" : ""}">
      <div class="list-lead">
        <i class="ph-bold ${sourceIcon(it.url)}" style="color:var(--accent-2)"></i>
        <span class="list-title">${escapeHtml(it.title)}</span>
      </div>
      <span class="list-meta u-muted" style="font-weight:var(--fw-semi);font-feature-settings:'tnum'">${escapeHtml(fmtPublished(it.published))}</span>
    </div>`).join("");

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="news_rss">
      <div class="w-title">
        <i class="ph-bold ph-rss" style="color:var(--accent-2)"></i>
        <h3>${escapeHtml(title)}</h3>
        <span class="w-title-meta">RSS</span>
      </div>
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
