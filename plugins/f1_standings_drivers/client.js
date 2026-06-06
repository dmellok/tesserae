// f1_standings_drivers — Spectra list archetype, championship-led.
//
// Each driver is a row: position icon + driver code + team chip on
// the left, points + delta arrow on the right, plus a points-gap
// micro-bar below the row body showing how many points the driver
// trails the championship leader (P1's bar is full; the rest scale
// proportionally). Crown glyph specifically marks the leader to set
// it apart from race-winner trophies elsewhere in the codebase.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Canonical team colours, keyed by Ergast/Jolpica constructorId. Same
// palette the f1_last_race podium uses for visual consistency across
// the F1 family.
const TEAM_COLOR = {
  red_bull: "#3671C6",
  ferrari: "#E80020",
  mercedes: "#27F4D2",
  mclaren: "#FF8000",
  aston_martin: "#229971",
  alpine: "#FF87BC",
  williams: "#64C4FF",
  rb: "#6692FF",
  alphatauri: "#6692FF",
  sauber: "#52E252",
  alfa: "#900000",
  haas: "#B6BABD",
  audi: "#00FF00",
  cadillac: "#000000",
};

function teamColor(constructorId, constructorName) {
  if (constructorId && TEAM_COLOR[constructorId]) return TEAM_COLOR[constructorId];
  const name = String(constructorName || "").toLowerCase();
  for (const [key, hex] of Object.entries(TEAM_COLOR)) {
    if (name.includes(key.replace(/_/g, " "))) return hex;
  }
  return "var(--surface-sunken)";
}

// Delta arrow chip. Positive = moved up the table (good), negative =
// fell, zero = held position. null means we have no previous-round
// data so the chip is omitted entirely rather than guessing.
function deltaChip(delta) {
  if (delta == null) return "";
  if (delta > 0) {
    return `<span class="standings-delta is-up" title="Up ${delta}">
              <i class="ph-bold ph-caret-up"></i>${delta}
            </span>`;
  }
  if (delta < 0) {
    return `<span class="standings-delta is-down" title="Down ${Math.abs(delta)}">
              <i class="ph-bold ph-caret-down"></i>${Math.abs(delta)}
            </span>`;
  }
  return `<span class="standings-delta is-same" title="No change">
            <i class="ph-bold ph-minus"></i>
          </span>`;
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

  // Crown for the championship leader (P1). Medal for the rest of the
  // podium (P2/P3). The trophy glyph stays specific to race wins
  // (f1_last_race) so the icon vocabulary across the F1 family reads
  // crown = championship lead, trophy = race victory.
  const POSITION_ICON = ["ph-crown", "ph-medal", "ph-medal"];
  const POSITION_ACCENT = [
    "var(--accent-2)",       // gold leader
    "var(--text-secondary)", // silver P2
    "var(--accent-1)",       // bronze P3
  ];

  // Leader's points anchor the micro-bar scale. Avoid divide-by-zero
  // at the start of a season where the leader can still be at 0
  // points. Defaults to 1 in that case so the bar collapses cleanly
  // rather than NaN-rendering.
  const leaderPoints = Math.max(1, Number(standings[0]?.points) || 0);

  const rows = standings.map((s, i) => {
    const isLeader = i === 0;
    const accent = isLeader ? "var(--accent-2)" : "var(--text-secondary)";
    const posIcon = POSITION_ICON[i];
    const posIconColor = POSITION_ACCENT[i];
    const team = teamColor(s.constructorId, s.constructor);
    const points = Number(s.points) || 0;
    const pctOfLeader = Math.max(0, Math.min(100, (points / leaderPoints) * 100));
    const delta = deltaChip(s.delta);
    return `
      <div class="standings-row ${i % 2 ? "is-zebra" : ""}" style="--team:${team}">
        <span class="standings-pos">
          ${posIcon
            ? `<i class="ph-bold ${posIcon}" style="color:${posIconColor}"></i>`
            : `<span class="standings-pos-num" style="color:${accent}">${escapeHtml(String(s.position || i + 1))}</span>`}
        </span>
        <div class="standings-body">
          <div class="standings-head">
            <span class="standings-code">${escapeHtml(s.code || s.family || "—")}</span>
            ${s.constructor ? `<span class="standings-team" style="color:${team}">${escapeHtml(s.constructor)}</span>` : ""}
            ${delta}
            <span class="standings-points">
              ${escapeHtml(String(s.points ?? "0"))}<small>PTS</small>
            </span>
          </div>
          <div class="standings-bar" aria-hidden="true">
            <div class="standings-bar-fill" style="width:${pctOfLeader.toFixed(1)}%;background:${team}"></div>
          </div>
        </div>
      </div>`;
  }).join("");

  const layout = `
    .standings-list {
      display: flex;
      flex-direction: column;
      gap: var(--space-1);
      min-height: 0;
      overflow: hidden;
    }
    .standings-row {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      gap: var(--space-3);
      padding: var(--space-2) var(--space-3);
      border-radius: var(--radius-0);
      align-items: center;
      box-shadow: inset 4px 0 0 0 var(--team);
    }
    .standings-row.is-zebra { background: var(--surface-sunken); }
    .standings-pos {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 1.6em;
      font-size: var(--icon-md);
    }
    .standings-pos .ph-bold { line-height: 1; }
    .standings-pos-num {
      font-size: var(--fs-body);
      font-weight: var(--fw-black);
      font-variant-numeric: tabular-nums;
    }
    .standings-body {
      display: flex;
      flex-direction: column;
      gap: 0.25em;
      min-width: 0;
    }
    .standings-head {
      display: flex;
      align-items: baseline;
      gap: var(--space-2);
      min-width: 0;
    }
    .standings-code {
      font-weight: var(--fw-black);
      font-size: var(--fs-body);
      letter-spacing: var(--ls-tight);
      flex: 0 0 auto;
    }
    .standings-team {
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      letter-spacing: var(--ls-label);
      text-transform: var(--label-transform, uppercase);
      flex: 1 1 auto;
      min-width: 0;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .standings-points {
      font-size: var(--fs-body);
      font-weight: var(--fw-black);
      font-variant-numeric: tabular-nums;
      flex: 0 0 auto;
      margin-left: auto;
    }
    .standings-points small {
      font-size: 0.55em;
      color: var(--text-muted);
      font-weight: var(--fw-bold);
      margin-left: 0.2em;
      letter-spacing: var(--ls-label);
    }
    /* Position-change chip — up arrow + count for gains, down arrow +
       count for losses, dash for "held". Coloured by direction so a
       scan picks out who moved without reading the number. */
    .standings-delta {
      display: inline-flex;
      align-items: center;
      gap: 0.1em;
      padding: 0.1em 0.4em;
      border-radius: var(--pill-radius, var(--radius-0));
      font-size: var(--fs-caption);
      font-weight: var(--fw-black);
      letter-spacing: 0.02em;
      font-variant-numeric: tabular-nums;
      flex: 0 0 auto;
      line-height: 1.1;
    }
    .standings-delta .ph-bold { font-size: 0.95em; line-height: 1; }
    .standings-delta.is-up {
      color: var(--accent-3);
      background: color-mix(in oklab, var(--accent-3) 14%, transparent);
    }
    .standings-delta.is-down {
      color: var(--accent-1);
      background: color-mix(in oklab, var(--accent-1) 14%, transparent);
    }
    .standings-delta.is-same {
      color: var(--text-muted);
      background: color-mix(in oklab, var(--text-muted) 12%, transparent);
    }
    /* Points-gap micro-bar — leader paints full width in their team
       colour; everyone else's bar scales to their share of the
       leader's points so a glance reads the championship gap. */
    .standings-bar {
      width: 100%;
      height: var(--stroke-3);
      background: color-mix(in oklab, var(--text-primary) 8%, transparent);
      overflow: hidden;
    }
    .standings-bar-fill { height: 100%; }

    /* xs / sm: drop the team name and delta chip so the row stays a
       single tight line of pos + code + points + bar. */
    @container (max-width: 360px) {
      .standings-team { display: none; }
      .standings-delta { display: none; }
    }
    /* lg: bigger rows + chunkier bars. */
    @container (min-width: 700px) {
      .standings-row { padding: var(--space-3) var(--space-4); }
      .standings-bar { height: calc(var(--stroke-3) * 1.4); }
      .standings-code { font-size: var(--fs-lead); }
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="f1_standings_drivers">
      <div class="w-title">
        <i class="ph-bold ph-crown" style="color:var(--accent-2)"></i>
        <h3>Standings</h3>
        ${data.season ? `<span class="w-title-meta">${escapeHtml(String(data.season))}${data.round ? ` · R${data.round}` : ""}</span>` : ""}
      </div>
      <div class="w-body">
        <div class="standings-list">${rows}</div>
      </div>
    </div>`;
}
