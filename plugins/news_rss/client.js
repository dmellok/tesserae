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
  // Strip the time portion if present so rows read tightly.
  if (iso.includes("T")) {
    const [date] = iso.split("T");
    const [y, mo, d] = date.split("-").map(Number);
    if (Number.isNaN(y)) return "";
    const today = new Date();
    const isToday = today.getFullYear() === y && today.getMonth() + 1 === mo && today.getDate() === d;
    if (isToday) return iso.split("T")[1].slice(0, 5);
    return `${String(d).padStart(2, "0")}/${String(mo).padStart(2, "0")}`;
  }
  return iso;
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
        <i class="ph-bold ph-rss" style="color:var(--accent-2)"></i>
        <span class="list-title">${escapeHtml(it.title)}</span>
      </div>
      <span class="list-meta u-muted" style="font-weight:var(--fw-semi)">${escapeHtml(fmtPublished(it.published))}</span>
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
