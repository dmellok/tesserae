// github_releases — latest tags across watched repos.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function ago(iso) {
  if (!iso) return "—";
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  if (s < 604800) return `${Math.floor(s / 86400)}d`;
  return `${Math.floor(s / 604800)}w`;
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = `
      <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
      <link rel="stylesheet" href="/plugins/github_releases/client.css">
      <div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>
    `;
    return;
  }
  const size = ctx.cell.size;
  const releases = Array.isArray(data.releases) ? data.releases : [];

  // Age tier — fresh (<14d) gets accent, recent (<60d) accent2, stale
  // (>180d) accent3, otherwise surface. Used to colour-code the pill
  // on the right side of each row.
  function ageTier(iso) {
    if (!iso) return "stale";
    const days = (Date.now() - new Date(iso).getTime()) / 86400000;
    if (days < 14)  return "fresh";
    if (days < 60)  return "recent";
    if (days < 180) return "older";
    return "stale";
  }
  function daysAgo(iso) {
    if (!iso) return "—";
    const d = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
    if (d === 0) return "today";
    if (d === 1) return "1 day";
    return `${d} days`;
  }

  // Latest-overall summary across watched repos.
  const newestIso = releases.length ? releases[0].published_at : null;
  const newestTier = ageTier(newestIso);
  const newestRepo = releases.length ? releases[0].repo : "";

  const rows = releases.map((r, i) => `
    <div class="rl-row ${i === 0 ? 'rl-row--latest' : ''}">
      <i class="ph-bold ph-tag rl-icon"></i>
      <span class="rl-repo" title="${escapeHtml(r.repo)}">${escapeHtml(r.repo)}</span>
      <span class="rl-tag">${escapeHtml(r.tag)}${r.prerelease ? ' <span class="rl-pre">pre</span>' : ""}</span>
      <span class="rl-age rl-age--${ageTier(r.published_at)}">
        <i class="ph-bold ph-clock"></i>${escapeHtml(daysAgo(r.published_at))}
      </span>
    </div>
  `).join("");

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/github_releases/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="rl-title">Releases · ${releases.length}</span>
        <i class="ph-bold ph-github-logo wb-bar-icon"></i>
      </header>
      ${releases.length ? `
        <section class="rl-summary rl-summary--${newestTier}">
          <div class="rl-summary-lbl">Newest release</div>
          <div class="rl-summary-tag">${escapeHtml(releases[0].tag || "—")}</div>
          <div class="rl-summary-meta">
            <span class="rl-summary-repo">${escapeHtml(newestRepo)}</span>
            <span class="rl-summary-age"><i class="ph-bold ph-clock"></i>${escapeHtml(daysAgo(newestIso))}</span>
          </div>
        </section>` : ""}
      <section class="rl-list">${rows || `<div class="rl-empty">No releases.</div>`}</section>
    </div>
  `;
}
