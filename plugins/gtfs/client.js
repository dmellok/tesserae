// gtfs, approaching vehicles at one stop.
//
// Two archetypes, picked by cell size, because the information shape
// genuinely changes:
//
//   xs  .stat-body , minutes to the next vehicle. Nothing else fits.
//   sm  .stat-body , same hero plus the route badge + where it's going.
//   md  .list-body , 3 upcoming arrivals.
//   lg  .list-body , 6 upcoming arrivals, bigger type.
//
// Colour is by urgency role, not hue: accent-1 for "it's here / run",
// accent-2 for "soon", accent-3 for "you have time". Route badges paint
// the agency's own route_color when the feed ships one (the data-identity
// carve-out in docs/widgets.md, same rule as F1 team colours) and fall
// back to --surface-sunken. The live marker uses accent-4, the documented
// "live" slot.

const MODE_PH = {
  tram: "ph-tram",
  subway: "ph-subway",
  rail: "ph-train",
  bus: "ph-bus",
  ferry: "ph-boat",
  gondola: "ph-cable-car",
  funicular: "ph-train-regional",
  monorail: "ph-train-simple",
};

// How many arrivals each size shows. xs/sm are hero-only by definition;
// md and lg are the list sizes.
const ROWS_BY_SIZE = { xs: 1, sm: 1, md: 3, lg: 6 };

// Feed-age thresholds. MTA republishes about every 30s, so a couple of
// minutes is worth mentioning and five means something is wrong.
// Split columns are narrower but no shorter, so each holds more than half
// of what the single-column board shows.
const SPLIT_ROWS_PER_COLUMN = 5;

const AGE_SHOWN_AFTER_S = 120;
const STALE_AFTER_S = 300;

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Minutes -> accent slot by role. 1 = alert / "now", 2 = warning / act
// soon, 3 = positive / plenty of time.
function urgency(minutes) {
  if (!Number.isFinite(minutes) || minutes <= 1) return 1;
  if (minutes <= 5) return 2;
  return 3;
}

function fmtMinutes(m) {
  if (!Number.isFinite(m)) return "-";
  return m <= 0 ? "now" : String(m);
}

// Route chip. route_color / route_text_color are part of the data (the
// agency's own line colour), so they land as inline hex; feeds that omit
// them get the neutral sunken chip.
// Only a literal #RRGGBB is allowed through into a style attribute, so a
// hostile feed can't smuggle extra declarations into the chip.
function hex(value) {
  return /^#[0-9A-Fa-f]{6}$/.test(String(value ?? "")) ? String(value) : "";
}

function routeBadge(a) {
  const color = hex(a.color);
  const bg = color || "var(--surface-sunken)";
  const fg = color ? (hex(a.text_color) || "var(--on-accent)") : "var(--text-primary)";
  return `<span class="gt-route" style="background:${bg};color:${fg}">${escapeHtml(a.route || "?")}</span>`;
}

// Lateness against the timetable, when an RT feed gave us one. Late is the
// alert slot, early the positive one; on-time says nothing at all rather
// than adding a chip to every row.
function delayChip(a, compact = false) {
  const d = Number(a.delay);
  if (!Number.isFinite(d) || d === 0) return "";
  const slot = d > 0 ? 1 : 3;
  // Compact form for rows that also carry an origin chip: "+4" keeps the
  // fact without pushing the line past the row and clipping mid-word.
  const text = compact
    ? `${d > 0 ? "+" : "−"}${Math.abs(d)}`
    : (d > 0 ? `${d} min late` : `${-d} min early`);
  return `<span class="gt-delay" style="color:var(--accent-${slot})">${escapeHtml(text)}</span>`;
}

// How many stops out the vehicle is, from the realtime feed's own position
// report. Opt-in: it's the platform-sign metric, but it's noise on a board
// where everything is one or two stops away.
function stopsChip(a, show) {
  if (!show || !Number.isFinite(Number(a.stops_away))) return "";
  const n = Number(a.stops_away);
  const text = n === 0 ? "here" : n === 1 ? "1 stop" : `${n} stops`;
  return `<span class="gt-stops">${escapeHtml(text)}</span>`;
}

// Which station this train leaves from. Only meaningful — and only shown —
// when the board covers two stops.
function originChip(a, show) {
  if (!show || !a.stop_name) return "";
  return `<span class="gt-origin">${escapeHtml(a.stop_name)}</span>`;
}

// Track / platform, when the feed publishes one and the cell asked for it.
// NYCT ships this as a GTFS-RT extension; commuter rail feeds carry it too.
function trackChip(a, show) {
  if (!show || !a.track) return "";
  return `<span class="gt-track">Trk ${escapeHtml(a.track)}</span>`;
}

function liveDot(a) {
  return a.live
    ? '<i class="ph-bold ph-broadcast gt-live" title="Live"></i>'
    : "";
}

// The size class rides on ``.w`` itself: ``.w-body`` has to stay a direct
// flex child of the shell or it stops filling the cell's height.
function shell(body, size = "md") {
  return `
    <link rel="stylesheet" href="/static/style/spectra-widgets.css">
    <style>${STYLE}</style>
    <div class="w size-${escapeHtml(size)}" data-widget="gtfs">${body}</div>`;
}

const STYLE = `
  .gt-route {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 1.9em;
    padding: 0.1em 0.4em;
    font-weight: var(--fw-black);
    font-size: 0.95em;
    line-height: 1.3;
    border-radius: var(--pill-radius, var(--radius-0));
    flex: 0 0 auto;
  }
  .gt-live { color: var(--accent-4); font-size: 0.7em; }
  .gt-stops { font-weight: var(--fw-bold); color: var(--text-secondary); white-space: nowrap; }
  .gt-origin {
    /* Shrinks (and ellipses) before the delay chip does: a truncated
       station name still reads, half a "+4" doesn't. */
    flex: 0 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    padding: 0 0.35em;
    background: var(--surface-sunken);
    color: var(--text-secondary);
    font-weight: var(--fw-bold);
    white-space: nowrap;
    border-radius: var(--pill-radius, var(--radius-0));
  }
  .gt-stale { color: var(--accent-1); }
  /* Two-column split (lg, opt-in). Columns share the body's height; each
     carries its own destination heading so neither needs explaining. */
  .gt-split {
    flex: 1 1 auto;
    min-height: 0;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: var(--space-5);
  }
  .gt-col {
    min-width: 0;
    min-height: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .gt-col-head {
    font-size: var(--fs-label);
    font-weight: var(--fw-bold);
    letter-spacing: var(--ls-label);
    text-transform: var(--label-transform, uppercase);
    color: var(--text-muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .gt-col .gt-when .gt-delay ~ .gt-stops,
  .gt-col .gt-when .gt-delay ~ .gt-track { display: none; }
  .gt-col .gt-rows {
    flex: 1 1 auto;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }
  /* Alert strip (lg): the one place the full text fits. */
  .gt-alert {
    flex: 0 0 auto;
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-2) var(--space-3);
    background: var(--accent-1-soft);
    color: var(--accent-1);
    font-size: var(--fs-label);
    font-weight: var(--fw-bold);
    border-radius: var(--radius-0);
    overflow: hidden;
  }
  .gt-track {
    padding: 0 0.35em;
    background: var(--surface-sunken);
    font-weight: var(--fw-bold);
    border-radius: var(--pill-radius, var(--radius-0));
  }
  /* Service-alert text is agency prose and can run long; the title bar is
     one line, so it truncates rather than shoving the stop name out. */
  .w-title .w-title-meta {
    max-width: 55%;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .gt-delay {
    flex: 0 0 auto;
    white-space: nowrap;
    font-weight: var(--fw-bold);
    text-transform: var(--label-transform, uppercase);
    letter-spacing: var(--ls-label);
  }

  /* Hero (xs / sm). The icon and the number are the widget at these
     sizes, so both scale off cqmin rather than the type scale. */
  .gt-hero {
    flex: 1 1 auto;
    min-height: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--space-1);
    text-align: center;
  }
  .gt-hero-top {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--space-3);
    min-width: 0;
  }
  .gt-hero-icon { font-size: clamp(1.8em, 22cqmin, 5em); line-height: 1; }
  .gt-hero-min {
    font-size: clamp(2.4em, 34cqmin, 9em);
    font-weight: var(--fw-black);
    line-height: var(--lh-tight);
    letter-spacing: var(--ls-tight);
    font-variant-numeric: tabular-nums;
  }
  .gt-hero-unit {
    font-size: var(--fs-label);
    font-weight: var(--fw-bold);
    letter-spacing: var(--ls-label);
    text-transform: var(--label-transform, uppercase);
    color: var(--text-muted);
  }
  .gt-hero-line {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--space-2);
    min-width: 0;
    font-size: clamp(0.8em, 5cqmin, 1.3em);
  }
  .gt-hero-sign {
    font-weight: var(--fw-semi);
    color: var(--text-secondary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .gt-hero-delay { font-size: var(--fs-label); }
  .gt-hero-extra {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--space-2);
    font-size: var(--fs-caption);
  }
  /* Second and third vehicle as a quiet "then" strip under the hero at
     sm, the one bit of look-ahead that fits without crowding. */
  .gt-then {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--space-3);
    font-size: var(--fs-label);
    font-weight: var(--fw-bold);
    color: var(--text-muted);
    font-variant-numeric: tabular-nums;
  }

  /* Rows (md / lg). No drawn dividers, the minutes block carries a soft
     accent fill and that plus spacing is the whole hierarchy. */
  /* Rows split the body's height evenly (flex: 1 1 0) instead of stacking
     at their natural height. Natural height overflows the moment the row
     count goes up or the cell is short, and the last row gets clipped. */
  .gt-rows { overflow: hidden; }
  .gt-row {
    grid-template-columns: auto minmax(0, 1fr) auto;
    gap: var(--space-3);
    flex: 1 1 0;
    min-height: 0;
  }
  .gt-lead { min-width: 0; display: flex; flex-direction: column; gap: 0.1em; }
  .gt-sign {
    font-weight: var(--fw-semi);
    font-size: var(--fs-body);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .gt-when {
    display: flex;
    align-items: center;
    flex-wrap: nowrap;
    overflow: hidden;
    gap: 0.3em;
    font-size: var(--fs-caption);
    font-weight: var(--fw-bold);
    letter-spacing: var(--ls-label);
    color: var(--text-muted);
    font-variant-numeric: tabular-nums;
  }
  .gt-min {
    display: flex;
    align-items: baseline;
    gap: 0.2em;
    padding: 0.15em 0.5em;
    border-radius: var(--radius-0);
    font-variant-numeric: tabular-nums;
  }
  .gt-min .v { font-size: var(--fs-lead); font-weight: var(--fw-black); }
  .gt-min .u {
    font-size: var(--fs-caption);
    font-weight: var(--fw-bold);
    text-transform: var(--label-transform, uppercase);
  }
  /* Struck-through time, not a greyed row: the row still has to be legible
     from across the room, it just mustn't read as catchable. */
  .is-canceled .gt-sign, .is-canceled .gt-when { text-decoration: line-through; }
  .is-canceled .gt-sign { color: var(--text-secondary); }
  /* lg keeps the type heavy but the row chrome tight: six rows at the
     md paddings overflow an 800px-tall cell and clip the last one. */
  .size-lg .gt-row { padding: var(--space-2) var(--space-3); gap: var(--space-4); }
  .size-lg .gt-sign { font-size: var(--fs-lead); }
  .size-lg .gt-min .v { font-size: var(--fs-value); }
  .size-lg .gt-route { font-size: 1.15em; }
`;

function titleBar(data, size, hasAlertStrip = false) {
  // A feed note ("Delays northbound", "Live feed unavailable") outranks the
  // live badge: it's the thing the reader needs, and the badge is still
  // implied by the per-row live dots.
  // A feed that stopped updating still hands us confident-looking
  // predictions, so age is reported rather than assumed: past STALE_AFTER_S
  // the live badge gives way to how old the data actually is.
  const age = Number(data.feed_age_s);
  const stale = Number.isFinite(age) && age >= STALE_AFTER_S;
  const dot = data.live && !stale ? '<i class="ph-bold ph-broadcast gt-live"></i> ' : "";
  const aged = Number.isFinite(age) && age >= AGE_SHOWN_AFTER_S
    ? `${Math.round(age / 60)} min old`
    : "";
  // Countdowns are frozen at render time and an e-ink panel can hold a frame
  // for minutes, so the board says when it was drawn. The clock times in each
  // row stay true; the "N min" figures don't.
  const asOf = data.now ? `as of ${escapeHtml(data.now)}` : "";
  const meta = data.note && !hasAlertStrip
    ? `${dot}${escapeHtml(data.note)}`
    : (stale
      ? `<span class="gt-stale">${escapeHtml(aged || "not updating")}</span>`
      : (data.live
        ? `${dot}Live${aged ? ` · ${aged}` : ""}${asOf ? ` · ${asOf}` : ""}`
        : asOf));
  const live = meta ? `<span class="w-title-meta">${meta}</span>` : "";
  const icon = MODE_PH[(data.arrivals || [])[0]?.mode] || "ph-bus";
  return `
    <div class="w-title">
      <i class="ph-bold ${icon}" style="color:var(--accent-4)"></i>
      <h3>${escapeHtml(data.label || data.stop || "Departures")}</h3>
      ${size === "xs" ? "" : live}
    </div>`;
}

function heroBlock(arrivals, size, walk = 0, opts = {}) {
  // The hero is "the one you'll actually catch", so a cancelled trip never
  // claims it; it still shows in the "then" strip below.
  const next = arrivals.find((a) => !a.canceled) || arrivals[0];
  // With a walk time set, the number you actually need is when to leave, not
  // when the train arrives. The arrival time stays visible underneath.
  const shown = walk > 0 ? Math.max(0, Number(next.minutes) - walk) : Number(next.minutes);
  const slot = urgency(shown);
  const icon = MODE_PH[next.mode] || "ph-bus";
  const line = size === "xs"
    ? ""
    : `<div class="gt-hero-line">
         ${routeBadge(next)}
         <span class="gt-hero-sign">${escapeHtml(next.headsign || "")}</span>
         ${liveDot(next)}
       </div>
       ${walk > 0 ? `<div class="gt-hero-delay u-muted">arrives ${escapeHtml(next.time || "")}</div>` : ""}
       ${(() => {
         const extra = [
           originChip(next, opts.showOrigin),
           stopsChip(next, opts.showStops),
           trackChip(next, opts.showTrack),
         ].filter(Boolean).join(" ");
         return extra ? `<div class="gt-hero-extra">${extra}</div>` : "";
       })()}
       ${delayChip(next) ? `<div class="gt-hero-delay">${delayChip(next)}</div>` : ""}`;
  // At sm there's room for a one-line "then 12 · 24" look-ahead; at xs
  // the single number is the whole widget.
  const then = size === "xs"
    ? ""
    : (() => {
      const rest = arrivals
        .filter((a) => a !== next && Number.isFinite(a.minutes))
        .slice(0, 2);
      return rest.length
        ? `<div class="gt-then">then ${rest
            .map((a) => escapeHtml(a.canceled ? "✕" : fmtMinutes(a.minutes)))
            .join(" · ")} min</div>`
        : "";
    })();
  return `
    <div class="gt-hero">
      <div class="gt-hero-top">
        <i class="ph-bold ${icon} gt-hero-icon" style="color:var(--accent-${slot})"></i>
        <div>
          <div class="gt-hero-min" style="color:var(--accent-${slot})">${escapeHtml(fmtMinutes(shown))}</div>
          <div class="gt-hero-unit">${
            walk > 0
              ? (shown > 0 ? "min to leave" : "leave now")
              : (shown > 0 ? "min" : "arriving")
          }</div>
        </div>
      </div>
      ${line}
      ${then}
    </div>`;
}

function rowsBlock(arrivals, opts) {
  // No per-row mode glyph: every row at a given stop is the same vehicle
  // type, so it repeats without saying anything. The mode still reads from
  // the title bar's lead icon (and the hero at xs/sm).
  return arrivals.map((a) => {
    const slot = urgency(a.minutes);
    // A cancelled trip keeps its slot on the board — "the 14:05 isn't coming"
    // is the useful statement — but drops the countdown, which would read as
    // a train you could still catch.
    if (a.canceled) {
      return `
        <div class="list-row gt-row is-canceled">
          ${routeBadge(a)}
          <div class="gt-lead">
            <span class="gt-sign">${escapeHtml(a.headsign || a.route || "")}</span>
            <span class="gt-when">${escapeHtml(a.time || "")}</span>
          </div>
          <div class="gt-min" style="background:var(--accent-1-soft);color:var(--accent-1)">
            <span class="u">Cancelled</span>
          </div>
        </div>`;
    }
    return `
      <div class="list-row gt-row">
        ${routeBadge(a)}
        <div class="gt-lead">
          <span class="gt-sign">${escapeHtml(a.headsign || a.route || "")}</span>
          <span class="gt-when">${escapeHtml(a.time || "")} ${liveDot(a)} ${originChip(a, opts.showOrigin)} ${delayChip(a, opts.showOrigin)} ${stopsChip(a, opts.showStops)} ${trackChip(a, opts.showTrack)}</span>
        </div>
        <div class="gt-min" style="background:var(--accent-${slot}-soft);color:var(--accent-${slot})">
          <span class="v">${escapeHtml(fmtMinutes(a.minutes))}</span>
          ${a.minutes > 0 ? '<span class="u">min</span>' : ""}
        </div>
      </div>`;
  }).join("");
}

// Split view groups the board by direction_id and gives each group its own
// column, headed by where those trains go. On a big lobby cell that beats
// one column where half the rows are the wrong way for the reader.
function splitBlock(arrivals, opts, perColumn) {
  const groups = new Map();
  for (const a of arrivals) {
    const key = String(a.direction ?? "");
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(a);
  }
  // One direction in the data means there's nothing to split; fall back
  // rather than painting a lone column beside dead space.
  if (groups.size < 2) return "";
  const columns = [...groups.entries()].slice(0, 2).map(([, list]) => {
    // Head the column with the destinations its trains actually serve —
    // one name would misdescribe a column that mixes routes.
    const counts = new Map();
    for (const a of list) counts.set(a.headsign, (counts.get(a.headsign) || 0) + 1);
    const heading = [...counts.entries()]
      .sort((x, y) => y[1] - x[1])
      .slice(0, 2)
      .map(([sign]) => sign)
      .filter(Boolean)
      .join(" · ");
    return `
      <div class="gt-col">
        <div class="gt-col-head">${escapeHtml(heading ? `to ${heading}` : "")}</div>
        <div class="list-body gt-rows">${rowsBlock(list.slice(0, perColumn), opts)}</div>
      </div>`;
  });
  return `<div class="w-body gt-split">${columns.join("")}</div>`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const size = ctx?.cell?.size || "md";
  const cellOpts = ctx?.cell?.options || {};
  const all = Array.isArray(data.arrivals) ? data.arrivals : [];
  const opts = {
    showTrack: Boolean(cellOpts.show_track),
    showStops: Boolean(cellOpts.show_stops_away),
    // Two stops on the board makes "which stop" load-bearing; one makes it
    // noise, so it turns itself on from the data rather than an option.
    showOrigin: new Set(all.map((a) => a.stop_name).filter(Boolean)).size > 1,
  };
  const walk = Math.max(0, Number(cellOpts.walk_minutes) || 0);

  if (data.error) {
    shadow.innerHTML = shell(`
      <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Departures</h3></div>
      <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>`);
    return;
  }

  // The hero sizes still read arrivals 2-3 for the "then" strip, so only
  // the list sizes cut the array down.
  const arrivals = (size === "xs" || size === "sm") ? all : all.slice(0, ROWS_BY_SIZE[size] ?? 3);

  if (!arrivals.length) {
    shadow.innerHTML = shell(`
      ${size === "xs" ? "" : titleBar(data, size)}
      <div class="w-body stat-body">
        <div class="gt-hero">
          <i class="ph-bold ph-clock gt-hero-icon" style="color:var(--text-muted)"></i>
          <p class="u-muted">Nothing approaching</p>
        </div>
      </div>`, size);
    return;
  }

  // Fragments: the Panels canvas can place one part of the widget on its
  // own. Each paints self-contained, filling its box, with no title bar —
  // the canvas supplies its own framing.
  const frag = ctx?.fragment || "full";
  if (frag === "next") {
    shadow.innerHTML = shell(
      `<div class="w-body stat-body">${heroBlock(all, "sm", walk, opts)}</div>`,
      "sm",
    );
    return;
  }
  if (frag === "list") {
    shadow.innerHTML = shell(
      `<div class="w-body list-body gt-rows">${rowsBlock(all.slice(0, 4), opts)}</div>`,
      "md",
    );
    return;
  }

  // Split is opt-in and only earns its keep where there's width for two
  // columns; at md it would halve the row width for no gain.
  const wantSplit = cellOpts.layout === "split" && size === "lg";
  const split = wantSplit ? splitBlock(all, opts, SPLIT_ROWS_PER_COLUMN) : "";

  const body = (size === "xs" || size === "sm")
    ? `<div class="w-body stat-body">${heroBlock(arrivals, size, walk, opts)}</div>`
    : split || `<div class="w-body list-body gt-rows">${rowsBlock(arrivals, opts)}</div>`;

  // At lg an alert has room to be read in full rather than truncated into
  // the title bar, which is where it goes at every other size.
  const alert = size === "lg" && data.note
    ? `<div class="gt-alert"><i class="ph-bold ph-warning-circle"></i>${escapeHtml(data.note)}</div>`
    : "";

  shadow.innerHTML = shell(`
    ${size === "xs" ? "" : titleBar(data, size, Boolean(alert))}
    ${body}
    ${alert}`, size);
}
