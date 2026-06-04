// f1_standings_drivers — Spectra list archetype. Each driver is a
// zebra row with the position number on the left (accent-2 for the
// championship leader), the driver code + family name as the title,
// and the points total right-aligned.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="f1_standings_drivers">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Standings</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const standings = Array.isArray(data.standings) ? data.standings : [];

  if (standings.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="f1_standings_drivers">
        <div class="w-title"><i class="ph-bold ph-trophy" style="color:var(--accent-2)"></i><h3>Standings</h3></div>
        <div class="w-body"><p class="u-muted">No standings yet.</p></div>
      </div>`;
    return;
  }

  // Trophy / medal icons mark the top three: gold trophy for the
  // championship leader, silver + bronze medals for second and third.
  // Positions four and down keep just the number so the icon
  // vocabulary stays a podium signal instead of decoration.
  const PODIUM_ICON = ["ph-trophy", "ph-medal", "ph-medal"];
  const PODIUM_ACCENT = ["var(--accent-2)", "var(--text-secondary)", "var(--accent-1)"];
  const rows = standings.map((s, i) => {
    const isLeader = i === 0;
    const accent = isLeader ? "var(--accent-2)" : "var(--text-secondary)";
    const posIcon = PODIUM_ICON[i];
    const posIconColor = PODIUM_ACCENT[i];
    const posStyle = `width:1.4em;text-align:center;font-weight:var(--fw-black);color:${accent}`;
    return `
      <div class="list-row ${i % 2 ? "is-zebra" : ""}">
        <div class="list-lead">
          ${posIcon
            ? `<i class="ph-bold ${posIcon}" style="font-size:1.1em;color:${posIconColor};line-height:1"></i>`
            : `<span style="${posStyle}">${escapeHtml(String(s.position || i + 1))}</span>`}
          <span class="list-title">${escapeHtml(s.code || s.family || "—")}${s.constructor ? `<small class="u-muted" style="font-weight:var(--fw-semi);font-size:.7em;margin-left:.4em"><i class="ph-bold ph-wrench" style="font-size:.85em;margin-right:.2em;vertical-align:-.05em"></i>${escapeHtml(s.constructor)}</small>` : ""}</span>
        </div>
        <span class="list-meta" style="color:${accent}">${escapeHtml(s.points || "0")}<small style="font-size:.6em;color:var(--text-muted);font-weight:var(--fw-bold);margin-left:.2em">PTS</small></span>
      </div>`;
  }).join("");

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="f1_standings_drivers">
      <div class="w-title">
        <i class="ph-bold ph-trophy" style="color:var(--accent-2)"></i>
        <h3>Standings</h3>
        ${data.season ? `<span class="w-title-meta">${escapeHtml(String(data.season))}${data.round ? ` · R${data.round}` : ""}</span>` : ""}
      </div>
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
