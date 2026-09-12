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
// Floors, not caps: the list grows past these when the cell has the height
// for more rows (see rowsThatFit), so a cell zoomed out to 0.25 fills with
// departures instead of stretching six rows across the gaps.
const ROWS_BY_SIZE = { xs: 1, sm: 1, md: 3, lg: 6 };

// Feed-age thresholds. MTA republishes about every 30s, so a couple of
// minutes is worth mentioning and five means something is wrong.
// Split columns are narrower but no shorter, so each holds more than half
// of what the single-column board shows.
const SPLIT_ROWS_PER_COLUMN = 5;

// Natural row height in body ems, from the row's own padding + the sign,
// when and minutes lines at --lh-body. Measuring instead would need the
// shell's stylesheet applied, which a shadow <link> doesn't guarantee
// synchronously, so the arithmetic mirrors STYLE below.
const ROW_EM = { md: 3.4, lg: 3.7 };
const ROW_GAP_EM = 0.25;

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

function fmtMinutes(m, t) {
  if (!Number.isFinite(m)) return "-";
  return m <= 0 ? t("now", "now") : String(m);
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
function delayChip(a, t, compact = false) {
  const d = Number(a.delay);
  if (!Number.isFinite(d) || d === 0) return "";
  const slot = d > 0 ? 1 : 3;
  // Compact form for rows that also carry an origin chip: "+4" keeps the
  // fact without pushing the line past the row and clipping mid-word.
  const text = compact
    ? `${d > 0 ? "+" : "−"}${Math.abs(d)}`
    : (d > 0 ? `${d} ${t("min_late", "min late")}` : `${-d} ${t("min_early", "min early")}`);
  return `<span class="gt-delay" style="color:var(--accent-${slot})">${escapeHtml(text)}</span>`;
}

// How far out the vehicle is, from the realtime feed's own position
// report: stops away when the feed counts them, straight-line distance when
// it only publishes coordinates. Opt-in: it's the platform-sign metric, but
// it's noise on a board where everything is one or two stops away.
function stopsChip(a, show, units, t) {
  if (!show) return "";
  let text = "";
  if (Number.isFinite(Number(a.stops_away))) {
    const n = Number(a.stops_away);
    text = n === 0
      ? t("here", "here")
      : `${n} ${n === 1 ? t("stop", "stop") : t("stops", "stops")}`;
  } else if (Number.isFinite(Number(a.distance_m))) {
    text = fmtDistance(Number(a.distance_m), units);
  }
  return text ? `<span class="gt-stops">${escapeHtml(text)}</span>` : "";
}

function fmtDistance(metres, units) {
  if (units === "imperial") {
    const miles = metres / 1609.344;
    if (miles < 0.1) return `${Math.round(metres / 0.3048 / 10) * 10} ft`;
    return `${miles < 10 ? miles.toFixed(1) : Math.round(miles)} mi`;
  }
  if (metres < 1000) return `${Math.round(metres / 10) * 10} m`;
  const km = metres / 1000;
  return `${km < 10 ? km.toFixed(1) : Math.round(km)} km`;
}

// Which station this train leaves from. Only meaningful — and only shown —
// when the board covers two stops.
function originChip(a, show) {
  if (!show || !a.stop_name) return "";
  return `<span class="gt-origin">${escapeHtml(a.stop_name)}</span>`;
}

// Track / platform, when the feed publishes one and the cell asked for it.
// NYCT ships this as a GTFS-RT extension; commuter rail feeds carry it too.
function trackChip(a, show, t) {
  if (!show || !a.track) return "";
  return `<span class="gt-track">${t("track_abbr", "Trk")} ${escapeHtml(a.track)}</span>`;
}

function liveDot(a, t) {
  return a.live
    ? `<i class="ph-bold ph-broadcast gt-live" title="${t("live", "Live")}"></i>`
    : "";
}

// server.py's own feed note is a fixed English sentence, so the sentence is
// the stable id; a service alert is agency prose and passes through as data.
function noteText(note, t) {
  return note === "Live feed unavailable"
    ? t("live_feed_unavailable", "Live feed unavailable")
    : String(note ?? "");
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

function titleBar(data, size, hasAlertStrip, t) {
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
    ? `${Math.round(age / 60)} ${t("min_old", "min old")}`
    : "";
  // Countdowns are frozen at render time and an e-ink panel can hold a frame
  // for minutes, so the board says when it was drawn. The clock times in each
  // row stay true; the "N min" figures don't.
  const asOf = data.now ? `${t("as_of", "as of")} ${escapeHtml(data.now)}` : "";
  const meta = data.note && !hasAlertStrip
    ? `${dot}${escapeHtml(noteText(data.note, t))}`
    : (stale
      ? `<span class="gt-stale">${escapeHtml(aged || t("not_updating", "not updating"))}</span>`
      : (data.live
        ? `${dot}${t("live", "Live")}${aged ? ` · ${aged}` : ""}${asOf ? ` · ${asOf}` : ""}`
        : asOf));
  const live = meta ? `<span class="w-title-meta">${meta}</span>` : "";
  const icon = MODE_PH[(data.arrivals || [])[0]?.mode] || "ph-bus";
  return `
    <div class="w-title">
      <i class="ph-bold ${icon}" style="color:var(--accent-4)"></i>
      <h3>${escapeHtml(data.label || data.stop || t("departures", "Departures"))}</h3>
      ${size === "xs" ? "" : live}
    </div>`;
}

function heroBlock(arrivals, size, walk = 0, opts = {}) {
  const t = opts.t || ((key, fallback) => fallback ?? key);
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
         ${liveDot(next, t)}
       </div>
       ${walk > 0 ? `<div class="gt-hero-delay u-muted">${t("arrives", "arrives")} ${escapeHtml(next.time || "")}</div>` : ""}
       ${(() => {
         const extra = [
           originChip(next, opts.showOrigin),
           stopsChip(next, opts.showStops, opts.distanceUnits, t),
           trackChip(next, opts.showTrack, t),
         ].filter(Boolean).join(" ");
         return extra ? `<div class="gt-hero-extra">${extra}</div>` : "";
       })()}
       ${delayChip(next, t) ? `<div class="gt-hero-delay">${delayChip(next, t)}</div>` : ""}`;
  // At sm there's room for a one-line "then 12 · 24" look-ahead; at xs
  // the single number is the whole widget.
  const then = size === "xs"
    ? ""
    : (() => {
      const rest = arrivals
        .filter((a) => a !== next && Number.isFinite(a.minutes))
        .slice(0, 2);
      return rest.length
        ? `<div class="gt-then">${t("then", "then")} ${rest
            .map((a) => escapeHtml(a.canceled ? "✕" : fmtMinutes(a.minutes, t)))
            .join(" · ")} ${t("min", "min")}</div>`
        : "";
    })();
  return `
    <div class="gt-hero">
      <div class="gt-hero-top">
        <i class="ph-bold ${icon} gt-hero-icon" style="color:var(--accent-${slot})"></i>
        <div>
          <div class="gt-hero-min" style="color:var(--accent-${slot})">${escapeHtml(fmtMinutes(shown, t))}</div>
          <div class="gt-hero-unit">${
            walk > 0
              ? (shown > 0 ? t("min_to_leave", "min to leave") : t("leave_now", "leave now"))
              : (shown > 0 ? t("min", "min") : t("arriving", "arriving"))
          }</div>
        </div>
      </div>
      ${line}
      ${then}
    </div>`;
}

// How many list rows the cell can hold at its natural row height. Mirrors
// the shell metrics in spectra-widgets.css: --w-font-base clamp(14px,
// 7cqmin, 28px), --pad clamp(0.9em, 9cqmin, 1.4em), a --space-4 gap between
// shell children, and the title bar locked at the zoom=1 font size.
function rowsThatFit(shadow, ctx, size, { title = true, alert = false, colHead = false } = {}) {
  const w = Number(ctx?.cell?.w) || 0;
  const h = Number(ctx?.cell?.h) || 0;
  if (!w || !h) return 0;
  const clamp = (lo, v, hi) => Math.min(hi, Math.max(lo, v));
  const cqmin = Math.min(w, h);
  const base = clamp(14, 0.07 * cqmin, 28);
  const pad = clamp(0.9 * base, 0.09 * cqmin, 1.4 * base);
  let zoom = 1;
  try {
    const raw = getComputedStyle(shadow.host).getPropertyValue("--c-zoom");
    zoom = Number.parseFloat(raw) || 1;
  } catch { /* no host, no zoom */ }
  // Title font is clamp(14px, 7cqmin * zoom, 28px) / zoom; 1.5em min-height
  // plus up to a --space-2 rule pad and a 5px rule underneath.
  const titleFont = clamp(14, 0.07 * cqmin * zoom, 28) / zoom;
  const titleH = title ? 2 * titleFont + 5 + base : 0;
  // A two-line alert strip at --fs-label: padding + text + shell gap.
  const alertH = alert ? 3.2 * base + base : 0;
  const colHeadH = colHead ? 1.6 * base : 0;
  const available = h - 2 * pad - titleH - alertH - colHeadH;
  const rowH = (ROW_EM[size] ?? ROW_EM.md) * base;
  const gap = ROW_GAP_EM * base;
  return Math.max(0, Math.floor((available + gap) / (rowH + gap)));
}

function rowsBlock(arrivals, opts) {
  const t = opts.t || ((key, fallback) => fallback ?? key);
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
            <span class="u">${t("cancelled", "Cancelled")}</span>
          </div>
        </div>`;
    }
    return `
      <div class="list-row gt-row">
        ${routeBadge(a)}
        <div class="gt-lead">
          <span class="gt-sign">${escapeHtml(a.headsign || a.route || "")}</span>
          <span class="gt-when">${escapeHtml(a.time || "")} ${liveDot(a, t)} ${originChip(a, opts.showOrigin)} ${delayChip(a, t, opts.showOrigin)} ${stopsChip(a, opts.showStops, opts.distanceUnits, t)} ${trackChip(a, opts.showTrack, t)}</span>
        </div>
        <div class="gt-min" style="background:var(--accent-${slot}-soft);color:var(--accent-${slot})">
          <span class="v">${escapeHtml(fmtMinutes(a.minutes, t))}</span>
          ${a.minutes > 0 ? `<span class="u">${t("min", "min")}</span>` : ""}
        </div>
      </div>`;
  }).join("");
}

// Split view groups the board by direction_id and gives each group its own
// column, headed by where those trains go. On a big lobby cell that beats
// one column where half the rows are the wrong way for the reader.
function splitBlock(arrivals, opts, perColumn) {
  const t = opts.t || ((key, fallback) => fallback ?? key);
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
        <div class="gt-col-head">${escapeHtml(heading ? `${t("to", "to")} ${heading}` : "")}</div>
        <div class="list-body gt-rows">${rowsBlock(list.slice(0, perColumn), opts)}</div>
      </div>`;
  });
  return `<div class="w-body gt-split">${columns.join("")}</div>`;
}

export default function render(shadow, ctx) {
  const t = ctx?.t || ((key, fallback) => fallback ?? key);
  const data = ctx?.data ?? {};
  const size = ctx?.cell?.size || "md";
  const cellOpts = ctx?.cell?.options || {};
  const all = Array.isArray(data.arrivals) ? data.arrivals : [];
  const opts = {
    t,
    showTrack: Boolean(cellOpts.show_track),
    showStops: Boolean(cellOpts.show_stops_away),
    distanceUnits: cellOpts.distance_units === "imperial" ? "imperial" : "metric",
    // Two stops on the board makes "which stop" load-bearing; one makes it
    // noise, so it turns itself on from the data rather than an option.
    showOrigin: new Set(all.map((a) => a.stop_name).filter(Boolean)).size > 1,
  };
  const walk = Math.max(0, Number(cellOpts.walk_minutes) || 0);

  if (data.error) {
    shadow.innerHTML = shell(`
      <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>${t("departures", "Departures")}</h3></div>
      <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>`);
    return;
  }

  // The hero sizes still read arrivals 2-3 for the "then" strip, so only
  // the list sizes cut the array down: to the size's floor, or to as many
  // rows as the cell's height actually holds when that's more.
  const listRows = Math.max(
    ROWS_BY_SIZE[size] ?? 3,
    rowsThatFit(shadow, ctx, size, { alert: size === "lg" && Boolean(data.note) }),
  );
  const arrivals = (size === "xs" || size === "sm") ? all : all.slice(0, listRows);

  if (!arrivals.length) {
    shadow.innerHTML = shell(`
      ${size === "xs" ? "" : titleBar(data, size, false, t)}
      <div class="w-body stat-body">
        <div class="gt-hero">
          <i class="ph-bold ph-clock gt-hero-icon" style="color:var(--text-muted)"></i>
          <p class="u-muted">${t("nothing_approaching", "Nothing approaching")}</p>
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
      `<div class="w-body list-body gt-rows">${rowsBlock(all.slice(0, Math.max(4, rowsThatFit(shadow, ctx, "md", { title: false }))), opts)}</div>`,
      "md",
    );
    return;
  }

  // Split is opt-in and only earns its keep where there's width for two
  // columns; at md it would halve the row width for no gain.
  const wantSplit = cellOpts.layout === "split" && size === "lg";
  const perColumn = Math.max(
    SPLIT_ROWS_PER_COLUMN,
    rowsThatFit(shadow, ctx, size, { alert: Boolean(data.note), colHead: true }),
  );
  const split = wantSplit ? splitBlock(all, opts, perColumn) : "";

  const body = (size === "xs" || size === "sm")
    ? `<div class="w-body stat-body">${heroBlock(arrivals, size, walk, opts)}</div>`
    : split || `<div class="w-body list-body gt-rows">${rowsBlock(arrivals, opts)}</div>`;

  // At lg an alert has room to be read in full rather than truncated into
  // the title bar, which is where it goes at every other size.
  const alert = size === "lg" && data.note
    ? `<div class="gt-alert"><i class="ph-bold ph-warning-circle"></i>${escapeHtml(noteText(data.note, t))}</div>`
    : "";

  shadow.innerHTML = shell(`
    ${size === "xs" ? "" : titleBar(data, size, Boolean(alert), t)}
    ${body}
    ${alert}`, size);
}
