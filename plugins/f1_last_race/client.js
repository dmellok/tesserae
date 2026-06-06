// f1_last_race — Spectra status archetype with a proper F1 podium as
// the visual centrepiece. Three coloured steps in P2-P1-P3 visual
// order, heights stepping up to the middle (P1 tallest), each tinted
// by the driver's constructor colour. Driver code + team + race
// time/gap sit above each step. Trophy glyph hangs above the winner's
// code; fastest-lap lightning hangs above whichever driver set it.
// Circuit outline still occupies the right column from f1_core's
// bundled circuits.json.

import { getCircuit, trackSvg } from "../f1_core/static/circuits.js";

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// constructorId → team colour. Roughly the official 2024/25 livery
// colours, sourced as plain hex so the steps read the same on every
// Spectra theme (theme accents would shift between light/dark and
// pull the visual identity away from "this is Ferrari red"). Unknown
// constructors fall through to the text-secondary token so a future
// team without a mapping still gets a visible block.
const TEAM_COLORS = {
  ferrari: "#E8002D",
  mercedes: "#27F4D2",
  red_bull: "#3671C6",
  mclaren: "#FF8000",
  aston_martin: "#229971",
  williams: "#64C4FF",
  alpine: "#FF87BC",
  haas: "#B6BABD",
  rb: "#6692FF",
  alphatauri: "#5E8FAA",
  kick_sauber: "#52E252",
  sauber: "#52E252",
  alfa: "#900000",
};

function teamColor(id) {
  return TEAM_COLORS[String(id || "").toLowerCase()] || "var(--text-secondary)";
}

// Render one podium block. place is 1/2/3 (the finishing order), and
// the block's grid-order maps that to its visual slot (P2 left, P1
// centre, P3 right) so the row lands in the iconic podium shape.
function podiumBlock(p, place) {
  if (!p) return `<div class="podium-block podium-block--empty" data-place="${place}"></div>`;
  const code = p.code || `${(p.given || "")[0] || ""}${(p.family || "")[0] || ""}` || "—";
  const team = p.constructor || "";
  const time = p.time || "";
  const color = teamColor(p.constructorId);
  const fastest = !!p.fastest;
  return `
    <div class="podium-block" data-place="${place}" style="--team:${color}">
      <div class="podium-info">
        <div class="podium-glyphs">
          ${place === 1 ? `<i class="ph-bold ph-trophy podium-trophy" aria-label="Winner"></i>` : ""}
          ${fastest ? `<i class="ph-bold ph-lightning podium-fastest" aria-label="Fastest lap"></i>` : ""}
        </div>
        <span class="podium-code">${escapeHtml(code)}</span>
        <span class="podium-team">${escapeHtml(team)}</span>
        ${time ? `<span class="podium-time">${escapeHtml(time)}</span>` : ""}
      </div>
      <div class="podium-step">
        <span class="podium-place">P${place}</span>
      </div>
    </div>`;
}

export default async function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="f1_last_race">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Last Race</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const podium = Array.isArray(data.podium) ? data.podium.slice(0, 3) : [];
  const country = data.country || "";
  const circuitName = data.circuitName || "";
  const locality = data.locality || "";
  // Meta line as discrete spans so container queries can drop pieces
  // (and their trailing separators) progressively at narrower cells.
  // Trailing sep is paired with each item so hiding the item also
  // hides the dangling " · " that would otherwise lead the next one.
  const metaParts = [];
  if (circuitName) {
    metaParts.push(`<span class="f1-meta-circuit">${escapeHtml(circuitName)}</span>`);
    if (locality || country) metaParts.push(`<span class="f1-meta-sep">·</span>`);
  }
  if (locality) {
    metaParts.push(`<span class="f1-meta-locality">${escapeHtml(locality)}</span>`);
    if (country) metaParts.push(`<span class="f1-meta-sep">·</span>`);
  }
  if (country) {
    metaParts.push(`<span class="f1-meta-country">${escapeHtml(country)}</span>`);
  }
  const metaHtml = metaParts.join("");

  let track = null;
  try { track = await getCircuit(data.circuitId); } catch { track = null; }

  // Visual order P2-P1-P3 via grid order — the actual podium shape.
  // podiumBlock handles missing entries (returns an empty cell so the
  // grid stays balanced).
  const p1 = podium.find((p) => Number(p.position) === 1) || podium[0];
  const p2 = podium.find((p) => Number(p.position) === 2) || podium[1];
  const p3 = podium.find((p) => Number(p.position) === 3) || podium[2];
  const blocks = `
    ${podiumBlock(p2, 2)}
    ${podiumBlock(p1, 1)}
    ${podiumBlock(p3, 3)}
  `;

  const layout = `
    .f1-body {
      flex: 1 1 auto;
      min-height: 0;
      display: grid;
      grid-template-columns: 1fr;
      gap: var(--space-4);
      align-items: stretch;
    }
    .f1-data {
      display: flex;
      flex-direction: column;
      gap: var(--space-3);
      min-height: 0;
      min-width: 0;
      justify-content: flex-end;
    }
    /* Circuit cell hidden by default; only LG cells get the track
       silhouette. The SVG has no intrinsic dimensions (viewBox-only)
       so its container must give it explicit width + height with
       preserveAspectRatio handling the letterboxing. */
    .f1-track {
      display: none;
      color: var(--accent-2);
      overflow: hidden;
      align-items: center;
      justify-content: center;
    }
    .f1-track svg {
      width: 100%;
      height: 100%;
      display: block;
    }

    /* Podium proper — three flex-column blocks in P2 / P1 / P3 visual
       order. Block uses justify-content: flex-end so the driver +
       step + meta stack push to the bottom of the cell, giving the
       iconic "tallest block centre, lower blocks flanking" silhouette
       even when the .podium row is much taller than the stack needs. */
    .podium {
      flex: 0 0 auto;
      display: grid;
      grid-template-columns: 1fr 1fr 1fr;
      align-items: end;
      gap: var(--space-2);
      min-width: 0;
    }
    .podium-block {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: var(--space-1);
      text-align: center;
      min-width: 0;
    }
    .podium-block[data-place="2"] { order: 1; }
    .podium-block[data-place="1"] { order: 2; }
    .podium-block[data-place="3"] { order: 3; }
    .podium-block--empty .podium-step { background: var(--surface-sunken); }

    .podium-info {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 0.1em;
      min-width: 0;
      max-width: 100%;
    }
    .podium-glyphs {
      display: flex;
      align-items: center;
      gap: 0.35em;
      min-height: 1em;
    }
    .podium-trophy { color: var(--accent-2); font-size: 1.2em; }
    .podium-fastest { color: var(--accent-6); font-size: 1.1em; }
    .podium-code {
      font-size: clamp(1.1em, 5cqmin, 2.2em);
      font-weight: var(--fw-black);
      line-height: 1;
      letter-spacing: var(--ls-tight);
      color: var(--text-primary);
    }
    .podium-team {
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      letter-spacing: var(--ls-label);
      text-transform: var(--label-transform, uppercase);
      color: var(--text-secondary);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 100%;
    }
    .podium-time {
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      color: var(--text-muted);
      font-variant-numeric: tabular-nums;
    }
    /* The step — a coloured bar whose height varies by place so the
       three form the podium silhouette. Heights against cqmin keep
       all three scaling uniformly on small cells. */
    .podium-step {
      width: 100%;
      background: var(--team);
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--surface);
      flex: 0 0 auto;
    }
    .podium-block[data-place="1"] .podium-step { height: clamp(2em, 12cqmin, 5em); }
    .podium-block[data-place="2"] .podium-step { height: clamp(1.5em, 9cqmin, 3.8em); }
    .podium-block[data-place="3"] .podium-step { height: clamp(1em, 6cqmin, 2.8em); }
    .podium-place {
      font-size: var(--fs-label);
      font-weight: var(--fw-black);
      letter-spacing: var(--ls-label);
      text-transform: var(--label-transform, uppercase);
    }

    /* Location meta line — pin icon + circuit / locality / country.
       Built as a flex row with a text wrapper that absorbs ellipsis
       overflow, plus container-query rules below that drop the less
       essential bits at narrower cells so the line doesn't keep
       overflowing into a clipped second-row. */
    .f1-meta {
      flex: 0 0 auto;
      display: flex;
      align-items: baseline;
      gap: 0.4em;
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      letter-spacing: var(--ls-label);
      text-transform: var(--label-transform, uppercase);
      color: var(--text-muted);
      align-self: center;
      min-width: 0;
      max-width: 100%;
    }
    .f1-meta .ph-bold { color: var(--accent-1); font-size: 1.1em; flex: 0 0 auto; }
    .f1-meta-text {
      flex: 0 1 auto;
      min-width: 0;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .f1-meta-text > * { margin-right: 0.4em; }
    .f1-meta-text > *:last-child { margin-right: 0; }
    .f1-meta-sep { color: var(--text-muted); opacity: 0.55; }

    /* Progressive shedding of meta-line elements so the line doesn't
       overflow and get clipped at the cell boundary. Trailing sep
       pairs with each item (hidden together) so a stray " · " doesn't
       lead the remainder. */
    /* md or smaller: drop the circuit name (longest) + its sep. */
    @container (max-width: 699px) {
      .f1-meta-circuit,
      .f1-meta-circuit + .f1-meta-sep { display: none; }
    }
    /* sm or smaller: drop country + the locality's trailing sep. */
    @container (max-width: 440px) {
      .f1-meta-country,
      .f1-meta-locality + .f1-meta-sep { display: none; }
    }
    /* xs: drop the meta entirely + team/time, podium fills the body. */
    @container (max-width: 280px) {
      .f1-meta { display: none; }
      .podium-team { display: none; }
      .podium-time { display: none; }
    }
    /* lg: bring the circuit back, side-by-side with the podium
       column. Bigger codes + steps, capped modestly so a wide cell
       doesn't blow the 3-letter codes past their column. */
    @container (min-width: 700px) {
      .f1-body { grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr); }
      .f1-track { display: flex; }
      .podium-code { font-size: clamp(1.5em, 6cqmin, 3em); }
      .podium-block[data-place="1"] .podium-step { height: clamp(3em, 16cqmin, 7em); }
      .podium-block[data-place="2"] .podium-step { height: clamp(2.2em, 12cqmin, 5em); }
      .podium-block[data-place="3"] .podium-step { height: clamp(1.6em, 8cqmin, 3.6em); }
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="f1_last_race">
      <div class="w-title">
        <i class="ph-bold ph-flag-checkered" style="color:var(--accent-1)"></i>
        <h3>${escapeHtml(data.raceName || "Last Race")}</h3>
        ${data.round ? `<span class="w-title-meta">R${escapeHtml(String(data.round))}</span>` : ""}
      </div>
      <div class="w-body">
        <div class="f1-body">
          <div class="f1-data">
            <div class="podium">${blocks}</div>
            ${metaHtml ? `<div class="f1-meta"><i class="ph-bold ph-map-pin"></i><span class="f1-meta-text">${metaHtml}</span></div>` : ""}
          </div>
          ${track ? `<div class="f1-track">${trackSvg(track, { stroke: "var(--accent-2)" })}</div>` : ""}
        </div>
      </div>
    </div>`;
}
