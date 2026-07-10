// ha_zones, Spectra list archetype. One row per person/device
// tracker. Lead is either the person's profile picture (when HA
// exposes one) or a coloured-initials circle keyed to the person's
// name so each housemate has a stable avatar. Right-aligned meta
// pairs a zone-type glyph (house / briefcase / graduation-cap /
// barbell / etc., name-matched against the zone label) with the
// zone label itself.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function stateAccent(state) {
  if (!state) return "var(--text-secondary)";
  const s = String(state).toLowerCase();
  if (s === "home") return "var(--accent-3)";
  if (s === "not_home" || s === "away") return "var(--text-muted)";
  return "var(--accent-5)";
}

function stateLabel(state) {
  if (!state) return "-";
  if (state === "not_home") return "Away";
  if (state === "home") return "Home";
  return state;
}

// Zone-type glyph, pattern-matched against the zone label so
// `home/work/school/gym/parents/grandma/...` each pick up a
// recognisable icon. Falls back to ph-map-pin for unmatched zones.
const ZONE_ICONS = [
  [/^home$/i, "ph-house"],
  [/^away$|^not_home$/i, "ph-airplane-takeoff"],
  [/work|office|hq/i, "ph-briefcase"],
  [/school|uni|college|class|kindy/i, "ph-graduation-cap"],
  [/gym|fitness|crossfit|yoga/i, "ph-barbell"],
  [/cafe|coffee|starbucks|costa/i, "ph-coffee"],
  [/restaurant|dinner|lunch|food/i, "ph-fork-knife"],
  [/shop|store|mall|grocery|woolies|coles|aldi/i, "ph-shopping-cart"],
  [/airport|terminal/i, "ph-airplane"],
  [/hospital|clinic|doctor|dentist/i, "ph-first-aid-kit"],
  [/parents|grandma|grandpa|family/i, "ph-users-three"],
  [/park|garden/i, "ph-tree"],
  [/beach|coast|shore/i, "ph-sun-horizon"],
  [/library/i, "ph-books"],
  [/church|temple|mosque|synagogue/i, "ph-cross"],
  [/^pub$|bar\b|brewery/i, "ph-beer-stein"],
  [/cinema|movie|theatre/i, "ph-film-strip"],
];

function zoneIcon(zone) {
  for (const [re, icon] of ZONE_ICONS) {
    if (re.test(zone || "")) return icon;
  }
  return "ph-map-pin";
}

// Stable hash → one of the six categorical accent tokens. Same
// person always picks the same colour, so the initials-fallback
// avatar is consistent across renders.
function initialsColor(name) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) | 0;
  const accents = ["var(--accent-1)", "var(--accent-2)", "var(--accent-3)", "var(--accent-4)", "var(--accent-5)", "var(--accent-6)"];
  return accents[Math.abs(h) % accents.length];
}

function initials(name) {
  return (name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_zones">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Zones</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const items = Array.isArray(data.items) ? data.items : [];
  const place = data.label || "Zones";
  const summary = data.summary || {};
  const home = summary.home ?? items.filter((i) => i.state === "home").length;
  const total = summary.total ?? items.length;

  if (items.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_zones">
        <div class="w-title"><i class="ph-bold ph-users-three"></i><h3>${escapeHtml(place)}</h3></div>
        <div class="w-body"><p class="u-muted">No people tracked.</p></div>
      </div>`;
    return;
  }

  const rows = items.map((it, i) => {
    const accent = stateAccent(it.state);
    // Avatar: profile picture when HA provides one, else a coloured
    // initials circle that's stable per-person.
    const avatar = it.entity_picture
      ? `<img class="zone-avatar" src="${escapeHtml(it.entity_picture)}" alt=""/>`
      : `<span class="zone-avatar zone-avatar--init" style="background:${initialsColor(it.name)}">${escapeHtml(initials(it.name))}</span>`;
    const zPh = zoneIcon(it.state);
    return `
      <div class="zone-row ${i % 2 ? "is-zebra" : ""}">
        <div class="list-lead zone-row-lead">
          ${avatar}
          <span class="list-title">${escapeHtml(it.name)}</span>
        </div>
        <span class="zone-state" style="color:${accent}">
          <i class="ph-bold ${zPh}" style="font-size:.95em"></i>
          ${escapeHtml(stateLabel(it.state))}
        </span>
      </div>`;
  }).join("");

  const layout = `
    .zone-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--space-2);
      padding: var(--space-2) var(--space-3);
      border-radius: var(--radius-1);
      min-width: 0;
    }
    .zone-row.is-zebra {
      background: color-mix(in oklab, var(--text-primary) 3%, transparent);
    }
    .zone-row-lead {
      flex: 1 1 auto;
      min-width: 0;
      gap: var(--space-2);
    }
    .zone-row-lead .list-title {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .zone-avatar {
      width: 1.6em;
      height: 1.6em;
      border-radius: 50%;
      object-fit: cover;
      flex: 0 0 auto;
      background: var(--surface-sunken);
      display: inline-flex;
      align-items: center;
      justify-content: center;
    }
    .zone-avatar--init {
      color: var(--surface);
      font-weight: var(--fw-black);
      font-size: .7em;
      letter-spacing: 0;
    }
    .zone-state {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      font-weight: var(--fw-bold);
      letter-spacing: var(--ls-label);
      text-transform: uppercase;
      font-size: var(--fs-caption);
      flex: 0 0 auto;
    }
  `;

  // Fragments (issue #60): the Panels canvas can place just one part of the
  // widget. ``ctx.fragment`` selects which; "full" (default) is the whole
  // card. Each fragment paints self-contained, filling its own box.
  const frag = ctx?.fragment || "full";
  if (frag === "roster") {
    shadow.innerHTML = `
      ${css}
      <style>${layout}</style>
      <div class="w" data-widget="ha_zones"><div class="w-body list-body">${rows}</div></div>`;
    return;
  }
  if (frag === "count") {
    shadow.innerHTML = `
      ${css}
      <style>
        .zones-count { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; gap: var(--space-1); }
        .zones-count .n { font-size: clamp(2.4em, 40cqmin, 7em); font-weight: var(--fw-black); line-height: 1; color: var(--accent-3); font-variant-numeric: tabular-nums; }
        .zones-count .n small { color: var(--text-muted); font-size: .5em; font-weight: var(--fw-bold); }
        .zones-count .l { font-size: var(--fs-caption); font-weight: var(--fw-bold); letter-spacing: var(--ls-label); text-transform: uppercase; color: var(--text-muted); }
      </style>
      <div class="w" data-widget="ha_zones"><div class="w-body zones-count">
        <span class="n">${home}<small>/${total}</small></span>
        <span class="l">Home</span>
      </div></div>`;
    return;
  }

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="ha_zones">
      <div class="w-title">
        <i class="ph-bold ph-users-three" style="color:var(--accent-3)"></i>
        <h3>${escapeHtml(place)}</h3>
        <span class="w-title-meta">${home}/${total} HOME</span>
      </div>
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
