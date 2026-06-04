// github_releases — Spectra list archetype. Each row leads with a
// version-tag icon (accent-2 stable / muted prerelease / accent-1
// draft), the release name + repo as the title, and the tag as
// right-aligned meta.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function repoShort(name) {
  if (typeof name !== "string") return "";
  const slash = name.lastIndexOf("/");
  return slash >= 0 ? name.slice(slash + 1) : name;
}

function fmtAgo(iso) {
  if (typeof iso !== "string" || !iso) return "";
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return "";
  const days = Math.floor((Date.now() - t) / (1000 * 60 * 60 * 24));
  if (days < 1) return "today";
  if (days < 7) return `${days}d`;
  if (days < 30) return `${Math.floor(days / 7)}w`;
  if (days < 365) return `${Math.floor(days / 30)}mo`;
  return `${Math.floor(days / 365)}y`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="github_releases">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Releases</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const releases = Array.isArray(data.releases) ? data.releases : [];

  if (releases.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="github_releases">
        <div class="w-title">
          <i class="ph-bold ph-tag" style="color:var(--accent-2)"></i>
          <h3>Releases</h3>
        </div>
        <div class="w-body"><p class="u-muted">No releases.</p></div>
      </div>`;
    return;
  }

  const rows = releases.map((r, i) => {
    const accent = r.draft ? "var(--accent-1)" : r.prerelease ? "var(--text-muted)" : "var(--accent-2)";
    const ago = fmtAgo(r.published_at);
    const repoBit = `<small class="u-muted" style="font-weight:var(--fw-semi);font-size:.7em;margin-left:.4em">${escapeHtml(repoShort(r.repo))}</small>`;
    const tagBadge = r.draft
      ? `<small style="font-size:.65em;color:var(--accent-1);font-weight:var(--fw-black);margin-right:.4em">DRAFT</small>`
      : r.prerelease
        ? `<small style="font-size:.65em;color:var(--text-muted);font-weight:var(--fw-black);margin-right:.4em">PRE</small>`
        : "";
    return `
      <div class="list-row ${i % 2 ? "is-zebra" : ""}">
        <div class="list-lead">
          <i class="ph-bold ph-tag" style="color:${accent}"></i>
          <span class="list-title">${escapeHtml(r.name || r.tag)}${repoBit}</span>
        </div>
        <span class="list-meta" style="color:${accent}">${tagBadge}${escapeHtml(r.tag)}${ago ? `<small style="font-size:.7em;color:var(--text-muted);font-weight:var(--fw-semi);margin-left:.3em">${ago}</small>` : ""}</span>
      </div>`;
  }).join("");

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="github_releases">
      <div class="w-title">
        <i class="ph-bold ph-tag" style="color:var(--accent-2)"></i>
        <h3>Releases</h3>
        <span class="w-title-meta">${releases.length}</span>
      </div>
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
