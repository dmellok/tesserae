// news_rss, Spectra list archetype. Title bar shows the feed's own
// title with an "RSS" identifier. Each row carries a source-host chip
// (host initial + hash-stable accent tint) on the left + a published
// chip on the right, plus a type-aware lead glyph (video / audio /
// image / article).

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

function hostOf(url) {
  if (typeof url !== "string") return "";
  try {
    const u = new URL(url);
    return u.hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

// Source-host → leading glyph. Mixed feeds with podcasts / videos /
// articles read more clearly when each row carries a recognisable
// type icon than identical RSS squares.
function sourceIcon(url) {
  if (typeof url !== "string") return "ph-article";
  const u = url.toLowerCase();
  if (u.includes("youtube.com") || u.includes("youtu.be") || /\.(mp4|mov|webm)(\?|$)/.test(u)) return "ph-play";
  if (/\.(mp3|m4a|ogg)(\?|$)/.test(u) || u.includes("podcast")) return "ph-microphone";
  if (u.includes("github.com")) return "ph-github-logo";
  if (/\.(jpe?g|png|gif|webp)(\?|$)/.test(u)) return "ph-image";
  return "ph-article";
}

// Hash a host name → one of six accent tokens. Same host always
// picks the same colour, so a list mixing TechCrunch + The Verge
// reads as two visually-distinct streams.
function hostColor(host) {
  let h = 0;
  for (let i = 0; i < host.length; i++) h = (h * 31 + host.charCodeAt(i)) | 0;
  const accents = ["var(--accent-1)", "var(--accent-2)", "var(--accent-3)", "var(--accent-4)", "var(--accent-5)", "var(--accent-6)"];
  return accents[Math.abs(h) % accents.length];
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const opts = ctx?.cell?.options ?? {};
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

  // Article preview: 0 (off) through 3 lines, clamped by CSS on the rendered
  // width rather than a character count, so the text fills whatever room the
  // cell actually has. Feeds that ship no usable summary (the server drops
  // link-dump descriptions) simply render the headline-only row.
  const excerptLines = Math.max(0, Math.min(3, Number(opts.excerpt_lines) || 0));

  const rows = items.map((it, i) => {
    const host = hostOf(it.url);
    const initial = (host.match(/[a-z]/i) || ["?"])[0].toUpperCase();
    const color = host ? hostColor(host) : "var(--accent-2)";
    const ph = sourceIcon(it.url);
    const ago = fmtPublished(it.published);
    const excerpt = excerptLines > 0 && it.excerpt
      ? `<p class="rss-excerpt" style="-webkit-line-clamp:${excerptLines};line-clamp:${excerptLines}">${escapeHtml(it.excerpt)}</p>`
      : "";
    return `
      <div class="rss-row ${i % 2 ? "is-zebra" : ""}">
        <div class="rss-row-head">
          <div class="list-lead rss-row-lead">
            <span class="rss-source" style="background:${color}" title="${escapeHtml(host)}">${escapeHtml(initial)}</span>
            <i class="ph-bold ${ph} rss-type" style="color:${color}"></i>
            <span class="list-title">${escapeHtml(it.title)}</span>
          </div>
          ${ago ? `<span class="rss-ago" title="${escapeHtml(it.published)}">${escapeHtml(ago)}</span>` : ""}
        </div>
        ${excerpt}
      </div>`;
  }).join("");

  const layout = `
    .rss-row {
      display: flex;
      flex-direction: column;
      gap: 2px;
      padding: var(--space-2) var(--space-3);
      border-radius: var(--radius-1);
      min-width: 0;
    }
    .rss-row-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--space-2);
      min-width: 0;
    }
    /* Article preview under the headline. Clamped to the option's line
       count; the browser breaks on word boundaries and ellipsises the
       overflow, so the text ends where the cell runs out rather than at a
       guessed character. Indented to the headline's text, past the source
       chip and type icon. */
    .rss-excerpt {
      margin: 0;
      padding-left: calc(var(--space-2) * 2 + 2.1em);
      font-size: calc(var(--fs-caption) * 0.95);
      line-height: 1.32;
      color: var(--text-secondary);
      display: -webkit-box;
      -webkit-box-orient: vertical;
      white-space: normal;
      overflow: hidden;
      word-break: break-word;
    }
    .rss-row.is-zebra {
      background: color-mix(in oklab, var(--text-primary) 3%, transparent);
    }
    .rss-row-lead {
      flex: 1 1 auto;
      min-width: 0;
      gap: var(--space-2);
    }
    .rss-row-lead .list-title {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .rss-source {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 1.4em;
      height: 1.4em;
      border-radius: 4px;
      color: var(--surface);
      font-weight: var(--fw-black);
      font-size: .8em;
      letter-spacing: 0;
      flex: 0 0 auto;
    }
    .rss-type {
      font-size: .9em;
      flex: 0 0 auto;
    }
    .rss-ago {
      color: var(--text-muted);
      font-weight: var(--fw-bold);
      font-size: var(--fs-caption);
      font-variant-numeric: tabular-nums;
      letter-spacing: var(--ls-label);
      flex: 0 0 auto;
    }
    @container (max-width: 280px) {
      .rss-type { display: none; }
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="news_rss">
      <div class="w-title">
        <i class="ph-bold ph-rss" style="color:var(--accent-2)"></i>
        <h3>${escapeHtml(title)}</h3>
        <span class="w-title-meta">RSS</span>
      </div>
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
