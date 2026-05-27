// github_repo — single repo at a glance.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function compact(n) {
  if (n == null) return "—";
  const abs = Math.abs(n);
  if (abs >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (abs >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(n);
}

function ago(iso) {
  if (!iso) return "—";
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = `
      <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
      <link rel="stylesheet" href="/plugins/github_repo/client.css">
      <div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>
    `;
    return;
  }
  const size = ctx.cell.size;
  const [owner, name] = (data.repo || "/").split("/");

  // Language stacked bar — cycle the theme's decorative palette so it
  // reads as part of the theme rather than a random hex grab.
  const langClasses = ["lang-c1", "lang-c2", "lang-c3", "lang-c4", "lang-c5", "lang-c6"];
  const langs = Array.isArray(data.languages) ? data.languages : [];
  const langBar = langs.length ? `
    <div class="gr-langs">
      <div class="gr-langbar">
        ${langs.map((l, i) => `<span class="gr-langseg ${langClasses[i % langClasses.length]}" style="width:${l.pct}%" title="${escapeHtml(l.name)} · ${l.pct}%"></span>`).join("")}
      </div>
      <div class="gr-langlegend">
        ${langs.map((l, i) => `<span class="gr-langchip"><span class="gr-langdot ${langClasses[i % langClasses.length]}"></span>${escapeHtml(l.name)} ${l.pct}%</span>`).join("")}
      </div>
    </div>
  ` : "";

  // 52-week commit-activity sparkline. Normalised against the busiest
  // week so quiet repos still show shape.
  const weeks = Array.isArray(data.commit_weeks) ? data.commit_weeks : [];
  const peak = data.busiest_week || Math.max(1, ...weeks, 1);
  const sparkBars = weeks.map((c) => {
    const h = Math.max(6, Math.min(100, (c / peak) * 100));
    return `<span class="gr-wbar" style="height:${h}%" title="${c} commits"></span>`;
  }).join("");
  const sparkBlock = weeks.length ? `
    <section class="gr-spark">
      <div class="gr-spark-head">
        <span>52-week commit activity</span>
        <span class="gr-spark-total"><i class="ph-bold ph-git-commit"></i>${compact(data.commits_year)}</span>
      </div>
      <div class="gr-spark-bars">${sparkBars}</div>
    </section>
  ` : "";

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/github_repo/client.css">
    <div class="root size-${size}">
      <header class="gr-bar">
        <span class="gr-mark" aria-hidden="true"></span>
        <span class="gr-title">${escapeHtml(owner)}<span class="gr-slash">/</span>${escapeHtml(name)}</span>
        <i class="ph-bold ph-github-logo gr-bar-icon"></i>
      </header>
      <section class="gr-body">
        ${data.description ? `<p class="gr-desc">${escapeHtml(data.description)}</p>` : ""}
        <div class="gr-meta">
          ${data.language ? `<span class="gr-tag"><span class="gr-dot"></span>${escapeHtml(data.language)}</span>` : ""}
          ${data.license ? `<span class="gr-tag"><i class="ph ph-scales"></i>${escapeHtml(data.license)}</span>` : ""}
          ${data.is_archived ? `<span class="gr-tag gr-tag--warn"><i class="ph ph-archive"></i>archived</span>` : ""}
        </div>
        ${langBar}
      </section>
      <section class="gr-stats">
        <div class="gr-stat gr-stat--accent"><i class="ph-bold ph-star"></i><span class="gr-v">${compact(data.stars)}</span><span class="gr-l">Stars</span></div>
        <div class="gr-stat gr-stat--surface"><i class="ph-bold ph-git-fork"></i><span class="gr-v">${compact(data.forks)}</span><span class="gr-l">Forks</span></div>
        <div class="gr-stat gr-stat--accent2"><i class="ph-bold ph-warning-circle"></i><span class="gr-v">${compact(data.issues)}</span><span class="gr-l">Issues</span></div>
        <div class="gr-stat gr-stat--accent3"><i class="ph-bold ph-tag"></i><span class="gr-v">${escapeHtml(data.latest_release || "—")}</span><span class="gr-l">Release</span></div>
      </section>
      ${sparkBlock}
      <footer class="gr-foot">
        <i class="ph ph-clock-clockwise"></i>last push ${escapeHtml(ago(data.pushed_at))} · ${escapeHtml(data.default_branch)}
      </footer>
    </div>
  `;
}
