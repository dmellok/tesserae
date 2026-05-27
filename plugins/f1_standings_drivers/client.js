// f1_standings_drivers — Bauhaus driver championship table.
//
// Layout:
//   1. Inverted header bar (mark + DRIVERS + "AFTER R<n>")
//   2. Standings rows: constructor-colour stripe on left, position,
//      family name, team short, points, wins.
//
// xs: top 3.  sm: top 5.  md: top 10.  lg: top 20.

// Constructor colour swatches — the small left-edge stripe on each
// row. Falls back to fgSoft for unknown teams.
const TEAM_COLOR = {
  mercedes:       "#00d2be",
  ferrari:        "#dc0000",
  red_bull:       "#1e3a8a",
  mclaren:        "#ff8700",
  alpine:         "#fd4ba4",
  aston_martin:   "#006f62",
  williams:       "#005aff",
  rb:             "#6692ff",
  alphatauri:     "#6692ff",
  haas:           "#b6babd",
  kick_sauber:    "#52e252",
  sauber:         "#52e252",
  audi:           "#1f1f1f",
  cadillac:       "#67696c",
};

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function renderError(msg) {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/plugins/f1_standings_drivers/client.css">
    <div class="root error">
      <i class="ph ph-warning-circle" aria-hidden="true"></i>
      <span>${escapeHtml(msg)}</span>
    </div>
  `;
}

function sizeRows(size) {
  switch (size) {
    case "xs": return 3;
    case "sm": return 5;
    case "md": return 10;
    case "lg": return 20;
    default:   return 10;
  }
}

function row(s) {
  const colour = TEAM_COLOR[s.constructorId] || "var(--theme-fgSoft)";
  const isLeader = s.position === "1";
  return `
    <div class="fs-row ${isLeader ? "fs-row--leader" : ""}">
      <span class="fs-stripe" style="background:${colour}" aria-hidden="true"></span>
      <span class="fs-pos">${escapeHtml(s.position || "")}</span>
      <span class="fs-name">
        <span class="fs-family">${escapeHtml(s.family || "")}</span>
        <span class="fs-team">${escapeHtml(s.constructor || "")}</span>
      </span>
      <span class="fs-pts">${escapeHtml(s.points || "0")}</span>
      <span class="fs-wins">${Number(s.wins || 0) > 0 ? escapeHtml(s.wins) + "W" : ""}</span>
    </div>
  `;
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = renderError(data.error);
    return;
  }
  const standings = Array.isArray(data.standings) ? data.standings : [];
  if (!standings.length) {
    shadow.innerHTML = renderError("no standings yet");
    return;
  }

  const size = ctx.cell.size;
  const visible = standings.slice(0, sizeRows(size));
  const rowsHtml = visible.map(row).join("");

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/f1_standings_drivers/client.css">
    <div class="root size-${size}">
      <header class="fs-bar">
        <span class="fs-mark" aria-hidden="true"></span>
        <span class="fs-bar-label">Drivers · ${escapeHtml(data.season || "")}</span>
        <span class="fs-bar-round">${data.round ? `After R${escapeHtml(data.round)}` : ""}</span>
      </header>
      <section class="fs-rows">${rowsHtml}</section>
    </div>
  `;
}
