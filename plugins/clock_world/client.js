// clock_world — Spectra list archetype. One row per configured city
// with its current time in the specified IANA timezone. Times use
// Intl.DateTimeFormat which respects the cell's chosen 12h/24h format.
// Each row carries a sun-position glyph (deep night / dawn / day /
// dusk) keyed to the city's local hour and a 24-hour day/night strip
// with a marker pip at the current local time — so a glance at the
// row tells you whether Tokyo is asleep or eating breakfast without
// having to parse the clock.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function parseCities(raw) {
  return String(raw || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [label, tz] = line.split("|").map((s) => s.trim());
      return { label: label || tz, tz: tz || label };
    });
}

function formatTime(now, tz, format) {
  try {
    return new Intl.DateTimeFormat([], {
      timeZone: tz,
      hour: "2-digit",
      minute: "2-digit",
      hour12: format === "12h",
    }).format(now);
  } catch {
    return "—";
  }
}

function dayOffset(now, tz) {
  // What day-of-month is it in that timezone vs the host? Returns
  // "tomorrow" / "yesterday" / "" so the row can mark a calendar
  // shift with an icon instead of pushing extra text into the time
  // column.
  try {
    const fmt = new Intl.DateTimeFormat([], { timeZone: tz, day: "numeric" });
    const remote = parseInt(fmt.format(now), 10);
    const here = now.getDate();
    if (Number.isNaN(remote)) return "";
    if (remote === here) return "";
    if (remote > here) return "tomorrow";
    return "yesterday";
  } catch {
    return "";
  }
}

// Return the local hour+minute fraction (0-23.99) at the given
// timezone, or -1 on failure. Used to pick a day vs night icon for
// the leading slot AND to position the marker on the day/night strip.
function localHourFraction(now, tz) {
  try {
    const fmt = new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
    const parts = fmt.formatToParts(now);
    const hourPart = parts.find((p) => p.type === "hour")?.value ?? "0";
    const minPart = parts.find((p) => p.type === "minute")?.value ?? "0";
    const h = parseInt(hourPart, 10);
    const m = parseInt(minPart, 10);
    if (!Number.isFinite(h) || !Number.isFinite(m)) return -1;
    return (h % 24) + (m / 60);
  } catch {
    return -1;
  }
}

// Map a local hour to one of five phases for the leading icon. Five
// bins read more nuanced than the previous binary day/night split:
// dawn and dusk get their own ph-sun-horizon icon so the row tells
// you "this city is just waking up" or "evening setting in" at a
// glance.
function phaseFor(hour) {
  if (hour < 0) return { icon: "ph-globe", accent: "var(--text-muted)" };
  if (hour < 5) return { icon: "ph-moon", accent: "var(--accent-5)" };
  if (hour < 7) return { icon: "ph-sun-horizon", accent: "var(--accent-2)" };
  if (hour < 17) return { icon: "ph-sun", accent: "var(--accent-2)" };
  if (hour < 20) return { icon: "ph-sun-horizon", accent: "var(--accent-1)" };
  return { icon: "ph-moon-stars", accent: "var(--accent-5)" };
}

export default function render(shadow, ctx) {
  const opts = ctx?.cell?.options || {};
  const cities = parseCities(opts.cities);
  const format = opts.format || "24h";
  const showStrip = opts.show_strip !== false;
  const now = new Date();

  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (cities.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="clock_world">
        <div class="w-title"><i class="ph-bold ph-globe"></i><h3>World</h3></div>
        <div class="w-body"><p class="u-muted">No cities configured.</p></div>
      </div>`;
    return;
  }

  // Shared 24-hour day/night gradient. Day window 6-18 (bright), with
  // dawn 5-7 and dusk 17-19 as soft accent-tinted transitions, night
  // outside that as a slate-tinted band. Built once as percent-of-day
  // stops; each row's strip uses this same gradient + a per-row
  // marker pip positioned at the city's current local-hour fraction.
  const stripGradient = `linear-gradient(to right,
    color-mix(in oklab, var(--accent-5) 22%, var(--surface)) 0%,
    color-mix(in oklab, var(--accent-5) 22%, var(--surface)) 20%,
    color-mix(in oklab, var(--accent-2) 18%, var(--surface)) 25%,
    color-mix(in oklab, var(--surface) 90%, white 10%) 33%,
    color-mix(in oklab, var(--surface) 90%, white 10%) 70%,
    color-mix(in oklab, var(--accent-1) 18%, var(--surface)) 75%,
    color-mix(in oklab, var(--accent-5) 22%, var(--surface)) 83%,
    color-mix(in oklab, var(--accent-5) 22%, var(--surface)) 100%)`;

  const rows = cities.map((c, i) => {
    const t = formatTime(now, c.tz, format);
    const off = dayOffset(now, c.tz);
    const h = localHourFraction(now, c.tz);
    const phase = phaseFor(h);
    // Day-shift cue. Lives in its own slot to the LEFT of the time
    // so the time itself stays in a single right-aligned column —
    // adding "yesterday" inline used to push the time leftward and
    // break the column alignment across rows. ph-arrow-down for a
    // calendar day BEHIND the host; ph-arrow-up for AHEAD.
    let shiftIcon = "";
    if (off === "yesterday") {
      shiftIcon = `<i class="ph-bold ph-arrow-down" title="yesterday" style="font-size:.85em;color:var(--text-muted);margin-right:.3em;vertical-align:-.05em"></i>`;
    } else if (off === "tomorrow") {
      shiftIcon = `<i class="ph-bold ph-arrow-up" title="tomorrow" style="font-size:.85em;color:var(--text-muted);margin-right:.3em;vertical-align:-.05em"></i>`;
    }

    // Day/night strip. Marker pip rides the gradient at the city's
    // current local hour (h / 24) — for a city in deep night the pip
    // sits in the slate band, midday it sits in the bright band, etc.
    // We render the strip BELOW the row's title+time line so it
    // reads as a tertiary detail and the time column doesn't shift.
    let strip = "";
    if (showStrip && h >= 0) {
      const pct = ((h / 24) * 100).toFixed(2);
      strip = `
        <div class="day-strip">
          <div class="day-strip-bar"></div>
          <div class="day-strip-pip" style="left:${pct}%">
            <span class="day-strip-pip-flag"></span>
          </div>
        </div>`;
    }

    return `
      <div class="city-row ${i % 2 ? "is-zebra" : ""}">
        <div class="city-row-head">
          <div class="list-lead">
            <i class="ph-bold ${phase.icon}" style="color:${phase.accent}"></i>
            <span class="list-title">${escapeHtml(c.label)}</span>
          </div>
          <span class="list-meta" style="font-variant-numeric:tabular-nums">${shiftIcon}${escapeHtml(t)}</span>
        </div>
        ${strip}
      </div>`;
  }).join("");

  const layout = `
    .city-row {
      display: flex;
      flex-direction: column;
      gap: var(--space-1);
      padding: var(--space-2) var(--space-3);
      border-radius: var(--radius-1);
    }
    .city-row.is-zebra {
      background: color-mix(in oklab, var(--text-primary) 3%, transparent);
    }
    .city-row-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--space-3);
    }
    /* Strip sits in a sunken framed track so the gradient reads as a
       deliberate channel rather than a free-floating ribbon. Track is
       a touch darker than the surface with a hairline border; the
       gradient sits 1px inset inside that frame. */
    .day-strip {
      position: relative;
      height: 10px;
      border-radius: 5px;
      background: color-mix(in oklab, var(--text-primary) 6%, var(--surface));
      border: 1px solid color-mix(in oklab, var(--text-primary) 14%, transparent);
      margin-top: 4px;
      overflow: visible;
    }
    .day-strip-bar {
      position: absolute;
      inset: 1px;
      border-radius: 4px;
      background: ${stripGradient};
    }
    /* Now-indicator. A chunky accent-1 (terracotta) bar that bleeds
       above and below the track with a strong surface halo, plus a
       triangle pennant pointing down at the bar from above. Three
       reinforcement cues — colour pop, height bleed, downward arrow
       — so the marker reads as the row's focal element instead of
       a hairline that disappears against the gradient. */
    .day-strip-pip {
      position: absolute;
      top: -5px;
      bottom: -5px;
      width: 4px;
      border-radius: 2px;
      background: var(--accent-1);
      box-shadow:
        0 0 0 2px var(--surface),
        0 0 0 3px color-mix(in oklab, var(--accent-1) 60%, transparent);
      transform: translateX(-50%);
      z-index: 2;
    }
    .day-strip-pip-flag {
      position: absolute;
      top: -7px;
      left: 50%;
      transform: translateX(-50%);
      width: 0;
      height: 0;
      border-left: 5px solid transparent;
      border-right: 5px solid transparent;
      border-top: 6px solid var(--accent-1);
      filter: drop-shadow(0 0 1px var(--surface));
    }
    /* xs / sm cells: drop the strip; the row stays compact. */
    @container (max-width: 280px) {
      .day-strip { display: none; }
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="clock_world">
      <div class="w-title">
        <i class="ph-bold ph-globe" style="color:var(--accent-5)"></i>
        <h3>World</h3>
        <span class="w-title-meta">${cities.length} TZ</span>
      </div>
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
